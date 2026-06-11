"""
Sentient Trader backend API.

This service runs inside Oracle Cloud near private Valkey/Redis. The React
frontend calls this API over HTTPS with the user's Supabase access token.
All database, Alpaca, LLM-provider, Redis, and admin operations live here.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import os
import re
import sys
import time
import html
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
from dotenv import load_dotenv, find_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from supabase import create_client
from supabase.client import ClientOptions

sys.path.append(str(Path(__file__).resolve().parents[1]))

from redis_client import create_redis_client
from shared.logging_setup import request_id_var, setup_logging
from shared.worker_health import health_key, read_worker_state, worker_name

# Try to find a root/parent .env file first for local development.
# override=False: real environment variables (set by the container platform)
# always win over .env files, so a stray .env baked into an image can never
# silently replace production credentials.
dotenv_path = find_dotenv()
if dotenv_path:
    load_dotenv(dotenv_path, override=False)
else:
    load_dotenv(override=False)

# LOG_FORMAT=json emits structured lines stamped with the per-request
# request_id contextvar (set by request_id_middleware below).
setup_logging("api")
log = logging.getLogger("backend.api")

ServiceStatus = Literal["ok", "stale", "error", "unknown"]
UserTier = Literal["anonymous", "social", "super"]

ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
GROQ_MODELS_URL = os.environ.get(
    "GROQ_MODELS_URL", "https://api.groq.com/openai/v1/models"
)
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
STREAM_KEY = os.environ.get("REDIS_STREAM_KEY", "market-news")
STREAM_MAX_LEN = int(os.environ.get("REDIS_STREAM_MAX_LEN", "1000"))
TICKER_META_HASH_KEY = os.environ.get("TICKER_META_HASH_KEY", "sentient:ticker:meta")
TICKER_INDEX_TTL_SECONDS = int(os.environ.get("TICKER_INDEX_TTL_SECONDS", "600"))
PAGE_SIZE = 20
MAX_ORDER_IDS = 50
MAX_PROMPT_LENGTH = 5000

# /metrics (Prometheus). Optional bearer token: if set, scrapers must present it.
# Mirrors the agent's llm_budget key layout so we can expose budget usage.
METRICS_AUTH_TOKEN = os.environ.get("METRICS_AUTH_TOKEN", "").strip()
LLM_BUDGET_KEY_PREFIX = os.environ.get("LLM_BUDGET_KEY_PREFIX", "sentient:llm:budget")
WORKER_STALE_AFTER_SECONDS = int(os.environ.get("WORKER_STALE_AFTER_SECONDS", "180"))
# Mirror the agent's queue key defaults so /metrics can expose queue depths.
AGENT_DLQ_STREAM_KEY = os.environ.get("AGENT_DLQ_STREAM_KEY", f"{STREAM_KEY}:agent-dlq")
AGENT_RETRY_ZSET_KEY = os.environ.get("AGENT_RETRY_ZSET_KEY", f"{STREAM_KEY}:agent-retry")

TRADE_SUMMARY_SELECT = (
    "id, created_at, ticker, headline, article_url, sentiment_score, "
    "confidence_score, calibrated_confidence, confidence_cap, trade_action, "
    "pm_recommendation, risk_should_trade, executed_action, order_id, "
    "client_order_id, order_status, execution_error, gate_reason, decision_path, "
    "processing_started_at, processing_finished_at, quantity, is_simulated"
)
LEGACY_TRADE_DETAIL_SELECT = (
    f"{TRADE_SUMMARY_SELECT}, reasoning, article_source, article_id, decision_trace"
)
TRACE_DETAIL_SELECT = "decision_trace, reasoning, article_source, article_id"
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I
)
TICKER_REGEX = re.compile(r"^[A-Z]{1,6}$")
ALLOWED_ORDER_STATUSES = {"open", "closed", "all"}
# Per Alpaca portfolio-history docs: timeframe must be 1D for periods > 30 days,
# and intraday_reporting only applies to intraday timeframes (1Min/5Min/15Min/1H).
RANGE_CONFIG = {
    "D": {"period": "1D", "timeframe": "5Min", "intraday": True},
    "W": {"period": "1W", "timeframe": "1H", "intraday": True},
    "M": {"period": "1M", "timeframe": "1D", "intraday": False},
    "3M": {"period": "3M", "timeframe": "1D", "intraday": False},
    "6M": {"period": "6M", "timeframe": "1D", "intraday": False},
    "Y": {"period": "1A", "timeframe": "1D", "intraday": False},
    "5Y": {"period": "5A", "timeframe": "1D", "intraday": False},
}

INJECTION_MARKERS = [
    "ignore previous",
    "ignore all previous",
    "ignore above",
    "disregard previous",
    "disregard all",
    "system prompt",
    "you are now",
    "act as",
    "new instructions",
    "override instructions",
]

RATE_LIMITS: dict[UserTier, tuple[int, int, str]] = {
    "anonymous": (1, 24 * 60 * 60 * 1000, "ratelimit:simulate:anon"),
    "social": (2, 24 * 60 * 60 * 1000, "ratelimit:simulate:social"),
    "super": (60, 60 * 1000, "ratelimit:simulate:super"),
}

FIXED_WINDOW_SCRIPT = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
  redis.call("PEXPIRE", KEYS[1], ARGV[1])
end
return current
"""

EXCLUDED_MODEL_TERMS = [
    "whisper",
    "prompt-guard",
    "safeguard",
    "guard",
    "orpheus",
    "tts",
    "speech",
    "audio",
    "compound",
]
FAMILY_HINTS = [
    ("gpt-oss", 260),
    ("qwen", 240),
    ("llama", 180),
    ("mixtral", 150),
    ("gemma", 140),
    ("deepseek", 140),
]

_redis_client = None
_supabase_client = None
_ticker_index: list[dict[str, str]] | None = None
_ticker_index_loaded_at = 0.0


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def env_limit_list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [part.strip() for part in re.split(r"[,;]", raw) if part.strip()]


def rate_limit_storage_uri() -> str:
    explicit = os.environ.get("API_RATE_LIMIT_STORAGE_URI")
    if explicit:
        return explicit
    if not os.environ.get("REDIS_HOST"):
        return "memory://"

    username = quote(os.environ.get("REDIS_USERNAME") or "")
    password = quote(os.environ.get("REDIS_PASSWORD") or "")
    auth = ""
    if username and password:
        auth = f"{username}:{password}@"
    elif password:
        auth = f":{password}@"
    host = os.environ.get("REDIS_HOST", "127.0.0.1")
    port = os.environ.get("REDIS_PORT", "6379")
    db = os.environ.get("REDIS_DB", "0")
    return f"redis://{auth}{host}:{port}/{db}"


def normalized_client_ip(value: str) -> str:
    ip = value.strip().lower()
    if ip in {"127.0.0.1", "::1", "localhost"} or ip.startswith("::ffff:127."):
        return "localhost"
    return ip or "unknown"


# How many trusted reverse proxies sit in front of this app (e.g. Coolify's
# proxy, Netlify). Each appends the peer it saw to X-Forwarded-For, so the
# leftmost entries are attacker-controlled. We trust only the Nth-from-the-right
# hop — the address the outermost trusted proxy actually observed — which makes
# the anonymous-IP rate limit unspoofable. Set to 0 to ignore XFF entirely.
TRUSTED_PROXY_COUNT = max(int(os.environ.get("API_TRUSTED_PROXY_COUNT", "1")), 0)


def client_ip(request: Request) -> str:
    socket_ip = request.client.host if request.client else "unknown"
    if TRUSTED_PROXY_COUNT <= 0:
        return normalized_client_ip(socket_ip)

    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        chain = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
        if chain:
            # The real client is the entry appended by the outermost trusted
            # proxy. Anything further left was supplied by the client and is
            # ignored. If the chain is shorter than expected, fall back to the
            # leftmost (best available) rather than trusting a spoofed value.
            index = max(len(chain) - TRUSTED_PROXY_COUNT, 0)
            return normalized_client_ip(chain[index])
    return normalized_client_ip(socket_ip)


def rate_limit_salt() -> bytes:
    """Secret used to derive per-user/per-IP rate-limit keys.

    Required: a predictable salt makes the keys guessable, which would let a
    caller probe or grief another user's bucket. We never fall back to a public
    constant — ``rate_limit_salt()`` fails fast at startup if unset.
    """
    salt = os.environ.get("API_RATE_LIMIT_KEY_SALT") or os.environ.get(
        "SUPABASE_JWT_SECRET"
    )
    if not salt:
        raise RuntimeError(
            "API_RATE_LIMIT_KEY_SALT (or SUPABASE_JWT_SECRET) must be set to a "
            "long random secret. Refusing to use a predictable rate-limit salt."
        )
    return salt.encode()


