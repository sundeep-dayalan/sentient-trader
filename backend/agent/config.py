"""
Agent Configuration
====================
Supabase is the single source of truth for all trading parameters.
Defaults live in the current Supabase schema baseline — not here.

reload_from_supabase() is called at startup and reload_from_supabase_if_stale()
is called before each signal. If Supabase is unreachable at startup, startup
fails loudly rather than silently running on stale values. Later refresh
failures keep the current in-memory config.

Secrets (API keys) are NOT here — those stay in .env.
"""

import json
import logging
import os
import socket
import time

log = logging.getLogger("agent.config")

# ── Infrastructure — deployment-level, not stored in Supabase ────────────────

# Used only when Groq's /models discovery is unavailable. Normal operation is
# auto-ranked from the live endpoint. Groq Always Free intentionally does not
# allow operator-selected model IDs; the router uses whichever free active model
# is most suitable at runtime.
GROQ_MODEL_DISCOVERY_FALLBACK: list[str] = [
    "openai/gpt-oss-120b",
    "qwen/qwen3-32b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]

GROQ_MODELS_URL = os.environ.get(
    "GROQ_MODELS_URL",
    "https://api.groq.com/openai/v1/models",
)
GROQ_MODEL_DISCOVERY_TIMEOUT = float(
    os.environ.get("GROQ_MODEL_DISCOVERY_TIMEOUT", "5")
)
GROQ_MIN_CONTEXT_WINDOW = int(os.environ.get("GROQ_MIN_CONTEXT_WINDOW", "8192"))
GROQ_MIN_COMPLETION_TOKENS = int(os.environ.get("GROQ_MIN_COMPLETION_TOKENS", "1024"))

STREAM_KEY = os.environ.get("REDIS_STREAM_KEY", "market-news")
CONSUMER_GROUP = os.environ.get("REDIS_CONSUMER_GROUP", "sentient-agent-group")
CONSUMER_NAME = os.environ.get("REDIS_CONSUMER_NAME") or f"agent-{socket.gethostname()}"
CONSUMER_START_ID = os.environ.get("REDIS_CONSUMER_START_ID", "0")
BATCH_SIZE = 10
POLL_INTERVAL = 1.0
ERROR_RETRY = 5.0
REDIS_QUOTA_RETRY = int(os.environ.get("REDIS_QUOTA_RETRY", "60"))

AGENT_MAX_TRADE_SIGNAL_AGE_SECONDS = int(
    os.environ.get("AGENT_MAX_TRADE_SIGNAL_AGE_SECONDS", str(15 * 60))
)
AGENT_MAX_AUDIT_SIGNAL_AGE_SECONDS = int(
    os.environ.get("AGENT_MAX_AUDIT_SIGNAL_AGE_SECONDS", str(24 * 60 * 60))
)
AGENT_MAX_PROCESSING_ATTEMPTS = int(
    os.environ.get("AGENT_MAX_PROCESSING_ATTEMPTS", "3")
)
AGENT_RETRY_BASE_DELAY_SECONDS = int(
    os.environ.get("AGENT_RETRY_BASE_DELAY_SECONDS", "30")
)
AGENT_RETRY_MAX_DELAY_SECONDS = int(
    os.environ.get("AGENT_RETRY_MAX_DELAY_SECONDS", str(5 * 60))
)
AGENT_RETRY_BATCH_SIZE = int(os.environ.get("AGENT_RETRY_BATCH_SIZE", "5"))
AGENT_PENDING_IDLE_SECONDS = int(os.environ.get("AGENT_PENDING_IDLE_SECONDS", "60"))
AGENT_PENDING_BATCH_SIZE = int(os.environ.get("AGENT_PENDING_BATCH_SIZE", "5"))
AGENT_CONFIG_REFRESH_SECONDS = float(
    os.environ.get("AGENT_CONFIG_REFRESH_SECONDS", "5")
)
AGENT_RETRY_ZSET_KEY = os.environ.get(
    "AGENT_RETRY_ZSET_KEY",
    f"{STREAM_KEY}:agent-retry",
)
AGENT_RETRY_HASH_KEY = os.environ.get(
    "AGENT_RETRY_HASH_KEY",
    f"{STREAM_KEY}:agent-retry-payloads",
)
AGENT_DLQ_STREAM_KEY = os.environ.get(
    "AGENT_DLQ_STREAM_KEY",
    f"{STREAM_KEY}:agent-dlq",
)

# ── Agent parameters — populated by reload_from_supabase() at startup ────────

BUY_SENTIMENT_THRESHOLD: float
SELL_SENTIMENT_THRESHOLD: float
CONFIDENCE_THRESHOLD: float
ORDER_QTY: int
LLM_PROVIDER_CONFIG: dict

MOMENTUM_SYSTEM_PROMPT: str
VALUE_SYSTEM_PROMPT: str
RISK_SYSTEM_PROMPT: str
SYNTHESIS_SYSTEM_PROMPT: str

# ── Enhanced trading features — all default OFF for backward compatibility ───
# These are read from agent_config.config JSON if present, with safe defaults.

# Dynamic position sizing: scale order qty with conviction/thesis quality
DYNAMIC_POSITION_SIZING_ENABLED: bool = False
MAX_POSITION_PCT: float = 0.05  # Max % of portfolio per trade

# Bracket orders: auto stop-loss + take-profit after BUY fills
BRACKET_ORDERS_ENABLED: bool = False
STOP_LOSS_PCT: float = 0.03  # 3% default
TAKE_PROFIT_PCT: float = 0.06  # 6% default

# Trailing stops: tighten stop-loss as position gains
TRAILING_STOPS_ENABLED: bool = False
TRAILING_STOP_PCT: float = 0.03
TRAILING_STOP_ACTIVATION_PCT: float = 0.02  # Activate once 2% in profit

# Portfolio concentration limits
CONCENTRATION_LIMITS_ENABLED: bool = False
MAX_SINGLE_TICKER_PCT: float = 0.10  # Max 10% per ticker

# Daily loss circuit breaker
CIRCUIT_BREAKER_ENABLED: bool = False
MAX_DAILY_LOSS_PCT: float = 0.02  # Pause trading after 2% daily loss

# Technical indicators in LLM debate context
TECHNICAL_INDICATORS_ENABLED: bool = False

# Signal momentum aggregation
SIGNAL_MOMENTUM_ENABLED: bool = False

# Source credibility weighting
SOURCE_CREDIBILITY_ENABLED: bool = False

# Historical outcome feedback loop
FEEDBACK_LOOP_ENABLED: bool = False
FEEDBACK_LOOP_LOOKBACK_DAYS: int = 30

# Limit orders instead of market orders
USE_LIMIT_ORDERS: bool = False
LIMIT_ORDER_BUFFER_PCT: float = 0.005  # 0.5% buffer

# Market hours awareness
MARKET_HOURS_AWARENESS_ENABLED: bool = False

# Structured synthesis framework
STRUCTURED_SYNTHESIS_ENABLED: bool = False

# Price-move freshness gate: block trades when the stock has already moved
# too much since the headline was first analyzed (chasing protection).
PRICE_MOVE_GATE_ENABLED: bool = False
MAX_PRICE_MOVE_PCT: float = 0.03  # 3% default — block if price moved >3%

# Price-confirmation co-signal: after the committee approves a BUY/SELL, require
# the intraday tape to confirm the direction on elevated volume before sending
# the order. Sets the LOWER bound of the entry band (tape must react); the
# price-move gate above enforces the UPPER (anti-chase) bound. Disabled →
# pure pass-through (no API calls, no added latency).
PRICE_CONFIRMATION_ENABLED: bool = False
CONFIRM_MIN_MOVE_PCT: float = 0.002        # ≥0.2% in-direction reaction required
CONFIRM_MAX_MOVE_PCT: float = 0.03         # don't confirm an already-overextended move
CONFIRM_MIN_VOLUME_RATIO: float = 1.2      # post-news vol ≥1.2× pre-news baseline
CONFIRM_LOOKBACK_MINUTES: int = 30         # pre-news baseline window (minutes)
CONFIRM_REQUIRE_DATA: bool = False         # strict: block when intraday data is missing

CONFIG_FINGERPRINT = ""
LAST_CONFIG_REFRESH_EPOCH = 0.0


def _fingerprint_config(row: dict) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def _safe_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _safe_float(value, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def reload_from_supabase() -> bool:
    global BUY_SENTIMENT_THRESHOLD, SELL_SENTIMENT_THRESHOLD, CONFIDENCE_THRESHOLD
    global ORDER_QTY, LLM_PROVIDER_CONFIG
    global MOMENTUM_SYSTEM_PROMPT, VALUE_SYSTEM_PROMPT, RISK_SYSTEM_PROMPT, SYNTHESIS_SYSTEM_PROMPT
    global CONFIG_FINGERPRINT, LAST_CONFIG_REFRESH_EPOCH
    # Enhanced trading features
    global DYNAMIC_POSITION_SIZING_ENABLED, MAX_POSITION_PCT
    global BRACKET_ORDERS_ENABLED, STOP_LOSS_PCT, TAKE_PROFIT_PCT
    global TRAILING_STOPS_ENABLED, TRAILING_STOP_PCT, TRAILING_STOP_ACTIVATION_PCT
    global CONCENTRATION_LIMITS_ENABLED, MAX_SINGLE_TICKER_PCT
    global CIRCUIT_BREAKER_ENABLED, MAX_DAILY_LOSS_PCT
    global TECHNICAL_INDICATORS_ENABLED, SIGNAL_MOMENTUM_ENABLED
    global SOURCE_CREDIBILITY_ENABLED
    global FEEDBACK_LOOP_ENABLED, FEEDBACK_LOOP_LOOKBACK_DAYS
    global USE_LIMIT_ORDERS, LIMIT_ORDER_BUFFER_PCT
    global MARKET_HOURS_AWARENESS_ENABLED, STRUCTURED_SYNTHESIS_ENABLED
    global PRICE_MOVE_GATE_ENABLED, MAX_PRICE_MOVE_PCT
    global PRICE_CONFIRMATION_ENABLED, CONFIRM_MIN_MOVE_PCT, CONFIRM_MAX_MOVE_PCT
    global CONFIRM_MIN_VOLUME_RATIO, CONFIRM_LOOKBACK_MINUTES, CONFIRM_REQUIRE_DATA

    from supabase import create_client
    from supabase.client import ClientOptions

    client = create_client(
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        options=ClientOptions(
            schema=os.environ.get("SUPABASE_DB_SCHEMA", "public"),
        ),
    )
    result = (
        client.table("agent_config").select("config").eq("id", 1).single().execute()
    )
    row: dict = result.data.get("config", {}) if result.data else {}
    if not row:
        raise RuntimeError(
            "agent_config table is empty - run supabase/migrations/001_current_schema.sql"
        )

    fingerprint = _fingerprint_config(row)
    changed = fingerprint != CONFIG_FINGERPRINT

    # Core parameters (required)
    BUY_SENTIMENT_THRESHOLD = float(row["buy_sentiment_threshold"])
    SELL_SENTIMENT_THRESHOLD = float(row["sell_sentiment_threshold"])
    CONFIDENCE_THRESHOLD = float(row["confidence_threshold"])
    ORDER_QTY = int(row["order_qty"])
    LLM_PROVIDER_CONFIG = row.get("llm_provider") or {"type": "groq-always-free"}

    MOMENTUM_SYSTEM_PROMPT = row["momentum_system_prompt"]
    VALUE_SYSTEM_PROMPT = row["value_system_prompt"]
    RISK_SYSTEM_PROMPT = row["risk_system_prompt"]
    SYNTHESIS_SYSTEM_PROMPT = row["synthesis_system_prompt"]

    # Enhanced features (optional — all default OFF for backward compatibility)
    enhanced = row.get("enhanced_trading") or {}
    DYNAMIC_POSITION_SIZING_ENABLED = _safe_bool(enhanced.get("dynamic_position_sizing"), False)
    MAX_POSITION_PCT = _safe_float(enhanced.get("max_position_pct"), 0.05)
    BRACKET_ORDERS_ENABLED = _safe_bool(enhanced.get("bracket_orders"), False)
    STOP_LOSS_PCT = _safe_float(enhanced.get("stop_loss_pct"), 0.03)
    TAKE_PROFIT_PCT = _safe_float(enhanced.get("take_profit_pct"), 0.06)
    TRAILING_STOPS_ENABLED = _safe_bool(enhanced.get("trailing_stops"), False)
    TRAILING_STOP_PCT = _safe_float(enhanced.get("trailing_stop_pct"), 0.03)
    TRAILING_STOP_ACTIVATION_PCT = _safe_float(enhanced.get("trailing_stop_activation_pct"), 0.02)
    CONCENTRATION_LIMITS_ENABLED = _safe_bool(enhanced.get("concentration_limits"), False)
    MAX_SINGLE_TICKER_PCT = _safe_float(enhanced.get("max_single_ticker_pct"), 0.10)
    CIRCUIT_BREAKER_ENABLED = _safe_bool(enhanced.get("circuit_breaker"), False)
    MAX_DAILY_LOSS_PCT = _safe_float(enhanced.get("max_daily_loss_pct"), 0.02)
    TECHNICAL_INDICATORS_ENABLED = _safe_bool(enhanced.get("technical_indicators"), False)
    SIGNAL_MOMENTUM_ENABLED = _safe_bool(enhanced.get("signal_momentum"), False)
    SOURCE_CREDIBILITY_ENABLED = _safe_bool(enhanced.get("source_credibility"), False)
    FEEDBACK_LOOP_ENABLED = _safe_bool(enhanced.get("feedback_loop"), False)
    FEEDBACK_LOOP_LOOKBACK_DAYS = _safe_int(enhanced.get("feedback_loop_lookback_days"), 30)
    USE_LIMIT_ORDERS = _safe_bool(enhanced.get("use_limit_orders"), False)
    LIMIT_ORDER_BUFFER_PCT = _safe_float(enhanced.get("limit_order_buffer_pct"), 0.005)
    MARKET_HOURS_AWARENESS_ENABLED = _safe_bool(enhanced.get("market_hours_awareness"), False)
    STRUCTURED_SYNTHESIS_ENABLED = _safe_bool(enhanced.get("structured_synthesis"), False)
    PRICE_MOVE_GATE_ENABLED = _safe_bool(enhanced.get("price_move_gate"), False)
    MAX_PRICE_MOVE_PCT = _safe_float(enhanced.get("max_price_move_pct"), 0.03)
    PRICE_CONFIRMATION_ENABLED = _safe_bool(enhanced.get("price_confirmation"), False)
    CONFIRM_MIN_MOVE_PCT = _safe_float(enhanced.get("confirm_min_move_pct"), 0.002)
    CONFIRM_MAX_MOVE_PCT = _safe_float(enhanced.get("confirm_max_move_pct"), 0.03)
    CONFIRM_MIN_VOLUME_RATIO = _safe_float(enhanced.get("confirm_min_volume_ratio"), 1.2)
    CONFIRM_LOOKBACK_MINUTES = _safe_int(enhanced.get("confirm_lookback_minutes"), 30)
    CONFIRM_REQUIRE_DATA = _safe_bool(enhanced.get("confirm_require_data"), False)

    CONFIG_FINGERPRINT = fingerprint
    LAST_CONFIG_REFRESH_EPOCH = time.time()

    if changed:
        log.info(
            "Config loaded - buy=%.2f  sell=%.2f  confidence=%.2f  qty=%d  llm_provider=%s",
            BUY_SENTIMENT_THRESHOLD,
            SELL_SENTIMENT_THRESHOLD,
            CONFIDENCE_THRESHOLD,
            ORDER_QTY,
            LLM_PROVIDER_CONFIG.get("type", "groq-always-free"),
        )
        active_features = [name for name, enabled in [
            ("dynamic_sizing", DYNAMIC_POSITION_SIZING_ENABLED),
            ("bracket_orders", BRACKET_ORDERS_ENABLED),
            ("trailing_stops", TRAILING_STOPS_ENABLED),
            ("concentration", CONCENTRATION_LIMITS_ENABLED),
            ("circuit_breaker", CIRCUIT_BREAKER_ENABLED),
            ("technicals", TECHNICAL_INDICATORS_ENABLED),
            ("momentum", SIGNAL_MOMENTUM_ENABLED),
            ("source_cred", SOURCE_CREDIBILITY_ENABLED),
            ("feedback", FEEDBACK_LOOP_ENABLED),
            ("limit_orders", USE_LIMIT_ORDERS),
            ("market_hours", MARKET_HOURS_AWARENESS_ENABLED),
            ("structured_synth", STRUCTURED_SYNTHESIS_ENABLED),
            ("price_move_gate", PRICE_MOVE_GATE_ENABLED),
            ("price_confirmation", PRICE_CONFIRMATION_ENABLED),
        ] if enabled]
        if active_features:
            log.info("Enhanced features active: %s", ", ".join(active_features))
    else:
        log.debug("Config checked - unchanged")

    return changed


def reload_from_supabase_if_stale(*, force: bool = False) -> bool:
    if (
        not force
        and LAST_CONFIG_REFRESH_EPOCH
        and time.time() - LAST_CONFIG_REFRESH_EPOCH < AGENT_CONFIG_REFRESH_SECONDS
    ):
        return False
    return reload_from_supabase()
