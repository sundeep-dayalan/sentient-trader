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
  - LLM inference:      provider-dependent
  - Alpaca order:       ~150ms  (only when a trade is triggered)
  - Supabase insert:    ~100ms

Run locally:  python main.py
Deploy:       build and run the Dockerfile
"""

import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv, find_dotenv

sys.path.append(str(Path(__file__).resolve().parents[1]))

# Load environment variables first, before importing config.
# override=False: real environment variables (set by the container platform)
# always win over .env files, so a stray .env baked into an image can never
# silently replace production credentials. Locally, .env still fills in
# anything the shell doesn't set.
dotenv_path = find_dotenv()
if dotenv_path:
    load_dotenv(dotenv_path, override=False)
else:
    load_dotenv(override=False)

import config
from analyst import build_agent_graph
from cache import HeadlineCache
from consumer import RedisStreamConsumer
from logger import SupabaseLogger
from outcome_scheduler import (
    default_scheduler_run_tracker,
    start_outcome_labeler_scheduler,
)
from position_monitor import start_position_monitor
from redis_client import create_redis_client
from schemas import NewsMessage
from shared.logging_setup import setup_logging
from shared.singleton_lock import RedisLeaderLock
from trader import AlpacaTrader

# Set up logging first so any startup errors are visible in container logs.
# LOG_FORMAT=json emits structured lines with signal_id correlation fields.
setup_logging("agent")

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

    # Singleton side-loops hold Redis leader locks so running more than one
    # agent replica never double-manages stops or double-runs the labeler.
    lock_redis = create_redis_client()
    scheduler = start_outcome_labeler_scheduler(
        run_tracker=default_scheduler_run_tracker(),
        lock=RedisLeaderLock(lock_redis, "outcome-labeler"),
    )
    start_position_monitor(
        trader,
        lock=RedisLeaderLock(lock_redis, "position-monitor"),
        health_redis=lock_redis,
    )

    def process_news(news: NewsMessage) -> None:
        """Run one news article through the full agent graph."""
        processing_started_at = datetime.now(timezone.utc).isoformat()
        try:
            if config.reload_from_supabase_if_stale():
                log.info("Agent config hot-reloaded before processing %s", news.ticker)
        except Exception as exc:
            log.warning(
                "Could not refresh agent config; using current in-memory config: %s",
                exc,
            )

        initial_state = {
            "news": news,
            "is_cached": False,
            "market_context": None,
            "article_quality": None,
            "all_positions": None,
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
            "processing_started_at": processing_started_at,
        }
        graph.invoke(initial_state)

    def log_expired_news(news: NewsMessage, metadata: dict[str, Any]) -> None:
        """Record stale news as an explicit HOLD without spending LLM budget."""
        processing_started_at = datetime.now(timezone.utc).isoformat()
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
                "processing_started_at": processing_started_at,
                "processing_finished_at": datetime.now(timezone.utc).isoformat(),
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
        cache.mark_seen(news.headline, ticker=news.ticker, article_id=news.article_id)

    # Start consuming — blocks until a shutdown signal arrives
    log.info("Agent ready. Waiting for market news from Redis...")
    consumer = RedisStreamConsumer()

    def _handle_shutdown(signum, _frame) -> None:
        # Finish the in-flight entry (process + ACK) before exiting so a
        # rolling deploy never abandons a half-processed message.
        log.info("Received %s — draining and shutting down", signal.Signals(signum).name)
        consumer.request_stop()
        if scheduler is not None:
            scheduler.stop_event.set()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    consumer.start(on_message=process_news, on_expired=log_expired_news)
    log.info("Agent service exited cleanly")


if __name__ == "__main__":
    main()
