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
from pathlib import Path

# backend/ must be importable before listener (whose import chain reaches
# shared.worker_health) is loaded.
sys.path.append(str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv, find_dotenv

from listener import NewsListener
from shared.logging_setup import setup_logging

# Load .env file when running locally. In production, secrets are injected
# directly into the environment so this is a no-op.
# override=False: real environment variables always win over .env files, so a
# stray .env baked into an image can never silently replace production values.
dotenv_path = find_dotenv()
if dotenv_path:
    load_dotenv(dotenv_path, override=False)
else:
    load_dotenv(override=False)

# Structured logging so the host's log aggregator can parse and search entries.
# LOG_FORMAT=json emits one JSON object per line; default stays human-readable.
setup_logging("ingestion")

log = logging.getLogger("ingestion.main")


def main() -> None:
    log.info("Sentient Trader ingestion service starting...")

    listener = NewsListener()

    # .run() blocks forever — the websocket loop stays active.
    # The host should restart this process automatically if it crashes.
    listener.run()


if __name__ == "__main__":
    main()
