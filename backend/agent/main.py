"""
Sentient Trader — Agent Service
=================================
Entry point for the AI trading agent.

This service runs forever on Fly.io as a background worker:
  1. Consumes news messages from Kafka (put there by the ingestion service)
  2. Runs each headline through the LangGraph pipeline:
       check_cache → analyze (Groq LLaMA) → assess_risk → [trade] → log

The full round-trip per headline is typically 300-600ms:
  - Redis cache check:  ~10ms
  - Groq inference:     ~250ms
  - Alpaca order:       ~150ms  (only when a trade is triggered)
  - Supabase insert:    ~100ms

Run locally:  python main.py
Deploy:       flyctl deploy  (from this directory)
"""

import logging
import sys

from dotenv import load_dotenv

from analyst import build_agent_graph
from cache import HeadlineCache
from consumer import RedisStreamConsumer
from logger import SupabaseLogger
from schemas import NewsMessage
from trader import AlpacaTrader

load_dotenv()

# Suppress httpx's per-request INFO logs — they flood the output during polling
logging.getLogger("httpx").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)

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
            "analysis": None,
            "should_trade": False,
            "trade_order_id": None,
            "error": None,
        }
        try:
            graph.invoke(initial_state)
        except Exception as e:
            # Catch-all so one bad message never kills the consumer loop
            log.error("Graph invocation failed for [%s]: %s", news.ticker, e)

    # Start consuming — blocks forever
    log.info("Agent ready. Waiting for market news from Kafka...")
    consumer = RedisStreamConsumer()
    consumer.start(on_message=process_news)


if __name__ == "__main__":
    main()
