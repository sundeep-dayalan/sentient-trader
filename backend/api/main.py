"""
Sentient Trader backend API.

This service runs inside Oracle Cloud near private Valkey/Redis. The React
frontend calls this API over HTTPS with the user's Supabase access token.
All database, Alpaca, LLM-provider, Redis, and admin operations live here.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from dotenv import load_dotenv, find_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client
from supabase.client import ClientOptions

sys.path.append(str(Path(__file__).resolve().parents[1]))

from redis_client import create_redis_client
from shared.worker_health import health_key, read_worker_state, worker_name

# Try to find a root/parent .env file first for local development.
# If not found (e.g. in production/Docker), it will safely fallback.
dotenv_path = find_dotenv()
if dotenv_path:
    load_dotenv(dotenv_path, override=True)
else:
    load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("backend.api")

app = FastAPI(title="Sentient Trader Backend API")

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
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

ServiceStatus = Literal["ok", "stale", "error", "unknown"]
UserTier = Literal["anonymous", "social", "super"]

ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
GROQ_MODELS_URL = os.environ.get(
    "GROQ_MODELS_URL", "https://api.groq.com/openai/v1/models"
)
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
STREAM_KEY = os.environ.get("REDIS_STREAM_KEY", "market-news")
STREAM_MAX_LEN = int(os.environ.get("REDIS_STREAM_MAX_LEN", "1000"))
PAGE_SIZE = 20
MAX_ORDER_IDS = 50
MAX_PROMPT_LENGTH = 5000

TRADE_SUMMARY_SELECT = (
    "id, created_at, ticker, headline, article_url, sentiment_score, "
    "confidence_score, trade_action, order_id, quantity, is_simulated"
)
LEGACY_TRADE_DETAIL_SELECT = (
    f"{TRADE_SUMMARY_SELECT}, reasoning, article_source, article_id, decision_trace"
)
TRACE_DETAIL_SELECT = "decision_trace, reasoning, article_source, article_id"
TRADE_STATS_SELECT = "trade_action, order_id, sentiment_score"

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I
)
TICKER_REGEX = re.compile(r"^[A-Z]{1,6}$")
ALLOWED_ORDER_STATUSES = {"open", "closed", "all"}
RANGE_CONFIG = {
    "D": {"period": "1D", "timeframe": "5Min"},
    "W": {"period": "1W", "timeframe": "1H"},
    "M": {"period": "1M", "timeframe": "1D"},
    "3M": {"period": "3M", "timeframe": "1D"},
    "6M": {"period": "6M", "timeframe": "1D"},
    "Y": {"period": "1A", "timeframe": "1D"},
    "5Y": {"period": "5A", "timeframe": "1D"},
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


def get_user_from_token(token: str) -> UserInfo:
    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    anon_key = get_supabase_anon_key()
    if not anon_key:
        raise HTTPException(
            status_code=500, detail="SUPABASE_ANON_KEY is not configured"
        )

    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(
                f"{supabase_url}/auth/v1/user",
                headers={
                    "apikey": anon_key,
                    "Authorization": f"Bearer {token}",
                },
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


def get_optional_user(
    authorization: str | None = Header(default=None),
) -> UserInfo | None:
    token = auth_token(authorization)
    if not token:
        return None
    return get_user_from_token(token)


def require_user(authorization: str | None = Header(default=None)) -> UserInfo:
    token = auth_token(authorization)
    if not token:
        raise HTTPException(
            status_code=401, detail="Authentication required. Please sign in."
        )
    return get_user_from_token(token)


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
    return f"{prefix}: {str(error)[:240]}"


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


def is_risk_gated(row: dict[str, Any]) -> bool:
    trace = row.get("decision_trace")
    if isinstance(trace, dict):
        risk_gate = trace.get("risk_gate")
        if (
            isinstance(risk_gate, dict)
            and risk_gate.get("should_trade") is False
            and not str(row.get("order_id") or "").strip()
        ):
            return True
    return row.get("trade_action") == "HOLD"


def compute_dashboard_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sentiment_total = 0.0
    buy_orders = sell_orders = risk_gated = executed = 0
    for row in rows:
        if row.get("trade_action") == "BUY":
            buy_orders += 1
        if row.get("trade_action") == "SELL":
            sell_orders += 1
        if is_risk_gated(row):
            risk_gated += 1
        if str(row.get("order_id") or "").strip():
            executed += 1
        sentiment_total += number_value(row.get("sentiment_score")) or 0

    return {
        "analyzed": len(rows),
        "executed": executed,
        "buyOrders": buy_orders,
        "sellOrders": sell_orders,
        "riskGated": risk_gated,
        "avgSentiment": sentiment_total / len(rows) if rows else 0,
    }


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


def alpaca_fetch(path: str) -> Any:
    with httpx.Client(timeout=15) as client:
        response = client.get(f"{ALPACA_BASE_URL}{path}", headers=alpaca_headers())
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


def validate_simulation(payload: SimulateRequest) -> tuple[str, str, str]:
    ticker = payload.ticker.strip().upper()
    headline = payload.headline.strip()
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
    if payload.article_url and len(payload.article_url) > 2048:
        raise HTTPException(
            status_code=400, detail="Article URL must be 2048 characters or fewer."
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


def normalize_llm_provider_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        return default_llm_provider_config()

    provider_type = str(raw.get("type") or "groq-always-free").strip().lower()
    if provider_type == "groq-always-free":
        return default_llm_provider_config()
    if provider_type != "openrouter":
        raise HTTPException(
            status_code=400,
            detail="llm_provider.type must be groq-always-free or openrouter",
        )

    base_url = str(raw.get("base_url") or DEFAULT_OPENROUTER_BASE_URL).strip()
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="llm_provider.base_url must be a URL")

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
            raise HTTPException(status_code=400, detail="model priority must be an integer")
        if priority < 1 or priority in seen_priorities:
            raise HTTPException(
                status_code=400, detail="model priorities must be unique positive integers"
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
        "type": "openrouter",
        "base_url": base_url.rstrip("/"),
        "routing": {
            "strategy": "ordered_fallback",
            "max_wait_seconds": positive_float("max_wait_seconds", 600),
            "default_cooldown_seconds": positive_float(
                "default_cooldown_seconds", 60
            ),
            "key_status_check_interval_seconds": positive_float(
                "key_status_check_interval_seconds", 300
            ),
        },
        "models": models,
    }


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
def trades(before: str | None = None, after: str | None = None) -> dict[str, Any]:
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
def trade_detail(trade_id: str) -> dict[str, Any]:
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


@app.get("/stats")
def stats() -> dict[str, Any]:
    result = (
        get_supabase().table("trades").select(TRADE_STATS_SELECT).limit(10000).execute()
    )
    return {"stats": compute_dashboard_stats(result.data or []), "fetchedAt": now_iso()}


@app.get("/agent-config")
def agent_config() -> dict[str, Any]:
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
            "momentum": row.get("momentum_system_prompt"),
            "value": row.get("value_system_prompt"),
            "risk": row.get("risk_system_prompt"),
            "synthesis": row.get("synthesis_system_prompt"),
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
def orders(
    status: str = "all", limit: int = Query(default=100, ge=1, le=500)
) -> dict[str, Any]:
    try:
        safe_status = status if status in ALLOWED_ORDER_STATUSES else "all"
        query = f"status={safe_status}&limit={limit}&direction=desc&nested=true"
        account, positions, order_rows = (
            alpaca_fetch("/v2/account"),
            alpaca_fetch("/v2/positions"),
            alpaca_fetch(f"/v2/orders?{query}"),
        )
        account.pop("account_number", None)
        return {
            "account": account,
            "positions": positions,
            "orders": order_rows,
            "fetchedAt": now_iso(),
        }
    except Exception as exc:
        log.exception("Orders fetch error")
        return {
            "account": None,
            "positions": [],
            "orders": [],
            "fetchedAt": now_iso(),
            "error": str(exc),
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
def portfolio(range: str = "D") -> dict[str, Any]:
    try:
        range_key = range if range in RANGE_CONFIG else "D"
        config = RANGE_CONFIG[range_key]
        query = f"period={config['period']}&timeframe={config['timeframe']}&intraday_reporting=extended_hours"
        data, account = alpaca_fetch(
            f"/v2/account/portfolio/history?{query}"
        ), alpaca_fetch("/v2/account")

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

        history = []
        for index, ts in enumerate(timestamps):
            equity = number_value(equities[index] if index < len(equities) else None)
            if equity is not None:
                history.append(
                    {
                        "timestamp": datetime.fromtimestamp(ts, timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "equity": equity,
                    }
                )

        live_equity = number_value(
            account.get("portfolio_value") or account.get("equity")
        )
        latest = history[-1] if history else None
        if live_equity and live_equity > 0:
            latest_age_ms = (
                time.time() * 1000
                - datetime.fromisoformat(
                    latest["timestamp"].replace("Z", "+00:00")
                ).timestamp()
                * 1000
                if latest
                else float("inf")
            )
            value_changed = (
                abs(latest["equity"] - live_equity) >= 0.005 if latest else True
            )
            if not latest or latest_age_ms > 45_000 or value_changed:
                history.append({"timestamp": now_iso(), "equity": live_equity})

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
                "id": string_value(account.get("id")),
                "status": string_value(account.get("status")),
                "currency": string_value(account.get("currency")),
                "createdAt": string_value(account.get("created_at")),
                "paper": True,
            },
            "range": range_key,
            "source": "alpaca",
            "fetchedAt": now_iso(),
        }
    except Exception as exc:
        log.exception("Portfolio fetch error")
        return {"history": [], "error": str(exc), "fetchedAt": now_iso()}


@app.get("/status")
def status() -> dict[str, Any]:
    details: dict[str, str] = {}

    try:
        with httpx.Client(timeout=5) as client:
            alpaca_res = client.get(
                f"{ALPACA_BASE_URL}/v2/clock", headers=alpaca_headers()
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
        data = (
            get_supabase()
            .table("trades")
            .select("created_at")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
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
        redis = get_redis()
        agent_worker_name = worker_name("agent")
        agent_state = read_worker_state(redis, agent_worker_name)
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
                    f"Worker '{agent_worker_name}' heartbeat is stale by "
                    f"{int(heartbeat_age // 60)} minutes."
                )
            elif worker_status == "unhealthy":
                agent_status = "error"
                details["agent"] = (
                    f"Worker '{agent_worker_name}' is unhealthy: "
                    f"{phase}{phase_detail}."
                )
            elif heartbeat_age < 60 and worker_status == "healthy":
                agent_status = "ok"
                details["agent"] = (
                    f"Worker '{agent_worker_name}' is fresh: {phase}{phase_detail}."
                )
            else:
                agent_status = "stale"
                details["agent"] = (
                    f"Worker '{agent_worker_name}' is fresh but status is "
                    f"{worker_status}, phase is {phase}{phase_detail}."
                    if phase
                    else f"Worker '{agent_worker_name}' is fresh, but no phase "
                    "state was published."
                )
        else:
            agent_status = "error"
            details["agent"] = (
                f"No agent worker state found for '{agent_worker_name}' in Redis key '{health_key()}'."
            )
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


@app.post("/simulate")
def simulate(
    payload: SimulateRequest, user: UserInfo = Depends(require_user)
) -> dict[str, Any]:
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