def rate_limit_digest(value: str) -> str:
    return hmac.new(rate_limit_salt(), value.encode(), "sha256").hexdigest()[:32]


def _b64url_decode(segment: str) -> bytes:
    segment += "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment.encode())


def jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        parsed = json.loads(_b64url_decode(parts[1]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# Supabase legacy JWT secret (HS256). Used ONLY to verify rate-limit bucket
# identity cheaply, never for authorization — sensitive routes always
# re-validate the token against Supabase Auth (get_user_from_token).
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "").strip()


def jwt_payload_for_rate_limit(token: str) -> dict[str, Any]:
    """Payload for rate-limit bucketing, signature-verified when possible.

    Without verification, anyone could mint a JWT with another user's `sub`
    and grief that user's global rate-limit bucket. When SUPABASE_JWT_SECRET
    is configured we verify the HS256 signature and `exp` locally (no network
    hop); a forged or expired token falls back to the caller's IP bucket.
    If the secret is not configured (e.g. asymmetric-key Supabase projects),
    we keep the unverified parse — the bucket is then only a coarse limiter,
    which every protected route compensates for by re-validating the session.
    """
    if not SUPABASE_JWT_SECRET:
        return jwt_payload(token)

    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        header = json.loads(_b64url_decode(parts[0]))
        signature = _b64url_decode(parts[2])
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        return {}
    expected = hmac.new(
        SUPABASE_JWT_SECRET.encode(), f"{parts[0]}.{parts[1]}".encode(), "sha256"
    ).digest()
    if not hmac.compare_digest(expected, signature):
        return {}
    payload = jwt_payload(token)
    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time():
        return {}
    return payload


def is_anonymous_jwt(payload: dict[str, Any]) -> bool:
    if payload.get("is_anonymous") is True:
        return True
    app_metadata = payload.get("app_metadata")
    if not isinstance(app_metadata, dict):
        return False
    provider = app_metadata.get("provider")
    providers = app_metadata.get("providers")
    return provider == "anonymous" or (
        isinstance(providers, list) and "anonymous" in providers
    )


def rate_limit_key(request: Request) -> str:
    token = auth_token(request.headers.get("authorization"))
    if token:
        payload = jwt_payload_for_rate_limit(token)
        subject = str(payload.get("sub") or "").strip()
        if subject and not is_anonymous_jwt(payload):
            return f"user:{rate_limit_digest(subject)}"
        if subject and is_anonymous_jwt(payload):
            return f"anon-ip:{rate_limit_digest(client_ip(request))}"
    return f"ip:{rate_limit_digest(client_ip(request))}"


def retry_after_seconds(exc: RateLimitExceeded) -> int | None:
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is None:
        return None
    try:
        parsed = int(float(retry_after))
    except (TypeError, ValueError):
        return None
    return max(parsed, 0)


def rate_limit_window(request: Request) -> dict[str, int] | None:
    view_rate_limit = getattr(request.state, "view_rate_limit", None)
    if view_rate_limit is None:
        return None
    try:
        reset_at, remaining = request.app.state.limiter.limiter.get_window_stats(
            view_rate_limit[0], *view_rate_limit[1]
        )
        retry_after = max(int(1 + reset_at - time.time()), 0)
        return {
            "retryAfterSeconds": retry_after,
            "remaining": int(remaining),
            "resetAt": int(reset_at),
        }
    except Exception:
        log.debug("Could not read SlowAPI window stats", exc_info=True)
        return None


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    window = rate_limit_window(request)
    retry_after = window["retryAfterSeconds"] if window else retry_after_seconds(exc)
    detail = str(getattr(exc, "detail", "") or "global limit")
    payload: dict[str, Any] = {
        "error": {
            "code": "RATE_LIMITED",
            "message": "Too many requests. Please slow down and retry shortly.",
            "status": 429,
            "limit": detail,
            "retryAfterSeconds": retry_after,
            "rateLimit": window,
            "requestId": getattr(request.state, "request_id", None)
            or request.headers.get("x-request-id"),
        }
    }
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    response = JSONResponse(status_code=429, content=payload, headers=headers)

    try:
        view_rate_limit = getattr(request.state, "view_rate_limit", None)
        if view_rate_limit is not None:
            response = request.app.state.limiter._inject_headers(response, view_rate_limit)
    except Exception:
        log.debug("Could not inject SlowAPI rate-limit headers", exc_info=True)

    log.warning(
        "Rate limit exceeded path=%s method=%s client=%s limit=%s",
        request.url.path,
        request.method,
        rate_limit_key(request),
        detail,
    )
    return response


app = FastAPI(title="Sentient Trader Backend API")

api_rate_limit_enabled = env_bool("API_RATE_LIMIT_ENABLED", True)
if api_rate_limit_enabled:
    # Fail fast: rate-limit keys are HMAC'd with this salt. A missing salt used
    # to silently fall back to a public constant, making keys predictable.
    rate_limit_salt()
if not SUPABASE_JWT_SECRET:
    log.warning(
        "SUPABASE_JWT_SECRET is not set — rate-limit buckets fall back to "
        "unverified JWT claims (coarse limiter only; route auth is unaffected)."
    )

# Fail fast: /metrics exposes worker health and LLM budget internals. Either
# set a scrape token or explicitly opt into public exposure — never silently.
METRICS_PUBLIC = env_bool("METRICS_PUBLIC", False)
if not METRICS_AUTH_TOKEN and not METRICS_PUBLIC:
    raise RuntimeError(
        "METRICS_AUTH_TOKEN must be set to a scrape token, or METRICS_PUBLIC=true "
        "to explicitly expose /metrics without authentication."
    )

# When true, /orders and /portfolio (live broker account data) require the
# super-user allowlist instead of any signed-in account. Default keeps the
# public-demo behavior: any authenticated visitor can view the paper account.
ACCOUNT_ENDPOINTS_SUPER_ONLY = env_bool("API_ACCOUNT_ENDPOINTS_SUPER_ONLY", False)

# Per-endpoint limits for unauthenticated read endpoints (the global limits
# still apply on top). Search gets its own, looser limit because the UI fires
# a request per keystroke.
PUBLIC_READ_RATE_LIMIT = os.environ.get("API_PUBLIC_READ_RATE_LIMIT", "60/minute")
TICKER_SEARCH_RATE_LIMIT = os.environ.get("API_TICKER_SEARCH_RATE_LIMIT", "120/minute")
# Hard per-IP ceiling on /simulate that the per-user "free simulation" quota
# cannot be used to bypass. Minting fresh anonymous Supabase users (each a new
# user.id with its own daily quota) no longer multiplies the number of costly
# LLM debates a single source can trigger, because this limit is keyed by the
# (salted) client IP, independent of the authenticated identity. Set to 0 to
# disable. Enforced manually (not via a slowapi decorator) so the route keeps a
# resolvable Pydantic body annotation under `from __future__ import annotations`.
SIMULATE_IP_HOURLY_LIMIT = int(os.environ.get("API_SIMULATE_IP_HOURLY_LIMIT", "10"))
SIMULATE_IP_WINDOW_MS = 60 * 60 * 1000
limiter = Limiter(
    key_func=rate_limit_key,
    default_limits=env_limit_list("API_DEFAULT_RATE_LIMITS", "120/minute"),
    application_limits=env_limit_list("API_GLOBAL_RATE_LIMITS", "300/minute,5000/hour"),
    storage_uri=rate_limit_storage_uri(),
    headers_enabled=True,
    strategy=os.environ.get("API_RATE_LIMIT_STRATEGY", "fixed-window"),
    enabled=api_rate_limit_enabled,
    swallow_errors=True,
    in_memory_fallback=env_limit_list("API_RATE_LIMIT_FALLBACK_LIMITS", "120/minute"),
    in_memory_fallback_enabled=True,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

cors_origins = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS", os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# Security headers applied to every API response. This is a JSON-only API, so a
# locked-down CSP (default-src 'none') is appropriate — nothing should embed,
# script, or style these responses. HSTS is set for direct API hits over TLS.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Assign/propagate a request ID for every call.

    Honors an inbound X-Request-ID from the trusted proxy (truncated, sanitized)
    or generates one. The ID is stored in a contextvar so every log line emitted
    while handling the request carries it, echoed back in the X-Request-ID
    response header, and included in rate-limit error payloads.
    """
    inbound = (request.headers.get("x-request-id") or "").strip()
    rid = re.sub(r"[^a-zA-Z0-9_-]", "", inbound)[:64] or uuid.uuid4().hex[:16]
    request.state.request_id = rid
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers.setdefault("X-Request-ID", rid)
    return response


class UserInfo(BaseModel):
    id: str
    email: str | None = None
    is_anonymous: bool = False


class SimulateRequest(BaseModel):
    ticker: str
    headline: str
    source: str | None = None
    summary: str | None = None
    article_url: str | None = None


class CancelOrdersRequest(BaseModel):
    orderIds: list[str] = []


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = create_redis_client()
    return _redis_client


_http_async: httpx.AsyncClient | None = None


def get_async_http() -> httpx.AsyncClient:
    """Shared AsyncClient for outbound calls on hot request paths.

    A single pooled client keeps connections warm to Alpaca/Supabase Auth and,
    unlike the previous per-request sync httpx.Client, never ties up the
    FastAPI threadpool while waiting on the network.
    """
    global _http_async
    if _http_async is None:
        _http_async = httpx.AsyncClient(timeout=15)
    return _http_async


def get_supabase():
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(
            supabase_url=os.environ["SUPABASE_URL"],
            supabase_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
            options=ClientOptions(
                schema=os.environ.get("SUPABASE_DB_SCHEMA", "public")
            ),
        )
    return _supabase_client


def get_supabase_anon_key() -> str:
    return os.environ.get("SUPABASE_ANON_KEY", "")


def auth_token(authorization: str | None) -> str | None:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


async def get_user_from_token(token: str) -> UserInfo:
    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    anon_key = get_supabase_anon_key()
    if not anon_key:
        raise HTTPException(
            status_code=500, detail="SUPABASE_ANON_KEY is not configured"
        )

    try:
        response = await get_async_http().get(
            f"{supabase_url}/auth/v1/user",
            headers={
                "apikey": anon_key,
                "Authorization": f"Bearer {token}",
            },
            timeout=5,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503, detail="Could not validate Supabase session"
        ) from exc

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    payload = response.json()
    return UserInfo(
        id=str(payload.get("id") or ""),
        email=payload.get("email"),
        is_anonymous=payload.get("is_anonymous") is True,
    )


async def get_optional_user(
    authorization: str | None = Header(default=None),
) -> UserInfo | None:
    token = auth_token(authorization)
    if not token:
        return None
    return await get_user_from_token(token)


async def require_user(authorization: str | None = Header(default=None)) -> UserInfo:
    token = auth_token(authorization)
    if not token:
        raise HTTPException(
            status_code=401, detail="Authentication required. Please sign in."
        )
    return await get_user_from_token(token)


def is_super_user(user: UserInfo) -> bool:
    allowed = [
        email.strip().lower()
        for email in os.environ.get("SUPER_USER_EMAILS", "").split(",")
        if email.strip()
    ]
    return bool(user.email and user.email.lower() in allowed)


def require_super_user(user: UserInfo = Depends(require_user)) -> UserInfo:
    if user.is_anonymous:
        raise HTTPException(
            status_code=403, detail="Please sign in to access this feature."
        )
    if not is_super_user(user):
        raise HTTPException(
            status_code=403,
            detail="Admin access required. Your account does not have permission to modify this.",
        )
    return user


def require_account_viewer(user: UserInfo = Depends(require_user)) -> UserInfo:
    """Gate for live broker-account endpoints (/orders, /portfolio).

    Default: any signed-in account (public paper-trading demo). Set
    API_ACCOUNT_ENDPOINTS_SUPER_ONLY=true to restrict the account's financial
    figures to the super-user allowlist.
    """
    if ACCOUNT_ENDPOINTS_SUPER_ONLY and not is_super_user(user):
        raise HTTPException(
            status_code=403,
            detail="Account data is restricted on this deployment.",
        )
    return user


def user_tier(user: UserInfo) -> UserTier:
    if is_super_user(user):
        return "super"
    return "anonymous" if user.is_anonymous else "social"


def valid_iso_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def contains_injection_marker(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in INJECTION_MARKERS)


def status_error_detail(prefix: str, error: Exception) -> str:
    # /status is unauthenticated and polled frequently by the dashboard. Log the
    # full exception server-side but return only a generic message so raw driver
    # / connection / schema details never reach the public response.
    log.warning("%s: %s", prefix, error)
    return prefix


def number_value(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return (
        number
        if number == number and number not in (float("inf"), float("-inf"))
        else None
    )


def string_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def reasoning_from_trace(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    reasoning = (value.get("portfolio_manager_decision") or {}).get("reasoning")
    return reasoning if isinstance(reasoning, str) else ""


def alpaca_headers() -> dict[str, str]:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("Missing Alpaca API credentials")
    return {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }


async def alpaca_fetch(path: str) -> Any:
    response = await get_async_http().get(
        f"{ALPACA_BASE_URL}{path}", headers=alpaca_headers()
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Alpaca API returned HTTP {response.status_code} for {path}"
        )
    return response.json()


def check_simulate_limit(user_id: str, tier: UserTier) -> dict[str, Any]:
    limit, window_ms, prefix = RATE_LIMITS[tier]
    now_ms = int(time.time() * 1000)
    window_start = (now_ms // window_ms) * window_ms
    reset = window_start + window_ms
    ttl_ms = max(reset - now_ms, 1)
    key = f"{prefix}:{window_start}:{user_id}"

    count = int(get_redis().eval(FIXED_WINDOW_SCRIPT, 1, key, str(ttl_ms)))
    remaining = max(limit - count, 0)
    success = count <= limit

    if success:
        return {"success": True, "remaining": remaining, "reset": reset}

    minutes_until_reset = max((reset - int(time.time() * 1000) + 59_999) // 60_000, 1)
    if tier == "anonymous":
        error_message = "You've used your free simulation! Sign in with GitHub, Google, or Magic Link to get more."
    elif tier == "social":
        error_message = f"You've reached your daily simulation limit (2/day). Resets in {minutes_until_reset} minutes."
    else:
        error_message = "Rate limit exceeded. Please wait a moment and try again."

    return {
        "success": False,
        "remaining": remaining,
        "reset": reset,
        "errorMessage": error_message,
        "needsAuth": tier == "anonymous",
    }


def peek_simulate_limit(user_id: str, tier: UserTier) -> dict[str, Any]:
    """Read remaining simulations without consuming one (no INCR)."""
    limit, window_ms, prefix = RATE_LIMITS[tier]
    now_ms = int(time.time() * 1000)
    window_start = (now_ms // window_ms) * window_ms
    reset = window_start + window_ms
    key = f"{prefix}:{window_start}:{user_id}"

    raw = get_redis().get(key)
    count = int(raw) if raw is not None else 0
    remaining = max(limit - count, 0)
    return {"limit": limit, "remaining": remaining, "reset": reset, "tier": tier}


def validate_simulation(payload: SimulateRequest) -> tuple[str, str, str]:
    ticker = payload.ticker.strip().upper()
    headline = html.unescape(payload.headline.strip())
    source = (payload.source or "simulation").strip() or "simulation"

    if not TICKER_REGEX.match(ticker):
        raise HTTPException(
            status_code=400, detail="Invalid ticker. Must be 1-6 uppercase letters."
        )
    if len(headline) < 5:
        raise HTTPException(
            status_code=400,
            detail="Headline is required and must be at least 5 characters.",
        )
    if len(headline) > 500:
        raise HTTPException(
            status_code=400, detail="Headline must be 500 characters or fewer."
        )
    if payload.summary and len(payload.summary) > 2000:
        raise HTTPException(
            status_code=400, detail="Summary must be 2000 characters or fewer."
        )
    if len(source) > 200:
        raise HTTPException(
            status_code=400, detail="Source must be 200 characters or fewer."
        )
    if payload.article_url:
        if len(payload.article_url) > 2048:
            raise HTTPException(
                status_code=400, detail="Article URL must be 2048 characters or fewer."
            )
        # Reject non-http(s) schemes (javascript:, data:, …) at the boundary so a
        # malicious URL can never be stored and later rendered into an href. The
        # frontend wraps it in safeArticleUrl() too — this is defense in depth.
        scheme = urlparse(payload.article_url.strip()).scheme.lower()
        if scheme not in {"http", "https"}:
            raise HTTPException(
                status_code=400, detail="Article URL must be an http(s) URL."
            )
    if contains_injection_marker(headline) or (
        payload.summary and contains_injection_marker(payload.summary)
    ):
        raise HTTPException(
            status_code=400, detail="Input contains disallowed phrases."
        )

    return ticker, headline, source


def model_size_billions(model_id: str) -> float:
    sizes = [
        float(match)
        for match in re.findall(r"(\d+(?:\.\d+)?)\s*b(?:\b|-|_)", model_id, flags=re.I)
    ]
    return max(sizes) if sizes else 0


def score_groq_model(model: dict[str, Any]) -> float:
    model_id = str(model.get("id") or "").lower()
    if not model_id or not model.get("active"):
        return 0
    if any(term in model_id for term in EXCLUDED_MODEL_TERMS):
        return 0
    if int(model.get("context_window") or 0) < 8192:
        return 0
    if int(model.get("max_completion_tokens") or 0) < 1024:
        return 0

    score = min(model_size_billions(model_id), 160) * 4
    score += min(int(model.get("context_window") or 0), 131072) / 2048
    score += min(int(model.get("max_completion_tokens") or 0), 65536) / 4096
    score += next((hint for term, hint in FAMILY_HINTS if term in model_id), 0)
    if "instruct" in model_id:
        score += 45
    if "versatile" in model_id:
        score += 45
    if "reason" in model_id:
        score += 45
    if "instant" in model_id:
        score -= 120
    if "preview" in model_id:
        score -= 35
    return score


def format_model_label(model_id: str) -> str:
    return " ".join(
        part.capitalize() if part.upper() != part else part
        for part in re.split(r"[-_]", model_id.split("/")[-1])
        if part
    )


def fallback_auto_cascade() -> list[dict[str, str]]:
    return [
        {
            "id": "auto-ranked-groq-models",
            "label": "Auto-ranked Groq models",
            "reqDay": "Discovered by backend agent",
            "tpm": "Live active model list",
            "quality": "high",
        }
    ]


def default_llm_provider_config() -> dict[str, Any]:
    return {"type": "groq-always-free"}


def normalize_openrouter_profile_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=400, detail="openrouter profile must be an object"
        )
    base_url = str(raw.get("base_url") or DEFAULT_OPENROUTER_BASE_URL).strip()
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400, detail="llm_provider.base_url must be a URL"
        )

    routing_raw = raw.get("routing") if isinstance(raw.get("routing"), dict) else {}

    def positive_float(key: str, default: float) -> float:
        value = routing_raw.get(key)
        try:
            parsed = float(value if value not in (None, "") else default)
        except (TypeError, ValueError):
            parsed = default
        if parsed <= 0:
            raise HTTPException(
                status_code=400, detail=f"llm_provider.routing.{key} must be positive"
            )
        return parsed

    models_raw = raw.get("models")
    if not isinstance(models_raw, list) or not models_raw:
        raise HTTPException(
            status_code=400, detail="openrouter provider requires at least one model"
        )

    seen_priorities: set[int] = set()
    seen_ids: set[str] = set()
    models: list[dict[str, Any]] = []
    for index, item in enumerate(models_raw, start=1):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="each model must be an object")
        try:
            priority = int(item.get("priority", item.get("preference", index)))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="model priority must be an integer"
            )
        if priority < 1 or priority in seen_priorities:
            raise HTTPException(
                status_code=400,
                detail="model priorities must be unique positive integers",
            )
        seen_priorities.add(priority)

        model_id = str(item.get("id") or item.get("model") or "").strip()
        if not model_id or model_id in seen_ids:
            raise HTTPException(
                status_code=400, detail="model ids must be present and unique"
            )
        seen_ids.add(model_id)

        try:
            temperature = float(item.get("temperature", 0.7))
            top_p = float(item.get("top_p", 0.7))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="temperature and top_p must be numbers"
            )
        if not 0 <= temperature <= 2:
            raise HTTPException(
                status_code=400, detail="model temperature must be between 0 and 2"
            )
        if not 0 <= top_p <= 1:
            raise HTTPException(
                status_code=400, detail="model top_p must be between 0 and 1"
            )

        models.append(
            {
                "priority": priority,
                "id": model_id,
                "temperature": temperature,
                "top_p": top_p,
            }
        )

    models.sort(key=lambda model: (model["priority"], model["id"]))
    return {
        "base_url": base_url.rstrip("/"),
        "routing": {
            "strategy": "ordered_fallback",
            "max_wait_seconds": positive_float("max_wait_seconds", 600),
            "default_cooldown_seconds": positive_float("default_cooldown_seconds", 60),
            "key_status_check_interval_seconds": positive_float(
                "key_status_check_interval_seconds", 300
            ),
        },
        "models": models,
    }


def normalize_llm_provider_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        return default_llm_provider_config()

    provider_type = str(raw.get("type") or "groq-always-free").strip().lower()
    if provider_type == "groq-always-free":
        normalized = default_llm_provider_config()
        profile_raw = (
            raw.get("openrouter") if isinstance(raw.get("openrouter"), dict) else raw
        )
        if (
            isinstance(profile_raw, dict)
            and isinstance(profile_raw.get("models"), list)
            and profile_raw.get("models")
        ):
            try:
                normalized["openrouter"] = normalize_openrouter_profile_config(
                    profile_raw
                )
            except HTTPException:
                pass
        return normalized
    if provider_type != "openrouter":
        raise HTTPException(
            status_code=400,
            detail="llm_provider.type must be groq-always-free or openrouter",
        )

    return {"type": "openrouter", **normalize_openrouter_profile_config(raw)}


def get_model_cascade() -> list[dict[str, str]]:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return fallback_auto_cascade()
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(
                GROQ_MODELS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
        if response.status_code >= 400:
            return fallback_auto_cascade()
        ranked = sorted(
            [
                (model, score_groq_model(model))
                for model in response.json().get("data", [])
                if isinstance(model, dict)
            ],
            key=lambda item: (-item[1], str(item[0].get("id") or "")),
        )
        ranked = [
            (model, score) for model, score in ranked if score > 0 and model.get("id")
        ][:8]
        if not ranked:
            return fallback_auto_cascade()
        return [
            {
                "id": model["id"],
                "label": format_model_label(model["id"]),
                "reqDay": "Live Groq model",
                "tpm": f"{round(int(model.get('context_window') or 0) / 1000)}K context",
                "quality": "high" if index == 0 else "mid" if index < 5 else "fallback",
            }
            for index, (model, _score) in enumerate(ranked)
        ]
    except Exception:
        return fallback_auto_cascade()


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/auth/me")
def auth_me(user: UserInfo | None = Depends(get_optional_user)) -> dict[str, bool]:
    if not user:
        return {"isSuperUser": False, "isAnonymous": True}
    return {"isSuperUser": is_super_user(user), "isAnonymous": user.is_anonymous}


@app.get("/trades")
@limiter.limit(PUBLIC_READ_RATE_LIMIT)
def trades(
    request: Request,
    response: Response,
    before: str | None = None,
    after: str | None = None,
) -> dict[str, Any]:
    if before and after:
        raise HTTPException(
            status_code=400, detail="Use either 'before' or 'after', not both."
        )
    if before and not valid_iso_timestamp(before):
        raise HTTPException(
            status_code=400,
            detail="Invalid 'before' parameter. Must be a valid ISO 8601 timestamp.",
        )
    if after and not valid_iso_timestamp(after):
        raise HTTPException(
            status_code=400,
            detail="Invalid 'after' parameter. Must be a valid ISO 8601 timestamp.",
        )

    query = (
        get_supabase()
        .table("trades")
        .select(TRADE_SUMMARY_SELECT)
        .order("created_at", desc=True)
        .limit(PAGE_SIZE + 1)
    )
    if before:
        query = query.lt("created_at", before)
    if after:
        query = query.gt("created_at", after)

    result = query.execute()
    rows = result.data or []
    has_more = len(rows) > PAGE_SIZE
    return {"trades": rows[:PAGE_SIZE] if has_more else rows, "hasMore": has_more}


@app.get("/trades/{trade_id}")
@limiter.limit(PUBLIC_READ_RATE_LIMIT)
def trade_detail(request: Request, response: Response, trade_id: str) -> dict[str, Any]:
    if not UUID_RE.match(trade_id):
        raise HTTPException(status_code=400, detail="Invalid trade id.")

    sb = get_supabase()
    try:
        trade = (
            sb.table("trades")
            .select(TRADE_SUMMARY_SELECT)
            .eq("id", trade_id)
            .single()
            .execute()
            .data
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Trade not found.") from exc

    try:
        trace_row = (
            sb.table("trade_decision_traces")
            .select(TRACE_DETAIL_SELECT)
            .eq("trade_id", trade_id)
            .maybe_single()
            .execute()
            .data
        )
    except Exception:
        trace_row = None
        try:
            legacy_trade = (
                sb.table("trades")
                .select(LEGACY_TRADE_DETAIL_SELECT)
                .eq("id", trade_id)
                .single()
                .execute()
                .data
            )
            return {"trade": legacy_trade}
        except Exception:
            log.exception("Trade trace query error")

    return {
        "trade": {
            **trade,
            "reasoning": (trace_row or {}).get("reasoning")
            or reasoning_from_trace((trace_row or {}).get("decision_trace")),
            "article_source": (trace_row or {}).get("article_source"),
            "article_id": (trace_row or {}).get("article_id"),
            "decision_trace": (trace_row or {}).get("decision_trace"),
        }
    }


@app.delete("/trades/{trade_id}")
def delete_trade(
    trade_id: str, user: UserInfo = Depends(require_super_user)
) -> dict[str, Any]:
    """Super-admin only: delete a simulated signal (and its decision trace).

    Hard-restricted to simulated rows so real, executed trades can never be
    removed through this endpoint.
    """
    if not UUID_RE.match(trade_id):
        raise HTTPException(status_code=400, detail="Invalid trade id.")

    sb = get_supabase()
    try:
        existing = (
            sb.table("trades")
            .select("id, is_simulated")
            .eq("id", trade_id)
            .single()
            .execute()
            .data
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Trade not found.") from exc

    if not existing.get("is_simulated"):
        raise HTTPException(
            status_code=403, detail="Only simulated signals can be deleted."
        )

    # Single atomic statement: trade_decision_traces.trade_id has
    # ON DELETE CASCADE, so the trace is removed in the same transaction —
    # no partial-delete window, no orphaned trace rows.
    sb.table("trades").delete().eq("id", trade_id).execute()

    log.info("Super admin %s deleted simulated trade %s", user.email, trade_id)
    return {"success": True, "id": trade_id}


DASHBOARD_STAT_KEYS = (
    "analyzed",
    "executed",
    "buyOrders",
    "sellOrders",
    "riskGated",
    "preScreened",
    "fullDebates",
    "avgSentiment",
)


@app.get("/stats")
@limiter.limit(PUBLIC_READ_RATE_LIMIT)
def stats(request: Request, response: Response) -> dict[str, Any]:
    # Aggregate in the database (single atomic snapshot) instead of fetching rows
    # and counting in Python. PostgREST caps row responses at db-max-rows and
    # returns an arbitrary unordered subset, which made the old fetch-and-count
    # approach jitter on every refresh and badly understate the true totals.
    result = get_supabase().rpc("dashboard_stats").execute()
    payload = result.data if isinstance(result.data, dict) else {}
    stats_data = {
        key: payload.get(key, 0) or (0.0 if key == "avgSentiment" else 0)
        for key in DASHBOARD_STAT_KEYS
    }
    return {"stats": stats_data, "fetchedAt": now_iso()}


@app.get("/calibration")
@limiter.limit(PUBLIC_READ_RATE_LIMIT)
def calibration(
    request: Request,
    response: Response,
    min_signals: int = Query(default=1, ge=1, le=500),
) -> dict[str, Any]:
    """
    Outcome-driven calibration: forward returns and hit rate bucketed by the
    committee's conviction. Public read-only aggregate (no account data), built
    entirely from already-persisted signal_outcomes — no extra cost.
    """
    try:
        result = (
            get_supabase()
            .rpc("signal_calibration", {"min_signals": min_signals})
            .execute()
        )
    except Exception:
        log.exception("Calibration query failed")
        raise HTTPException(status_code=500, detail="Could not load calibration data.")
    payload = result.data if isinstance(result.data, dict) else {}
    return {"calibration": payload, "fetchedAt": now_iso()}



# Enhanced trading field validation — matches defaults in agent/config.py
_ENHANCED_BOOL_KEYS = {
    "bracket_orders",
    "trailing_stops",
    "concentration_limits",
    "circuit_breaker",
    "dynamic_position_sizing",
    "feedback_loop",
    "use_limit_orders",
    "price_move_gate",
    "technical_indicators",
    "signal_momentum",
    "source_credibility",
    "market_hours_awareness",
    "structured_synthesis",
}

_ENHANCED_FLOAT_RANGES: dict[str, tuple[float, float]] = {
    "stop_loss_pct": (0.001, 0.50),
    "take_profit_pct": (0.001, 1.0),
    "trailing_stop_pct": (0.001, 0.50),
    "trailing_stop_activation_pct": (0.0, 0.50),
    "max_single_ticker_pct": (0.01, 1.0),
    "max_daily_loss_pct": (0.001, 0.50),
    "max_position_pct": (0.001, 1.0),
    "limit_order_buffer_pct": (0.0, 0.10),
    "max_price_move_pct": (0.001, 0.50),
}

_ENHANCED_INT_RANGES: dict[str, tuple[int, int]] = {
    "feedback_loop_lookback_days": (1, 365),
}


def _validate_enhanced_trading(data: dict[str, Any]) -> None:
    """Validate enhanced_trading keys and value ranges."""
    allowed_keys = _ENHANCED_BOOL_KEYS | set(_ENHANCED_FLOAT_RANGES) | set(_ENHANCED_INT_RANGES)
    unknown = set(data) - allowed_keys
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown enhanced_trading keys: {', '.join(sorted(unknown))}",
        )
    for key in _ENHANCED_BOOL_KEYS:
        val = data.get(key)
        if val is not None and not isinstance(val, bool):
            raise HTTPException(
                status_code=400, detail=f"enhanced_trading.{key} must be a boolean"
            )
    for key, (lo, hi) in _ENHANCED_FLOAT_RANGES.items():
        val = data.get(key)
        if val is not None:
            try:
                fval = float(val)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"enhanced_trading.{key} must be a number",
                )
            if not lo <= fval <= hi:
                raise HTTPException(
                    status_code=400,
                    detail=f"enhanced_trading.{key} must be between {lo} and {hi}",
                )
    for key, (lo, hi) in _ENHANCED_INT_RANGES.items():
        val = data.get(key)
        if val is not None:
            try:
                ival = int(val)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"enhanced_trading.{key} must be an integer",
                )
            if not lo <= ival <= hi:
                raise HTTPException(
                    status_code=400,
                    detail=f"enhanced_trading.{key} must be between {lo} and {hi}",
                )


@app.get("/agent-config")
def agent_config(
    user: UserInfo | None = Depends(get_optional_user),
) -> dict[str, Any]:
    # The persona system prompts are proprietary strategy IP. Expose the public
    # dashboard fields (thresholds, model cascade, enhanced-trading flags) to
    # everyone, but reveal the prompt text only to super-users who can edit it.
    caller_is_super = bool(user and is_super_user(user))
    result = (
        get_supabase()
        .table("agent_config")
        .select("config")
        .eq("id", 1)
        .single()
        .execute()
    )
    row = result.data.get("config") if result.data else None
    if not row:
        raise HTTPException(
            status_code=500, detail="Failed to load agent configuration."
        )
    llm_provider = normalize_llm_provider_config(row.get("llm_provider"))
    model_cascade = (
        get_model_cascade()
        if llm_provider["type"] == "groq-always-free"
        else [
            {
                "id": model["id"],
                "label": format_model_label(model["id"]),
                "reqDay": f"Priority {model['priority']}",
                "tpm": f"temperature={model['temperature']} top_p={model['top_p']}",
                "quality": "high" if index == 0 else "mid" if index < 3 else "fallback",
            }
            for index, model in enumerate(llm_provider.get("models", []))
        ]
    )
    enhanced = row.get("enhanced_trading") or {}
    return {
        "thresholds": {
            "buy_sentiment": row.get("buy_sentiment_threshold"),
            "sell_sentiment": row.get("sell_sentiment_threshold"),
            "confidence": row.get("confidence_threshold"),
        },
        "execution": {"order_qty": row.get("order_qty")},
        "llm_provider": llm_provider,
        "model": {
            "cascade": model_cascade,
            "override": None,
        },
        "prompts": {
            "momentum": row.get("momentum_system_prompt") if caller_is_super else "",
            "value": row.get("value_system_prompt") if caller_is_super else "",
            "risk": row.get("risk_system_prompt") if caller_is_super else "",
            "synthesis": row.get("synthesis_system_prompt") if caller_is_super else "",
        },
        "enhanced_trading": {
            "bracket_orders": bool(enhanced.get("bracket_orders", False)),
            "stop_loss_pct": float(enhanced.get("stop_loss_pct", 0.03)),
            "take_profit_pct": float(enhanced.get("take_profit_pct", 0.06)),
            "trailing_stops": bool(enhanced.get("trailing_stops", False)),
            "trailing_stop_pct": float(enhanced.get("trailing_stop_pct", 0.03)),
            "trailing_stop_activation_pct": float(enhanced.get("trailing_stop_activation_pct", 0.02)),
            "concentration_limits": bool(enhanced.get("concentration_limits", False)),
            "max_single_ticker_pct": float(enhanced.get("max_single_ticker_pct", 0.10)),
            "circuit_breaker": bool(enhanced.get("circuit_breaker", False)),
            "max_daily_loss_pct": float(enhanced.get("max_daily_loss_pct", 0.02)),
            "dynamic_position_sizing": bool(enhanced.get("dynamic_position_sizing", False)),
            "max_position_pct": float(enhanced.get("max_position_pct", 0.05)),
            "feedback_loop": bool(enhanced.get("feedback_loop", False)),
            "feedback_loop_lookback_days": int(enhanced.get("feedback_loop_lookback_days", 30)),
            "use_limit_orders": bool(enhanced.get("use_limit_orders", False)),
            "limit_order_buffer_pct": float(enhanced.get("limit_order_buffer_pct", 0.005)),
            "price_move_gate": bool(enhanced.get("price_move_gate", False)),
            "max_price_move_pct": float(enhanced.get("max_price_move_pct", 0.03)),
            "technical_indicators": bool(enhanced.get("technical_indicators", False)),
            "signal_momentum": bool(enhanced.get("signal_momentum", False)),
            "source_credibility": bool(enhanced.get("source_credibility", False)),
            "market_hours_awareness": bool(enhanced.get("market_hours_awareness", False)),
            "structured_synthesis": bool(enhanced.get("structured_synthesis", False)),
        },
        "consumer": {"batch_size": 10, "poll_interval": 1.0, "error_retry": 5.0},
    }


@app.post("/agent-config")
def update_agent_config(
    body: dict[str, Any], _user: UserInfo = Depends(require_super_user)
) -> dict[str, bool]:
    thresholds = (
        body.get("thresholds") if isinstance(body.get("thresholds"), dict) else None
    )
    execution = (
        body.get("execution") if isinstance(body.get("execution"), dict) else None
    )
    model = body.get("model") if isinstance(body.get("model"), dict) else None
    llm_provider = (
        body.get("llm_provider") if isinstance(body.get("llm_provider"), dict) else None
    )
    prompts = body.get("prompts") if isinstance(body.get("prompts"), dict) else None
    enhanced_trading = (
        body.get("enhanced_trading")
        if isinstance(body.get("enhanced_trading"), dict)
        else None
    )

    if thresholds:
        buy = thresholds.get("buy_sentiment")
        sell = thresholds.get("sell_sentiment")
        confidence = thresholds.get("confidence")
        if buy is not None and not -1 <= float(buy) <= 1:
            raise HTTPException(
                status_code=400, detail="buy_sentiment must be between -1 and 1"
            )
        if sell is not None and not -1 <= float(sell) <= 1:
            raise HTTPException(
                status_code=400, detail="sell_sentiment must be between -1 and 1"
            )
        if confidence is not None and not 0 <= float(confidence) <= 1:
            raise HTTPException(
                status_code=400, detail="confidence must be between 0 and 1"
            )

    if execution and execution.get("order_qty") is not None:
        qty = execution["order_qty"]
        if not isinstance(qty, int) or qty < 1 or qty > 100:
            raise HTTPException(
                status_code=400, detail="order_qty must be an integer between 1 and 100"
            )

    if prompts:
        for key, value in prompts.items():
            if isinstance(value, str) and len(value) > MAX_PROMPT_LENGTH:
                raise HTTPException(
                    status_code=400,
                    detail=f'Prompt "{key}" exceeds maximum length of {MAX_PROMPT_LENGTH} characters',
                )

    if enhanced_trading:
        _validate_enhanced_trading(enhanced_trading)

    patch: dict[str, Any] = {}
    if thresholds:
        if thresholds.get("buy_sentiment") is not None:
            patch["buy_sentiment_threshold"] = thresholds["buy_sentiment"]
        if thresholds.get("sell_sentiment") is not None:
            patch["sell_sentiment_threshold"] = thresholds["sell_sentiment"]
        if thresholds.get("confidence") is not None:
            patch["confidence_threshold"] = thresholds["confidence"]
    if execution and execution.get("order_qty") is not None:
        patch["order_qty"] = execution["order_qty"]
    if llm_provider is not None:
        patch["llm_provider"] = normalize_llm_provider_config(llm_provider)
        patch["model_override"] = None
    if prompts:
        if prompts.get("momentum"):
            patch["momentum_system_prompt"] = prompts["momentum"]
        if prompts.get("value"):
            patch["value_system_prompt"] = prompts["value"]
        if prompts.get("risk"):
            patch["risk_system_prompt"] = prompts["risk"]
        if prompts.get("synthesis"):
            patch["synthesis_system_prompt"] = prompts["synthesis"]
    if enhanced_trading is not None:
        patch["enhanced_trading"] = enhanced_trading

    sb = get_supabase()
    existing = (
        sb.table("agent_config").select("config").eq("id", 1).single().execute().data
    )
    merged = {**(existing.get("config") if existing else {}), **patch}
    result = (
        sb.table("agent_config")
        .update({"config": merged, "updated_at": now_iso()})
        .eq("id", 1)
        .execute()
    )
    if not result.data:
        raise HTTPException(
            status_code=500, detail="Failed to update configuration. Please try again."
        )
    return {"ok": True}


@app.get("/orders")
async def orders(
    status: str = "all",
    limit: int = Query(default=100, ge=1, le=500),
    _user: UserInfo = Depends(require_account_viewer),
) -> dict[str, Any]:
    try:
        safe_status = status if status in ALLOWED_ORDER_STATUSES else "all"
        query = f"status={safe_status}&limit={limit}&direction=desc&nested=true"
        account, positions, order_rows = await asyncio.gather(
            alpaca_fetch("/v2/account"),
            alpaca_fetch("/v2/positions"),
            alpaca_fetch(f"/v2/orders?{query}"),
        )
        # Strip account-identifying fields before returning to the browser. The
        # dashboard only needs the financial figures, not the account identity.
        for field_name in ("account_number", "id"):
            account.pop(field_name, None)
        return {
            "account": account,
            "positions": positions,
            "orders": order_rows,
            "fetchedAt": now_iso(),
        }
    except Exception:
        log.exception("Orders fetch error")
        return {
            "account": None,
            "positions": [],
            "orders": [],
            "fetchedAt": now_iso(),
            "error": "Could not load orders.",
        }


@app.post("/orders/cancel")
def cancel_orders(
    payload: CancelOrdersRequest, _user: UserInfo = Depends(require_super_user)
) -> dict[str, Any]:
    order_ids = list(
        dict.fromkeys(
            order_id
            for order_id in payload.orderIds
            if isinstance(order_id, str) and order_id
        )
    )
    if not order_ids:
        raise HTTPException(status_code=400, detail="No order ids provided")
    if len(order_ids) > MAX_ORDER_IDS:
        raise HTTPException(
            status_code=400, detail=f"Too many order IDs. Maximum is {MAX_ORDER_IDS}."
        )

    results = []
    with httpx.Client(timeout=15) as client:
        for order_id in order_ids:
            response = client.delete(
                f"{ALPACA_BASE_URL}/v2/orders/{order_id}",
                headers=alpaca_headers(),
            )
            if response.status_code == 204:
                results.append(
                    {
                        "id": order_id,
                        "ok": True,
                        "status": 204,
                        "message": "Cancel request accepted",
                    }
                )
                continue
            body = response.json() if response.content else {}
            message = (
                body.get("message")
                if isinstance(body, dict) and isinstance(body.get("message"), str)
                else f"Alpaca returned HTTP {response.status_code}"
            )
            results.append(
                {
                    "id": order_id,
                    "ok": False,
                    "status": response.status_code,
                    "message": message,
                }
            )

    return {
        "results": results,
        "canceled": len([result for result in results if result["ok"]]),
        "failed": len([result for result in results if not result["ok"]]),
    }


@app.get("/portfolio")
async def portfolio(
    range: str = "D", _user: UserInfo = Depends(require_account_viewer)
) -> dict[str, Any]:
    try:
        range_key = range if range in RANGE_CONFIG else "D"
        config = RANGE_CONFIG[range_key]
        query = f"period={config['period']}&timeframe={config['timeframe']}"
        if config["intraday"]:
            # intraday_reporting only applies to intraday timeframes per Alpaca docs
            query += "&intraday_reporting=extended_hours"
        data, account = await asyncio.gather(
            alpaca_fetch(f"/v2/account/portfolio/history?{query}"),
            alpaca_fetch("/v2/account"),
        )

        timestamps = (
            data.get("timestamp") if isinstance(data.get("timestamp"), list) else []
        )
        equities = data.get("equity") if isinstance(data.get("equity"), list) else []
        profit_loss = (
            data.get("profit_loss") if isinstance(data.get("profit_loss"), list) else []
        )
        profit_loss_pct = (
            data.get("profit_loss_pct")
            if isinstance(data.get("profit_loss_pct"), list)
            else []
        )

        # Build points keyed by epoch-ms so the series is strictly sorted and free
        # of duplicate timestamps regardless of what Alpaca returns. The numeric
        # `t` lets the frontend use a real time-scale axis (proportional spacing,
        # unique tick labels) instead of treating timestamps as categories.
        points: dict[int, dict[str, Any]] = {}
        for index, ts in enumerate(timestamps):
            ts_value = number_value(ts)
            equity = number_value(equities[index] if index < len(equities) else None)
            if ts_value is None or equity is None:
                continue
            t_ms = int(ts_value * 1000)
            points[t_ms] = {
                "t": t_ms,
                "timestamp": datetime.fromtimestamp(ts_value, timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "equity": equity,
            }
        history = [points[key] for key in sorted(points)]

        live_equity = number_value(
            account.get("portfolio_value") or account.get("equity")
        )
        latest = history[-1] if history else None
        if live_equity and live_equity > 0:
            now_ms = int(time.time() * 1000)
            latest_age_ms = now_ms - latest["t"] if latest else float("inf")
            value_changed = (
                abs(latest["equity"] - live_equity) >= 0.005 if latest else True
            )
            if not latest or latest_age_ms > 45_000 or value_changed:
                history.append(
                    {"t": now_ms, "timestamp": now_iso(), "equity": live_equity}
                )

        base_value = number_value(data.get("base_value"))
        raw_profit_loss = number_value(profit_loss[-1] if profit_loss else None)
        raw_profit_loss_pct = number_value(
            profit_loss_pct[-1] if profit_loss_pct else None
        )
        raw_latest_equity = number_value(equities[-1] if equities else None)
        current_equity = (
            live_equity
            if live_equity and live_equity > 0
            else history[-1]["equity"] if history else 0
        )
        derived_profit_loss = (
            current_equity - base_value
            if base_value is not None
            else raw_profit_loss or 0
        )
        derived_profit_loss_pct = (
            derived_profit_loss / base_value
            if base_value and base_value > 0
            else raw_profit_loss_pct or 0
        )
        live_value_changed = (
            raw_latest_equity is not None
            and abs(current_equity - raw_latest_equity) >= 0.005
        )

        return {
            "history": history,
            "summary": {
                "equity": current_equity,
                "profitLoss": (
                    derived_profit_loss
                    if live_value_changed
                    else raw_profit_loss or derived_profit_loss
                ),
                "profitLossPct": (
                    derived_profit_loss_pct
                    if live_value_changed
                    else raw_profit_loss_pct or derived_profit_loss_pct
                ),
                "baseValue": base_value,
                "baseValueAsOf": (
                    data.get("base_value_asof")
                    if isinstance(data.get("base_value_asof"), str)
                    else None
                ),
            },
            "account": {
                # account.id intentionally omitted — the dashboard only needs
                # status/currency, not the Alpaca account identity.
                "status": string_value(account.get("status")),
                "currency": string_value(account.get("currency")),
                "createdAt": string_value(account.get("created_at")),
                "paper": True,
            },
            "range": range_key,
            "source": "alpaca",
            "fetchedAt": now_iso(),
        }
    except Exception:
        log.exception("Portfolio fetch error")
        return {
            "history": [],
            "error": "Could not load portfolio history.",
            "fetchedAt": now_iso(),
        }


def _query_last_trade_created_at() -> Any:
    return (
        get_supabase()
        .table("trades")
        .select("created_at")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )


def _read_agent_worker_state(name: str) -> dict[str, Any] | None:
    return read_worker_state(get_redis(), name)


@app.get("/status")
async def status() -> dict[str, Any]:
    details: dict[str, str] = {}

    try:
        alpaca_res = await get_async_http().get(
            f"{ALPACA_BASE_URL}/v2/clock", headers=alpaca_headers(), timeout=5
        )
        alpaca_status: ServiceStatus = "ok" if alpaca_res.status_code < 400 else "error"
        details["alpaca"] = (
            "Paper trading clock reachable."
            if alpaca_status == "ok"
            else f"Alpaca returned HTTP {alpaca_res.status_code}."
        )
    except Exception:
        alpaca_status = "error"
        details["alpaca"] = "Could not reach Alpaca paper API."

    try:
        # supabase-py is synchronous — run it in the threadpool so this
        # async handler never blocks the event loop.
        data = await run_in_threadpool(_query_last_trade_created_at)
        supabase_status: ServiceStatus = "ok"
        last_trade_at = data[0]["created_at"] if data else None
        details["supabase"] = "Trades table reachable."
    except Exception as exc:
        supabase_status = "error"
        last_trade_at = None
        details["supabase"] = status_error_detail(
            "Could not query Supabase trades table", exc
        )

    try:
        agent_worker_name = worker_name("agent")
        agent_state = await run_in_threadpool(
            _read_agent_worker_state, agent_worker_name
        )
        redis_status_value: ServiceStatus = "ok"
        details["redis"] = "Redis reachable."
        groq_status: ServiceStatus = (
            agent_state.get("groq", "unknown") if agent_state else "unknown"
        )
        details["groq"] = (agent_state or {}).get(
            "groq_detail"
        ) or "Backend agent has not published LLM provider status yet."
        agent_status: ServiceStatus = "unknown"
        last_heartbeat_at = None

        if agent_state:
            heartbeat = int(agent_state.get("last_heartbeat_epoch") or 0)
            heartbeat_age = time.time() - heartbeat
            last_heartbeat_at = agent_state.get("last_heartbeat_at") or (
                datetime.fromtimestamp(heartbeat, timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            phase = agent_state.get("phase") if agent_state else None
            worker_status = agent_state.get("status", "unknown")
            phase_detail = (
                f": {agent_state.get('detail')}"
                if agent_state and agent_state.get("detail")
                else ""
            )
            if heartbeat_age >= 180:
                agent_status = "error"
                details["agent"] = (
                    f"The agent worker heartbeat is stale by "
                    f"{int(heartbeat_age // 60)} minutes."
                )
            elif worker_status == "unhealthy":
                agent_status = "error"
                details["agent"] = f"The agent worker is unhealthy: {phase}{phase_detail}."
            elif heartbeat_age < 60 and worker_status == "healthy":
                agent_status = "ok"
                details["agent"] = f"The agent worker is fresh: {phase}{phase_detail}."
            else:
                agent_status = "stale"
                details["agent"] = (
                    f"The agent worker is fresh but status is "
                    f"{worker_status}, phase is {phase}{phase_detail}."
                    if phase
                    else "The agent worker is fresh, but no phase state was published."
                )
        else:
            agent_status = "error"
            details["agent"] = "No agent worker state has been published yet."
    except Exception as exc:
        redis_status_value = "error"
        agent_status = "unknown"
        groq_status = "unknown"
        last_heartbeat_at = None
        details["redis"] = status_error_detail(
            "Could not read Redis worker health", exc
        )
        details["agent"] = (
            "Agent status depends on Redis worker health, which could not be read."
        )
        details["groq"] = (
            "LLM provider status depends on backend agent worker state, which could not be read from Redis."
        )

    return {
        "alpaca": alpaca_status,
        "supabase": supabase_status,
        "groq": groq_status,
        "redis": redis_status_value,
        "agent": agent_status,
        "lastTradeAt": last_trade_at,
        "lastHeartbeatAt": last_heartbeat_at,
        "checkedAt": now_iso(),
        "details": details,
    }


# ── Prometheus metrics ───────────────────────────────────────────────────────


def _prom_label_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _prom_metric_name(key: str) -> str:
    """Sanitize an arbitrary worker-state field into a valid metric suffix."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", key).strip("_").lower()
    return sanitized or "value"


def _render_prometheus(samples: list[tuple[str, dict[str, str], float]]) -> str:
    """Render samples grouped by metric name with a single TYPE line each."""
    grouped: dict[str, list[tuple[dict[str, str], float]]] = {}
    order: list[str] = []
    for name, labels, value in samples:
        if name not in grouped:
            grouped[name] = []
            order.append(name)
        grouped[name].append((labels, value))

    lines: list[str] = []
    for name in order:
        lines.append(f"# TYPE {name} gauge")
        for labels, value in grouped[name]:
            if labels:
                label_str = ",".join(
                    f'{k}="{_prom_label_escape(str(v))}"' for k, v in labels.items()
                )
                lines.append(f"{name}{{{label_str}}} {value}")
            else:
                lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


@app.get("/metrics")
def metrics(authorization: str | None = Header(default=None)) -> PlainTextResponse:
    """
    Prometheus exposition of worker health + LLM budget.

    Scrape with Grafana Agent / Grafana Cloud's free tier. The data already lives
    in Redis (per-worker heartbeat state + the daily LLM-call counter), so this
    is a cheap read with no database hit. Protect with METRICS_AUTH_TOKEN if the
    endpoint is reachable outside a trusted network.
    """
    if METRICS_AUTH_TOKEN and not hmac.compare_digest(
        auth_token(authorization) or "", METRICS_AUTH_TOKEN
    ):
        raise HTTPException(status_code=401, detail="Invalid metrics token")

    now = time.time()
    samples: list[tuple[str, dict[str, str], float]] = []

    try:
        redis = get_redis()
        worker_states = redis.hgetall(health_key())
    except Exception:
        log.exception("Metrics: could not read worker health")
        worker_states = {}
        samples.append(("sentient_metrics_scrape_ok", {}, 0.0))
    else:
        samples.append(("sentient_metrics_scrape_ok", {}, 1.0))

    for name, raw in (worker_states or {}).items():
        try:
            state = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        labels = {"worker": str(name)}
        heartbeat = float(state.get("last_heartbeat_epoch") or 0)
        age = now - heartbeat if heartbeat else float("inf")
        fresh = age <= WORKER_STALE_AFTER_SECONDS
        healthy = state.get("status") == "healthy"
        samples.append(("sentient_worker_up", labels, 1.0 if (fresh and healthy) else 0.0))
        if heartbeat:
            samples.append(
                ("sentient_worker_heartbeat_age_seconds", labels, round(age, 1))
            )
        # Emit every numeric field generically so new health counters surface
        # without code changes (messages_consumed, retries, errors, …).
        for key, value in state.items():
            if key == "last_heartbeat_epoch" or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                metric = f"sentient_worker_{_prom_metric_name(key)}"
                samples.append((metric, labels, float(value)))

    # Queue depths: alert on DLQ growth or a stuck/backed-up news stream.
    try:
        redis = get_redis()
        samples.append(
            ("sentient_stream_depth", {"stream": STREAM_KEY}, float(redis.xlen(STREAM_KEY)))
        )
        samples.append(
            (
                "sentient_dlq_depth",
                {"stream": AGENT_DLQ_STREAM_KEY},
                float(redis.xlen(AGENT_DLQ_STREAM_KEY)),
            )
        )
        samples.append(
            ("sentient_retry_queue_depth", {}, float(redis.zcard(AGENT_RETRY_ZSET_KEY)))
        )
    except Exception:
        log.debug("Metrics: could not read queue depths", exc_info=True)

    # LLM daily-call budget (kill-switch visibility).
    try:
        budget_limit = max(int(os.environ.get("LLM_DAILY_CALL_BUDGET", "0") or 0), 0)
    except ValueError:
        budget_limit = 0
    if budget_limit > 0:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        try:
            used = int(get_redis().get(f"{LLM_BUDGET_KEY_PREFIX}:{day}") or 0)
        except Exception:
            used = 0
        samples.append(("sentient_llm_budget_used", {}, float(used)))
        samples.append(("sentient_llm_budget_limit", {}, float(budget_limit)))
        samples.append(
            ("sentient_llm_budget_remaining", {}, float(max(budget_limit - used, 0)))
        )
        samples.append(
            ("sentient_llm_budget_exhausted", {}, 1.0 if used >= budget_limit else 0.0)
        )

    return PlainTextResponse(
        _render_prometheus(samples),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


def load_ticker_index() -> list[dict[str, str]]:
    """
    In-process cache of the tradable ticker directory built by ingestion.

    Reads the `sentient:ticker:meta` Redis hash once and keeps it in memory for
    TICKER_INDEX_TTL_SECONDS so search-as-you-type doesn't reparse thousands of
    rows on every keystroke.
    """
    global _ticker_index, _ticker_index_loaded_at
    now = time.time()
    if _ticker_index is not None and now - _ticker_index_loaded_at < TICKER_INDEX_TTL_SECONDS:
        return _ticker_index

    try:
        rows = get_redis().hgetall(TICKER_META_HASH_KEY)
    except Exception:
        # Keep serving the last good index if Redis hiccups; empty otherwise.
        return _ticker_index or []

    index: list[dict[str, str]] = []
    for raw in rows.values():
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        symbol = str(parsed.get("symbol") or "").upper()
        if not symbol or parsed.get("tradable") is False:
            continue
        index.append(
            {
                "symbol": symbol,
                "name": str(parsed.get("name") or ""),
                "exchange": str(parsed.get("exchange") or ""),
            }
        )
    index.sort(key=lambda row: row["symbol"])
    _ticker_index = index
    _ticker_index_loaded_at = now
    return index


@app.get("/tickers/search")
@limiter.limit(TICKER_SEARCH_RATE_LIMIT)
def ticker_search(
    request: Request,
    response: Response,
    q: str = Query(default="", max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    """Search the tradable ticker directory by symbol or company name."""
    index = load_ticker_index()
    query = q.strip()
    if not query:
        return {"results": index[:limit]}

    upper = query.upper()
    lower = query.lower()
    symbol_prefix: list[dict[str, str]] = []
    other: list[dict[str, str]] = []
    for row in index:
        symbol = row["symbol"]
        if symbol.startswith(upper):
            symbol_prefix.append(row)
        elif upper in symbol or lower in row["name"].lower():
            other.append(row)

    return {"results": (symbol_prefix + other)[:limit]}


@app.get("/simulate/quota")
def simulate_quota(user: UserInfo = Depends(require_user)) -> dict[str, Any]:
    """Current remaining simulations for the caller, without consuming one."""
    return peek_simulate_limit(user.id, user_tier(user))


def enforce_simulate_ip_ceiling(request: Request) -> None:
    """Per-IP hourly hard cap on /simulate, independent of the auth identity.

    Without this, the per-user quota in check_simulate_limit is bypassable by
    churning anonymous Supabase users (each a fresh user.id with its own quota),
    letting a single source trigger an unbounded number of LLM debates.
    """
    if SIMULATE_IP_HOURLY_LIMIT <= 0:
        return
    now_ms = int(time.time() * 1000)
    window_start = (now_ms // SIMULATE_IP_WINDOW_MS) * SIMULATE_IP_WINDOW_MS
    reset = window_start + SIMULATE_IP_WINDOW_MS
    ttl_ms = max(reset - now_ms, 1)
    key = f"ratelimit:simulate:ip:{window_start}:{rate_limit_digest(client_ip(request))}"

    count = int(get_redis().eval(FIXED_WINDOW_SCRIPT, 1, key, str(ttl_ms)))
    if count > SIMULATE_IP_HOURLY_LIMIT:
        retry_after = max((reset - now_ms) // 1000, 1)
        raise HTTPException(
            status_code=429,
            detail={
                "success": False,
                "errorMessage": (
                    "This network has hit the hourly simulation limit. "
                    "Please try again later."
                ),
                "retryAfterSeconds": retry_after,
            },
        )


@app.post("/simulate")
def simulate(
    request: Request,
    payload: SimulateRequest,
    user: UserInfo = Depends(require_user),
) -> dict[str, Any]:
    enforce_simulate_ip_ceiling(request)
    ticker, headline, source = validate_simulation(payload)
    rate_limit = check_simulate_limit(user.id, user_tier(user))
    if not rate_limit["success"]:
        raise HTTPException(status_code=429, detail=rate_limit)

    message = {
        "ticker": ticker,
        "headline": headline,
        "source": source,
        "published_at": now_iso(),
        "is_simulated": "true",
    }
    if payload.summary:
        message["summary"] = payload.summary.strip()
    if payload.article_url:
        message["article_url"] = payload.article_url.strip()

    entry_id = get_redis().xadd(
        STREAM_KEY, message, id="*", maxlen=STREAM_MAX_LEN, approximate=True
    )
    return {
        "success": True,
        "ticker": ticker,
        "headline": headline,
        "entry_id": entry_id,
        "remaining": rate_limit["remaining"],
    }


# ── Enhanced Features Observability ──────────────────────────────────────────


@app.get("/enhanced-features/audit")
def enhanced_features_audit(
    limit: int = Query(default=50, ge=1, le=200),
    user: UserInfo = Depends(require_user),
) -> dict[str, Any]:
    """
    Audit recent enhanced feature activations.

    Queries the decision_trace JSONB for the `enhanced_features` section
    that records what each feature did per signal.
    """
    try:
        result = (
            get_supabase()
            .table("trade_decision_traces")
            .select("trade_id, decision_trace")
            .not_.is_("decision_trace->enhanced_features", "null")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = result.data or []

        # Aggregate feature stats
        feature_stats: dict[str, dict[str, int]] = {}
        signals_with_features = 0

        for row in rows:
            trace = row.get("decision_trace") or {}
            ef = trace.get("enhanced_features")
            if not ef:
                continue
            signals_with_features += 1

            for activation in ef.get("activations", []):
                feature = activation.get("feature", "unknown")
                outcome = activation.get("outcome", "unknown")
                if feature not in feature_stats:
                    feature_stats[feature] = {}
                feature_stats[feature][outcome] = (
                    feature_stats[feature].get(outcome, 0) + 1
                )

        return {
            "signals_analyzed": len(rows),
            "signals_with_features": signals_with_features,
            "feature_stats": feature_stats,
            "recent_reports": [
                {
                    "trade_id": row.get("trade_id"),
                    "enhanced_features": (
                        row.get("decision_trace", {}).get("enhanced_features")
                    ),
                }
                for row in rows[:10]  # Last 10 detailed reports
            ],
        }
    except Exception:
        log.exception("Enhanced features audit failed")
        raise HTTPException(
            status_code=500, detail="Could not load enhanced feature audit."
        )
