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


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


PROVIDER = os.environ.get("INGESTION_PROVIDER", "alpaca")
WORKER_NAME = os.environ.get("INGESTION_WORKER_NAME", "ingestion")
LIVE_ENABLED = env_bool("INGESTION_LIVE_ENABLED", True)

ALPACA_NEWS_BASE_URL = os.environ.get(
    "ALPACA_NEWS_BASE_URL",
    "https://data.alpaca.markets",
).rstrip("/")
ALPACA_TRADING_BASE_URL = os.environ.get(
    "ALPACA_TRADING_BASE_URL",
    os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
).rstrip("/")

TICKER_META_HASH_KEY = os.environ.get("TICKER_META_HASH_KEY", "sentient:ticker:meta")
TICKER_DIRECTORY_STATE_KEY = os.environ.get(
    "TICKER_DIRECTORY_STATE_KEY",
    "sentient:ticker:directory:state",
)
TICKER_DIRECTORY_REFRESH_SECONDS = env_int(
    "TICKER_DIRECTORY_REFRESH_SECONDS",
    24 * 60 * 60,
)
TICKER_DIRECTORY_HTTP_TIMEOUT_SECONDS = env_float(
    "TICKER_DIRECTORY_HTTP_TIMEOUT_SECONDS",
    15.0,
)
TICKER_ALIAS_OVERRIDES_JSON = os.environ.get("TICKER_ALIAS_OVERRIDES_JSON", "")
TICKER_ALIAS_OVERRIDES_PATH = os.environ.get("TICKER_ALIAS_OVERRIDES_PATH", "")
TICKER_ALIAS_OVERRIDES_KEY = os.environ.get(
    "TICKER_ALIAS_OVERRIDES_KEY",
    "sentient:ticker:alias-overrides",
)
TICKER_DIRECTORY_REFRESH_CHECK_SECONDS = env_int(
    "TICKER_DIRECTORY_REFRESH_CHECK_SECONDS",
    60,
)
TICKER_HEADLINE_MATCH_SCORE = env_int("TICKER_HEADLINE_MATCH_SCORE", 100)
TICKER_SUMMARY_MATCH_SCORE = env_int("TICKER_SUMMARY_MATCH_SCORE", 35)
TICKER_BODY_MATCH_SCORE = env_int("TICKER_BODY_MATCH_SCORE", 15)
TICKER_PUBLISH_SCORE_THRESHOLD = env_int("TICKER_PUBLISH_SCORE_THRESHOLD", 80)

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
