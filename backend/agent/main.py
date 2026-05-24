"""
Sentient Trader — Agent Service
=================================
Entry point for the AI trading agent.

This service runs forever as a background worker:
  1. Consumes news messages from a Redis Stream
  2. Runs each headline through the LangGraph pipeline:
       check_cache → fetch_context → momentum_analyst → value_analyst
       → risk_analyst → synthesizer → assess_risk → [trade] → log

The full round-trip per headline is typically 300-600ms:
  - Redis cache check:  ~10ms
  - Groq inference:     ~250ms
  - Alpaca order:       ~150ms  (only when a trade is triggered)
  - Supabase insert:    ~100ms

Run locally:  python main.py
Deploy:       build and run the Dockerfile
"""

import logging
import sys
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

# Load environment variables first, before importing config
load_dotenv()

import config
from analyst import build_agent_graph
from cache import HeadlineCache
from consumer import RedisStreamConsumer
from logger import SupabaseLogger
from schemas import NewsMessage
from trader import AlpacaTrader

# Set up logging first so any startup errors are visible in Fly logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

config.reload_from_supabase()

log = logging.getLogger("agent.main")


def main() -> None:
    log.info("Sentient Trader agent service starting...")

    # Initialize all dependencies once at startup — not on every message
    cache = HeadlineCache()
    trader = AlpacaTrader()
    db = SupabaseLogger()

    # Compile the LangGraph state machine once — reused for every message
    graph = build_agent_graph(cache=cache, trader=trader, db=db)

    def process_news(news: NewsMessage) -> None:
        """Run one news article through the full agent graph."""
        initial_state = {
            "news": news,
            "is_cached": False,
            "market_context": None,
            "article_quality": None,
            "momentum_opinion": None,
            "value_opinion": None,
            "risk_opinion": None,
            "momentum_model": None,
            "value_model": None,
            "risk_model": None,
            "llm_operations": [],
            "analysis": None,
            "should_trade": False,
            "risk_gate": None,
            "execution_plan": None,
            "trade_order_id": None,
            "execution": None,
            "error": None,
            "is_simulated": news.is_simulated,
        }
        graph.invoke(initial_state)

    def log_expired_news(news: NewsMessage, metadata: dict[str, Any]) -> None:
        """Record stale news as an explicit HOLD without spending LLM budget."""
        age = float(metadata.get("age_seconds") or 0)
        reason = (
            f"Signal expired before analysis. Article age was {int(age)}s, "
            f"above the {metadata.get('max_trade_age_seconds')}s trading window."
        )
        db.log_trade(
            ticker=news.ticker,
            headline=news.headline,
            sentiment_score=0.0,
            confidence_score=0.0,
            reasoning=reason,
            trade_action="HOLD",
            is_simulated=news.is_simulated,
            article_source=news.source,
            article_url=news.article_url,
            article_id=news.article_id,
            decision_trace={
                "schema_version": 2,
                "pipeline": "decision_core",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "news": news.model_dump(),
                "market_context": None,
                "article_quality": None,
                "llm_operations": [],
                "committee_debate": [],
                "portfolio_manager_decision": None,
                "risk_gate": {
                    "step": "freshness_gate",
                    "should_trade": False,
                    "reason": reason,
                    "metadata": metadata,
                },
                "execution": {
                    "step": "execute_trade",
                    "submitted": False,
                    "ticker": news.ticker,
                    "action": "HOLD",
                    "quantity": 0,
                    "order_id": None,
                    "reason": "No Alpaca order submitted for expired signal.",
                },
                "error": "expired_signal",
            },
        )
        cache.mark_seen(news.headline)

    # Start consuming — blocks forever
    log.info("Agent ready. Waiting for market news from Redis...")
    consumer = RedisStreamConsumer()
    consumer.start(on_message=process_news, on_expired=log_expired_news)


if __name__ == "__main__":
    main()
