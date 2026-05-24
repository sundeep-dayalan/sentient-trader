"""
Runtime configuration for the ingestion service.
"""

from __future__ import annotations

import os


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


PROVIDER = os.environ.get("INGESTION_PROVIDER", "alpaca")
WORKER_HEALTH_KEY = os.environ.get("WORKER_HEALTH_KEY", "sentient:workers:health")
WORKER_NAME = os.environ.get("INGESTION_WORKER_NAME", "ingestion")

ALPACA_NEWS_BASE_URL = os.environ.get(
    "ALPACA_NEWS_BASE_URL",
    "https://data.alpaca.markets",
).rstrip("/")

BACKFILL_STARTUP_LOOKBACK_SECONDS = env_int(
    "INGESTION_STARTUP_BACKFILL_SECONDS",
    15 * 60,
)
BACKFILL_RECONNECT_LOOKBACK_SECONDS = env_int(
    "INGESTION_RECONNECT_BACKFILL_SECONDS",
    5 * 60,
)
BACKFILL_HTTP_TIMEOUT_SECONDS = env_float("INGESTION_BACKFILL_HTTP_TIMEOUT_SECONDS", 10.0)
BACKFILL_PAGE_LIMIT = env_int("INGESTION_BACKFILL_PAGE_LIMIT", 50)
BACKFILL_MAX_PAGES = env_int("INGESTION_BACKFILL_MAX_PAGES", 20)

DEDUPE_HEADLINE_WINDOW_SECONDS = env_int(
    "INGESTION_DEDUPE_HEADLINE_WINDOW_SECONDS",
    6 * 60 * 60,
)

OUTBOX_BATCH_SIZE = env_int("INGESTION_OUTBOX_BATCH_SIZE", 25)
OUTBOX_RETRY_INTERVAL_SECONDS = env_float("INGESTION_OUTBOX_RETRY_INTERVAL_SECONDS", 10.0)
OUTBOX_MAX_RETRY_DELAY_SECONDS = env_int("INGESTION_OUTBOX_MAX_RETRY_DELAY_SECONDS", 5 * 60)

STREAM_RECONNECT_MAX_DELAY_SECONDS = env_int(
    "INGESTION_STREAM_RECONNECT_MAX_DELAY_SECONDS",
    60,
)

HEARTBEAT_INTERVAL_SECONDS = env_float("INGESTION_HEARTBEAT_INTERVAL_SECONDS", 30.0)
