"""
Sentient Trader — Ingestion Service
=====================================
Entry point for the market news ingestion pipeline.

This service runs forever as a background worker:
  1. Streams real-time news from Alpaca's WebSockets
  2. Filters incoming headlines for relevant stock tickers
  3. Publishes filtered news to a Redis Stream for downstream processing

The ingestion service is intentionally decoupled from the AI agent.
If the agent goes down, this service keeps buffering news in Redis —
no headlines are lost. This is the "severed pipeline" design.

Run locally:  python main.py
Deploy:       build and run the Dockerfile
"""

import logging
import sys

from dotenv import load_dotenv

from listener import NewsListener

# Load .env file when running locally. In production, secrets are injected
# directly into the environment so this is a no-op.
load_dotenv()

# Structured logging so the host's log aggregator can parse and search entries.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)

log = logging.getLogger("ingestion.main")


def main() -> None:
    log.info("Sentient Trader ingestion service starting...")

    listener = NewsListener()

    # .run() blocks forever — the websocket loop stays active.
    # The host should restart this process automatically if it crashes.
    listener.run()


if __name__ == "__main__":
    main()
