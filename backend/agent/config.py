"""
Agent Configuration
====================
Supabase is the single source of truth for all tunable parameters.
Defaults live in migration 004 — not here.

reload_from_supabase() is called once at startup (main.py) and populates
all module-level variables below. If Supabase is unreachable, startup fails
loudly rather than silently running on stale values.

Secrets (API keys) are NOT here — those stay in .env.
"""

import logging
import os

log = logging.getLogger("agent.config")

# ── Infrastructure — deployment-level, not stored in Supabase ────────────────

# Model selection policy.
#
# By default the ModelRouter discovers Groq's active models at startup and ranks
# them with a local policy: active + text/chat-shaped + enough context, excluding
# audio, guard, safeguard, TTS, and compound/agentic systems. If you need to force
# a preference order, set GROQ_MODEL_PINNED_ORDER to a comma-separated list; those
# models are tried first when active, and auto-ranked models fill in after them.
GROQ_MODEL_PINNED_ORDER: list[str] = [
    model.strip()
    for model in os.environ.get("GROQ_MODEL_PINNED_ORDER", "").split(",")
    if model.strip()
]

# Used only when Groq's /models discovery is unavailable. Normal operation is
# auto-ranked from the live endpoint; this keeps the agent functional if model
# discovery is temporarily forbidden/down.
GROQ_MODEL_DISCOVERY_FALLBACK: list[str] = [
    "openai/gpt-oss-120b",
    "qwen/qwen3-32b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]

# Backward-compatible alias used by older imports/tests. Empty means "auto-rank".
MODEL_CASCADE: list[str] = GROQ_MODEL_PINNED_ORDER

GROQ_MODELS_URL = os.environ.get(
    "GROQ_MODELS_URL",
    "https://api.groq.com/openai/v1/models",
)
GROQ_MODEL_DISCOVERY_TIMEOUT = float(os.environ.get("GROQ_MODEL_DISCOVERY_TIMEOUT", "5"))
GROQ_MIN_CONTEXT_WINDOW = int(os.environ.get("GROQ_MIN_CONTEXT_WINDOW", "8192"))
GROQ_MIN_COMPLETION_TOKENS = int(os.environ.get("GROQ_MIN_COMPLETION_TOKENS", "1024"))

STREAM_KEY     = os.environ.get("REDIS_STREAM_KEY", "market-news")
CONSUMER_GROUP = "sentient-agent-group"
CONSUMER_NAME  = "agent-worker-1"
BATCH_SIZE     = 10
POLL_INTERVAL  = 1.0
ERROR_RETRY    = 5.0
REDIS_QUOTA_RETRY = int(os.environ.get("REDIS_QUOTA_RETRY", "3600"))

# ── Agent parameters — populated by reload_from_supabase() at startup ────────

BUY_SENTIMENT_THRESHOLD:  float
SELL_SENTIMENT_THRESHOLD: float
CONFIDENCE_THRESHOLD:     float
ORDER_QTY:                int
MODEL_OVERRIDE:           str | None

MOMENTUM_SYSTEM_PROMPT:  str
VALUE_SYSTEM_PROMPT:     str
RISK_SYSTEM_PROMPT:      str
SYNTHESIS_SYSTEM_PROMPT: str


def reload_from_supabase() -> None:
    global BUY_SENTIMENT_THRESHOLD, SELL_SENTIMENT_THRESHOLD, CONFIDENCE_THRESHOLD
    global ORDER_QTY, MODEL_OVERRIDE
    global MOMENTUM_SYSTEM_PROMPT, VALUE_SYSTEM_PROMPT, RISK_SYSTEM_PROMPT, SYNTHESIS_SYSTEM_PROMPT

    from supabase import create_client
    client = create_client(
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    result = (
        client.table("agent_config")
        .select("config")
        .eq("id", 1)
        .single()
        .execute()
    )
    row: dict = result.data.get("config", {}) if result.data else {}
    if not row:
        raise RuntimeError("agent_config table is empty — run migration 004")

    BUY_SENTIMENT_THRESHOLD  = float(row["buy_sentiment_threshold"])
    SELL_SENTIMENT_THRESHOLD = float(row["sell_sentiment_threshold"])
    CONFIDENCE_THRESHOLD     = float(row["confidence_threshold"])
    ORDER_QTY                = int(row["order_qty"])
    MODEL_OVERRIDE           = row.get("model_override") or None

    MOMENTUM_SYSTEM_PROMPT  = row["momentum_system_prompt"]
    VALUE_SYSTEM_PROMPT     = row["value_system_prompt"]
    RISK_SYSTEM_PROMPT      = row["risk_system_prompt"]
    SYNTHESIS_SYSTEM_PROMPT = row["synthesis_system_prompt"]

    log.info(
        "Config loaded — buy=%.2f  sell=%.2f  confidence=%.2f  qty=%d  model=%s",
        BUY_SENTIMENT_THRESHOLD, SELL_SENTIMENT_THRESHOLD,
        CONFIDENCE_THRESHOLD, ORDER_QTY, MODEL_OVERRIDE or "cascade",
    )
