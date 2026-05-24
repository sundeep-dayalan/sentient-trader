"""
Historical Alpaca news replay through the normal ingestion pipeline.

This script is for reliability testing. It fetches Alpaca REST news for a time
window and hands each raw article to NewsListener._handle_article(), so replay
uses the same normalization, durable store, dedupe, ticker filtering, outbox,
and Redis publish path as live ingestion.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.append(str(BACKEND_DIR))

from dotenv import load_dotenv

import config
from backfill import AlpacaNewsBackfiller
from listener import NewsListener
from models import iso_z, parse_datetime, utc_now
from producer import STREAM_KEY


log = logging.getLogger("ingestion.replay_historical")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay historical Alpaca news through the ingestion pipeline.",
    )
    window = parser.add_mutually_exclusive_group(required=True)
    window.add_argument("--days", type=float, help="Replay the last N days.")
    window.add_argument("--hours", type=float, help="Replay the last N hours.")
    window.add_argument(
        "--start",
        help="Replay start timestamp, for example 2026-05-14T00:00:00Z.",
    )
    parser.add_argument(
        "--end",
        help="Replay end timestamp. Defaults to now when --days/--hours are used.",
    )
    parser.add_argument(
        "--origin",
        default="historical_replay",
        help="Origin label stored in ingestion events/cursors.",
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=config.BACKFILL_PAGE_LIMIT,
        help="Alpaca page size, capped by the backfiller at 50.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=config.BACKFILL_MAX_PAGES,
        help="Maximum Alpaca pages to fetch.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and count Alpaca articles without touching DB or Redis.",
    )
    parser.add_argument(
        "--confirm-replay",
        action="store_true",
        help="Required for real replay because it writes DB rows and Redis messages.",
    )
    return parser.parse_args()


def _resolve_window(args: argparse.Namespace) -> tuple[datetime, datetime]:
    end = parse_datetime(args.end) if args.end else utc_now()
    if end is None:
        raise ValueError(f"Invalid --end timestamp: {args.end}")

    if args.days is not None:
        return end - timedelta(days=args.days), end
    if args.hours is not None:
        return end - timedelta(hours=args.hours), end

    start = parse_datetime(args.start)
    if start is None:
        raise ValueError(f"Invalid --start timestamp: {args.start}")
    return start, end


def _table_count(listener: NewsListener, table: str) -> int | None:
    try:
        result = listener._store._table(table).select("id", count="exact").limit(1).execute()
        return int(result.count or 0)
    except Exception as exc:
        log.warning("Could not count %s: %s", table, exc)
        return None


def _redis_stream_len(listener: NewsListener) -> int | None:
    try:
        return int(listener._producer.redis.xlen(STREAM_KEY))
    except Exception as exc:
        log.warning("Could not read Redis stream length for %s: %s", STREAM_KEY, exc)
        return None


def _delta(before: int | None, after: int | None) -> str:
    if before is None or after is None:
        return "unknown"
    return str(after - before)


def _print_plan(args: argparse.Namespace, start: datetime, end: datetime) -> None:
    mode = "dry-run" if args.dry_run else "real replay"
    print("Historical replay plan")
    print(f"  mode: {mode}")
    print(f"  start: {iso_z(start)}")
    print(f"  end: {iso_z(end)}")
    print(f"  origin: {args.origin}")
    print(f"  stream: {STREAM_KEY}")
    print(f"  schema: {os.environ.get('SUPABASE_DB_SCHEMA', 'public')}")
    print(f"  max_pages: {args.max_pages}")
    print(f"  page_limit: {args.page_limit}")


def _run_dry_run(start: datetime, end: datetime) -> int:
    backfiller = AlpacaNewsBackfiller(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"],
    )
    samples: list[str] = []

    def count_only(article: dict[str, Any]) -> None:
        if len(samples) < 5:
            samples.append(str(article.get("headline") or "")[:120])

    fetched = backfiller.run(start=start, end=end, on_article=count_only)
    print(f"Fetched {fetched} Alpaca articles; DB/Redis untouched.")
    if samples:
        print("Sample headlines:")
        for headline in samples:
            print(f"  - {headline}")
    return fetched


def _run_replay(args: argparse.Namespace, start: datetime, end: datetime) -> int:
    listener = NewsListener()
    before = {
        "raw_news_articles": _table_count(listener, "raw_news_articles"),
        "news_outbox": _table_count(listener, "news_outbox"),
        "ingestion_events": _table_count(listener, "ingestion_events"),
        "redis_stream": _redis_stream_len(listener),
    }

    backfiller = AlpacaNewsBackfiller(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"],
    )
    fetched = backfiller.run(
        start=start,
        end=end,
        on_article=lambda raw: listener._handle_article(raw, origin=args.origin),
    )

    after = {
        "raw_news_articles": _table_count(listener, "raw_news_articles"),
        "news_outbox": _table_count(listener, "news_outbox"),
        "ingestion_events": _table_count(listener, "ingestion_events"),
        "redis_stream": _redis_stream_len(listener),
    }

    print("Replay complete")
    print(f"  fetched_from_alpaca: {fetched}")
    for key in before:
        print(
            f"  {key}: before={before[key]} after={after[key]} "
            f"delta={_delta(before[key], after[key])}"
        )
    return fetched


def main() -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )

    args = _parse_args()
    start, end = _resolve_window(args)
    if start >= end:
        raise ValueError("--start must be before --end")

    config.BACKFILL_PAGE_LIMIT = args.page_limit
    config.BACKFILL_MAX_PAGES = args.max_pages

    _print_plan(args, start, end)

    if args.dry_run:
        _run_dry_run(start, end)
        return 0

    if not args.confirm_replay:
        print("\nRefusing to run real replay without --confirm-replay.")
        print("Use --dry-run to fetch/count only, or add --confirm-replay to write DB/Redis.")
        return 2

    _run_replay(args, start, end)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
