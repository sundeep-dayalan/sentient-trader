"""
Agent Configuration
====================
Supabase is the single source of truth for all trading parameters.
Defaults live in the current Supabase schema baseline — not here.

reload_from_supabase() is called once at startup (main.py) and populates
all module-level variables below. If Supabase is unreachable, startup fails
loudly rather than silently running on stale values.

Secrets (API keys) are NOT here — those stay in .env.
"""

import logging
import os
import socket

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


def reload_from_supabase() -> None:
    global BUY_SENTIMENT_THRESHOLD, SELL_SENTIMENT_THRESHOLD, CONFIDENCE_THRESHOLD
    global ORDER_QTY, LLM_PROVIDER_CONFIG
    global MOMENTUM_SYSTEM_PROMPT, VALUE_SYSTEM_PROMPT, RISK_SYSTEM_PROMPT, SYNTHESIS_SYSTEM_PROMPT

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
            "agent_config table is empty — run supabase/migrations/001_current_schema.sql"
        )

    BUY_SENTIMENT_THRESHOLD = float(row["buy_sentiment_threshold"])
    SELL_SENTIMENT_THRESHOLD = float(row["sell_sentiment_threshold"])
    CONFIDENCE_THRESHOLD = float(row["confidence_threshold"])
    ORDER_QTY = int(row["order_qty"])
    LLM_PROVIDER_CONFIG = row.get("llm_provider") or {"type": "groq-always-free"}

    MOMENTUM_SYSTEM_PROMPT = row["momentum_system_prompt"]
    VALUE_SYSTEM_PROMPT = row["value_system_prompt"]
    RISK_SYSTEM_PROMPT = row["risk_system_prompt"]
    SYNTHESIS_SYSTEM_PROMPT = row["synthesis_system_prompt"]

    log.info(
        "Config loaded — buy=%.2f  sell=%.2f  confidence=%.2f  qty=%d  llm_provider=%s",
        BUY_SENTIMENT_THRESHOLD,
        SELL_SENTIMENT_THRESHOLD,
        CONFIDENCE_THRESHOLD,
        ORDER_QTY,
        LLM_PROVIDER_CONFIG.get("type", "groq-always-free"),
    )
