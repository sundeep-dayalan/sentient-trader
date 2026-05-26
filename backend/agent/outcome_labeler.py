"""
Post-signal outcome labeler.

Run periodically to attach 15m, 1h, and end-of-day return labels to logged
signals. These labels let audits answer whether blocked directional
recommendations would have made or lost money.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from dotenv import find_dotenv, load_dotenv

log = logging.getLogger("agent.outcome_labeler")

MARKET_TZ = ZoneInfo("America/New_York")
ALPACA_DATA_BASE_URL = os.environ.get(
    "ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"
)
ALPACA_DATA_FEED = os.environ.get("ALPACA_DATA_FEED", "iex")
TERMINAL_STATUSES = {"LABELED", "NO_BARS", "ERROR"}
ALPACA_STOCK_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]*$")


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    close: float


def parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def market_close_for_signal(signal_at: datetime) -> datetime:
    local = signal_at.astimezone(MARKET_TZ)
    close = datetime.combine(local.date(), time(16, 0), tzinfo=MARKET_TZ)
    return close.astimezone(timezone.utc)


def first_close_at_or_after(bars: list[Bar], target: datetime) -> Optional[float]:
    for bar in sorted(bars, key=lambda item: item.timestamp):
        if bar.timestamp >= target:
            return bar.close
    return None


def last_close_at_or_before(bars: list[Bar], target: datetime) -> Optional[float]:
    eligible = [bar.close for bar in bars if bar.timestamp <= target]
    return eligible[-1] if eligible else None


def return_pct(start: Optional[float], end: Optional[float]) -> Optional[float]:
    if start is None or end is None or start <= 0:
        return None
    return round((end - start) / start, 6)


def is_supported_alpaca_stock_symbol(ticker: str) -> bool:
    """
    Alpaca's stock bars endpoint expects US-style symbols, not exchange-prefixed
    symbols such as TSX:ENB. Treat unsupported forms as no-bar rows so one
    foreign/invalid ticker cannot abort the whole scheduler run.
    """
    return bool(ALPACA_STOCK_SYMBOL_PATTERN.fullmatch(ticker.strip().upper()))


def alpaca_bar_error_message(exc: httpx.HTTPStatusError) -> str:
    status_code = exc.response.status_code
    reason = exc.response.reason_phrase
    return f"Alpaca bars request failed with HTTP {status_code} {reason}."


def label_status_for_record(record: dict[str, Any], now: datetime) -> str:
    signal_at = parse_timestamp(str(record["signal_at"]))
    eod_due = now >= market_close_for_signal(signal_at)
    if record.get("return_eod") is not None:
        return "LABELED"
    if eod_due and (
        record.get("return_15m") is not None
        or record.get("return_1h") is not None
        or record.get("signal_price") is not None
    ):
        return "LABELED"
    return "PARTIAL"


def signal_price_from_trace(trace: Optional[dict[str, Any]]) -> Optional[float]:
    try:
        price = ((trace or {}).get("market_context") or {}).get("price")
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def alpaca_headers() -> dict[str, str]:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("Missing Alpaca API credentials")
    return {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }


def fetch_bars(ticker: str, start: datetime, end: datetime) -> list[Bar]:
    params = {
        "timeframe": "1Min",
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "adjustment": "raw",
        "feed": ALPACA_DATA_FEED,
        "limit": "10000",
    }
    with httpx.Client(timeout=20) as client:
        response = client.get(
            f"{ALPACA_DATA_BASE_URL}/v2/stocks/{ticker}/bars",
            headers=alpaca_headers(),
            params=params,
        )
        response.raise_for_status()
        rows = response.json().get("bars") or []

    bars: list[Bar] = []
    for row in rows:
        try:
            bars.append(
                Bar(
                    timestamp=parse_timestamp(str(row["t"])),
                    close=float(row["c"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return bars


def build_outcome_record(
    *,
    trade_id: str,
    ticker: str,
    signal_at: datetime,
    signal_price: Optional[float],
    bars: list[Bar],
    now: datetime,
    label_attempts: int = 1,
) -> dict[str, Any]:
    price_at_signal = signal_price or first_close_at_or_after(bars, signal_at)
    target_15m = signal_at + timedelta(minutes=15)
    target_1h = signal_at + timedelta(hours=1)
    target_eod = market_close_for_signal(signal_at)

    price_15m = first_close_at_or_after(bars, target_15m) if now >= target_15m else None
    price_1h = first_close_at_or_after(bars, target_1h) if now >= target_1h else None
    price_eod = last_close_at_or_before(bars, target_eod) if now >= target_eod else None

    record = {
        "trade_id": trade_id,
        "ticker": ticker,
        "signal_at": signal_at.isoformat(),
        "signal_price": price_at_signal,
        "price_15m": price_15m,
        "return_15m": return_pct(price_at_signal, price_15m),
        "price_1h": price_1h,
        "return_1h": return_pct(price_at_signal, price_1h),
        "price_eod": price_eod,
        "return_eod": return_pct(price_at_signal, price_eod),
        "label_status": "PARTIAL",
        "label_error": None,
        "label_attempts": label_attempts,
        "last_attempt_at": now.isoformat(),
        "labeled_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    record["label_status"] = label_status_for_record(record, now)
    return record


def build_no_bars_record(
    *,
    trade_id: str,
    ticker: str,
    signal_at: datetime,
    signal_price: Optional[float],
    now: datetime,
    error: str,
    label_attempts: int = 1,
) -> dict[str, Any]:
    return {
        "trade_id": trade_id,
        "ticker": ticker,
        "signal_at": signal_at.isoformat(),
        "signal_price": signal_price,
        "price_15m": None,
        "return_15m": None,
        "price_1h": None,
        "return_1h": None,
        "price_eod": None,
        "return_eod": None,
        "label_status": "NO_BARS",
        "label_error": error,
        "label_attempts": label_attempts,
        "last_attempt_at": now.isoformat(),
        "labeled_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def should_skip_existing(existing: Optional[dict[str, Any]], *, force: bool) -> bool:
    if force or not existing:
        return False
    return str(existing.get("label_status") or "").upper() in TERMINAL_STATUSES


def next_attempt_count(existing: Optional[dict[str, Any]]) -> int:
    try:
        return int((existing or {}).get("label_attempts") or 0) + 1
    except (TypeError, ValueError):
        return 1


def maybe_single_data(query) -> Optional[dict[str, Any]]:
    """
    Return one Supabase row or None.

    Some supabase-py/PostgREST combinations return None from execute() for a
    zero-row maybe_single() response instead of an object with .data. Treat that
    as the expected "no row yet" case.
    """
    result = query.maybe_single().execute()
    data = getattr(result, "data", None)
    return data if isinstance(data, dict) else None


def get_supabase_client():
    from supabase import create_client
    from supabase.client import ClientOptions

    return create_client(
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        options=ClientOptions(
            schema=os.environ.get("SUPABASE_DB_SCHEMA", "public"),
        ),
    )


def label_recent_signals(limit: int, *, force: bool = False) -> int:
    now = datetime.now(timezone.utc)
    min_age = (now - timedelta(minutes=15)).isoformat()
    sb = get_supabase_client()
    trades = (
        sb.table("trades")
        .select("id, ticker, created_at")
        .lte("created_at", min_age)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )

    labeled = 0
    for trade in trades:
        trade_id = trade.get("id")
        ticker = trade.get("ticker")
        created_at = trade.get("created_at")
        if not trade_id or not ticker or not created_at:
            continue

        existing = maybe_single_data(
            sb.table("signal_outcomes")
            .select("trade_id, label_status, label_attempts")
            .eq("trade_id", trade_id)
        )
        if should_skip_existing(existing, force=force):
            log.info(
                "Skipping %s trade_id=%s; existing outcome status=%s",
                ticker,
                trade_id,
                existing.get("label_status"),
            )
            continue

        trace_row = (
            maybe_single_data(
                sb.table("trade_decision_traces")
                .select("decision_trace")
                .eq("trade_id", trade_id)
            )
            or {}
        )
        trace = trace_row.get("decision_trace")
        signal_at = parse_timestamp(str(created_at))
        trace_price = signal_price_from_trace(trace if isinstance(trace, dict) else None)
        if not is_supported_alpaca_stock_symbol(str(ticker)):
            error = (
                "Ticker format is unsupported by Alpaca stock bars endpoint: "
                f"{ticker}"
            )
            log.info("%s trade_id=%s", error, trade_id)
            record = build_no_bars_record(
                trade_id=str(trade_id),
                ticker=str(ticker),
                signal_at=signal_at,
                signal_price=trace_price,
                now=now,
                error=error,
                label_attempts=next_attempt_count(existing),
            )
            sb.table("signal_outcomes").upsert(record, on_conflict="trade_id").execute()
            labeled += 1
            continue

        end = min(now, market_close_for_signal(signal_at) + timedelta(minutes=5))
        if end <= signal_at:
            record = build_no_bars_record(
                trade_id=str(trade_id),
                ticker=str(ticker),
                signal_at=signal_at,
                signal_price=trace_price,
                now=now,
                error="Signal timestamp is after the available market-data window.",
                label_attempts=next_attempt_count(existing),
            )
            sb.table("signal_outcomes").upsert(record, on_conflict="trade_id").execute()
            labeled += 1
            continue

        try:
            bars = fetch_bars(str(ticker), signal_at, end)
        except httpx.HTTPStatusError as exc:
            error = alpaca_bar_error_message(exc)
            log.info("%s %s trade_id=%s", error, ticker, trade_id)
            record = build_no_bars_record(
                trade_id=str(trade_id),
                ticker=str(ticker),
                signal_at=signal_at,
                signal_price=trace_price,
                now=now,
                error=error,
                label_attempts=next_attempt_count(existing),
            )
            sb.table("signal_outcomes").upsert(record, on_conflict="trade_id").execute()
            labeled += 1
            continue

        if not bars:
            error = "No Alpaca bars available for requested window."
            log.info("%s %s trade_id=%s", error, ticker, trade_id)
            record = build_no_bars_record(
                trade_id=str(trade_id),
                ticker=str(ticker),
                signal_at=signal_at,
                signal_price=trace_price,
                now=now,
                error=error,
                label_attempts=next_attempt_count(existing),
            )
            sb.table("signal_outcomes").upsert(record, on_conflict="trade_id").execute()
            labeled += 1
            continue

        record = build_outcome_record(
            trade_id=str(trade_id),
            ticker=str(ticker),
            signal_at=signal_at,
            signal_price=trace_price,
            bars=bars,
            now=now,
            label_attempts=next_attempt_count(existing),
        )
        sb.table("signal_outcomes").upsert(record, on_conflict="trade_id").execute()
        labeled += 1

    return labeled


def main() -> None:
    dotenv_path = find_dotenv()
    if dotenv_path:
        load_dotenv(dotenv_path, override=False)
    else:
        load_dotenv(override=False)

    parser = argparse.ArgumentParser(description="Label post-signal returns.")
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Relabel rows even when signal_outcomes already has a terminal status.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
    )
    labeled = label_recent_signals(limit=args.limit, force=args.force)
    log.info("Labeled %d signal outcome rows", labeled)


if __name__ == "__main__":
    main()
