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

MODEL_CASCADE: list[str] = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

STREAM_KEY     = os.environ.get("REDIS_STREAM_KEY", "market-news")
CONSUMER_GROUP = "sentient-agent-group"
CONSUMER_NAME  = "agent-worker-1"
BATCH_SIZE     = 10
POLL_INTERVAL  = 1.0
ERROR_RETRY    = 5.0

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
