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
from typing import Any, Optional, Protocol
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

# How long to keep retrying a signal before declaring a genuine NO_BARS.
# Signals that arrive pre-market, after the close, or on weekends/holidays have
# no bars yet when first seen; the labeler must keep them retryable until the
# next live trading session produces data. Six days bridges a long holiday
# weekend (e.g. Fri-after-close -> the following Tuesday open).
MAX_LABEL_HORIZON = timedelta(
    days=int(os.environ.get("OUTCOME_LABELER_MAX_HORIZON_DAYS", "6"))
)
# Re-scan this many non-terminal outcomes each run so rows that aged out of the
# most-recent trades window still get completed.
RETRY_SCAN_LIMIT = int(os.environ.get("OUTCOME_LABELER_RETRY_SCAN_LIMIT", "1500"))
# Marker stored in label_error for retryable "no bars yet" rows. These reuse the
# non-terminal PARTIAL status (no new enum value, so no DB constraint change)
# but the note keeps them distinguishable from genuinely partial labels.
AWAITING_BARS_NOTE = "Awaiting market-data bars for the signal's trading session."
# Transient HTTP statuses worth retrying instead of terminalizing.
RETRYABLE_HTTP_STATUSES = {408, 425, 429}


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


def next_regular_session_close(signal_at: datetime) -> datetime:
    """
    The 16:00 ET close of the regular session whose end-of-day return a signal
    should be measured against.

    Intraday/pre-market signals use the current weekday's close; signals at or
    after the close (and weekend signals) roll to the next weekday's close. This
    keeps the EOD label anchored to a real regular session even when 15m/1h are
    measured from an after-hours bar. Holidays are not modeled; a rolled-to
    holiday simply yields no EOD bar and the row stays retryable.
    """
    local = signal_at.astimezone(MARKET_TZ)

    def close_on(day) -> datetime:
        return datetime.combine(day, time(16, 0), tzinfo=MARKET_TZ).astimezone(
            timezone.utc
        )

    if local.weekday() < 5 and local.timetz().replace(tzinfo=None) < time(16, 0):
        return close_on(local.date())

    day = local.date() + timedelta(days=1)
    while day.weekday() >= 5:  # skip Saturday/Sunday
        day += timedelta(days=1)
    return close_on(day)


def first_bar_at_or_after(bars: list[Bar], target: datetime) -> Optional[Bar]:
    for bar in sorted(bars, key=lambda item: item.timestamp):
        if bar.timestamp >= target:
            return bar
    return None


def first_close_at_or_after(bars: list[Bar], target: datetime) -> Optional[float]:
    bar = first_bar_at_or_after(bars, target)
    return bar.close if bar is not None else None


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


def outcome_label_status(
    *,
    target_eod: datetime,
    now: datetime,
    return_eod: Optional[float],
    return_15m: Optional[float],
    return_1h: Optional[float],
    signal_price: Optional[float],
) -> str:
    if return_eod is not None:
        return "LABELED"
    if now >= target_eod and (
        return_15m is not None or return_1h is not None or signal_price is not None
    ):
        return "LABELED"
    return "PARTIAL"


def label_status_for_record(record: dict[str, Any], now: datetime) -> str:
    signal_at = parse_timestamp(str(record["signal_at"]))
    return outcome_label_status(
        target_eod=market_close_for_signal(signal_at),
        now=now,
        return_eod=record.get("return_eod"),
        return_15m=record.get("return_15m"),
        return_1h=record.get("return_1h"),
        signal_price=record.get("signal_price"),
    )


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


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_extended_hours(signal_at: datetime) -> bool:
    """True when the signal falls outside the 9:30-16:00 ET regular session."""
    local = signal_at.astimezone(MARKET_TZ)
    if local.weekday() >= 5:  # Saturday/Sunday
        return True
    return not (time(9, 30) <= local.timetz().replace(tzinfo=None) < time(16, 0))


class BarProvider(Protocol):
    """A pluggable source of 1-minute close bars for a ticker/time window.

    Add a new source by implementing this protocol and registering it in
    BAR_PROVIDER_REGISTRY; nothing else in the labeler needs to change.
    """

    name: str

    def fetch_bars(self, ticker: str, start: datetime, end: datetime) -> list[Bar]:
        ...


class AlpacaBarProvider:
    """Primary provider: Alpaca historical bars (IEX on the free tier)."""

    name = "alpaca"

    def fetch_bars(self, ticker: str, start: datetime, end: datetime) -> list[Bar]:
        return fetch_bars(ticker, start, end)


class YFinanceBarProvider:
    """
    Fallback provider: Yahoo Finance 1-minute bars including pre/post-market.

    Free and keyless, but unofficial and limited to ~7 days of 1-minute history,
    which is ample for a labeler that runs within hours/days of each signal. Used
    to fill the extended-hours windows where the free Alpaca/IEX feed is empty.
    """

    name = "yfinance"

    def __init__(self) -> None:
        # Import eagerly so an unavailable dependency disables this provider at
        # resolution time instead of failing mid-run.
        import yfinance  # noqa: F401

    def fetch_bars(self, ticker: str, start: datetime, end: datetime) -> list[Bar]:
        import yfinance as yf

        frame = yf.download(
            ticker,
            start=start.astimezone(timezone.utc),
            end=end.astimezone(timezone.utc),
            interval="1m",
            prepost=True,
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        if frame is None or frame.empty:
            return []

        closes = frame["Close"]
        if hasattr(closes, "columns"):  # MultiIndex columns (single-ticker frame)
            closes = closes.iloc[:, 0]

        bars: list[Bar] = []
        for index, close in closes.items():
            try:
                if close != close:  # NaN guard without importing pandas/math
                    continue
                timestamp = index.to_pydatetime()
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                bars.append(
                    Bar(timestamp=timestamp.astimezone(timezone.utc), close=float(close))
                )
            except (AttributeError, TypeError, ValueError):
                continue
        return bars


BAR_PROVIDER_REGISTRY: dict[str, type[BarProvider]] = {
    "alpaca": AlpacaBarProvider,
    "yfinance": YFinanceBarProvider,
}


def resolve_bar_providers() -> list[BarProvider]:
    """
    Build the provider chain: the Alpaca primary followed by any configured
    fallbacks. Fallbacks are enabled by OUTCOME_LABELER_OFFHOURS_FALLBACK and
    selected by name via OUTCOME_LABELER_FALLBACK_PROVIDERS (comma-separated,
    default "yfinance"). Unknown or unavailable providers are skipped with a
    warning so a missing optional dependency never breaks labeling.
    """
    providers: list[BarProvider] = [AlpacaBarProvider()]
    if not env_bool("OUTCOME_LABELER_OFFHOURS_FALLBACK", default=True):
        return providers

    names = [
        name.strip()
        for name in os.environ.get(
            "OUTCOME_LABELER_FALLBACK_PROVIDERS", "yfinance"
        ).split(",")
        if name.strip()
    ]
    for name in names:
        provider_cls = BAR_PROVIDER_REGISTRY.get(name)
        if provider_cls is None:
            log.warning("Unknown bar provider '%s'; skipping", name)
            continue
        try:
            providers.append(provider_cls())
        except Exception as exc:
            log.warning("Bar provider '%s' unavailable; skipping: %s", name, exc)
    return providers


def _earliest_timestamp(bars: list[Bar]) -> Optional[datetime]:
    return min((bar.timestamp for bar in bars), default=None)


def get_bars(
    providers: list[BarProvider],
    ticker: str,
    start: datetime,
    end: datetime,
    *,
    prefer_fallback: bool,
) -> list[Bar]:
    """
    Resolve bars through the provider chain.

    Uses the primary (Alpaca) result unless it is empty, or the window starts in
    extended hours (prefer_fallback) where the free Alpaca/IEX feed has little
    data. In those cases each fallback is queried and the result with the
    earliest coverage of the window wins, so a single consistent source is used
    per signal. If every provider fails and the primary raised, the primary
    error is re-raised so the caller's transient/terminal handling still applies.
    """
    primary, *fallbacks = providers
    primary_error: Optional[Exception] = None
    try:
        best = primary.fetch_bars(ticker, start, end)
    except (httpx.HTTPStatusError, httpx.TransportError) as exc:
        primary_error = exc
        best = []

    if fallbacks and (prefer_fallback or not best):
        for fallback in fallbacks:
            try:
                candidate = fallback.fetch_bars(ticker, start, end)
            except Exception as exc:  # pragma: no cover - defensive
                log.info("Fallback %s failed for %s: %s", fallback.name, ticker, exc)
                continue
            if candidate:
                best_earliest = _earliest_timestamp(best)
                if best_earliest is None or _earliest_timestamp(candidate) < best_earliest:
                    best = candidate

    if not best and primary_error is not None:
        raise primary_error
    return best


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
    # Anchor the forward-return windows to the first bar at/after the signal.
    # Bars only exist during live market sessions, so this rolls past pre-market,
    # after-hours, weekend, and holiday gaps without a market calendar: a signal
    # logged after the close anchors on the next session's opening bar, and an
    # intraday signal anchors on (effectively) itself, preserving prior behavior.
    anchor = first_bar_at_or_after(bars, signal_at)
    anchor_ts = anchor.timestamp if anchor is not None else signal_at
    price_at_signal = signal_price or first_close_at_or_after(bars, signal_at)
    target_15m = anchor_ts + timedelta(minutes=15)
    target_1h = anchor_ts + timedelta(hours=1)
    # EOD is the next regular session close from the signal time (not the anchor),
    # so after-hours anchors still get a next-day EOD rather than a passed close.
    target_eod = next_regular_session_close(signal_at)

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
    record["label_status"] = outcome_label_status(
        target_eod=target_eod,
        now=now,
        return_eod=record["return_eod"],
        return_15m=record["return_15m"],
        return_1h=record["return_1h"],
        signal_price=record["signal_price"],
    )
    return record


def build_pending_record(
    *,
    trade_id: str,
    ticker: str,
    signal_at: datetime,
    signal_price: Optional[float],
    now: datetime,
    note: str,
    label_attempts: int = 1,
) -> dict[str, Any]:
    """
    A retryable "not labeled yet" row.

    Used when no bars are available yet because the signal's trading session has
    not produced data (pre-market, after-hours, weekend, holiday). It reuses the
    non-terminal PARTIAL status so the row is retried on later runs, while the
    note in label_error keeps it distinguishable from a genuinely partial label.
    """
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
        "label_status": "PARTIAL",
        "label_error": note,
        "label_attempts": label_attempts,
        "last_attempt_at": now.isoformat(),
        "labeled_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def _fetch_failure_record(
    *,
    trade_id: str,
    ticker: str,
    signal_at: datetime,
    signal_price: Optional[float],
    now: datetime,
    error: str,
    transient: bool,
    horizon_exceeded: bool,
    attempts: int,
) -> dict[str, Any]:
    """Pick a retryable PARTIAL vs terminal NO_BARS row for a failed bars fetch."""
    if transient and not horizon_exceeded:
        return build_pending_record(
            trade_id=trade_id,
            ticker=ticker,
            signal_at=signal_at,
            signal_price=signal_price,
            now=now,
            note=f"{error} Will retry.",
            label_attempts=attempts,
        )
    return build_no_bars_record(
        trade_id=trade_id,
        ticker=ticker,
        signal_at=signal_at,
        signal_price=signal_price,
        now=now,
        error=error,
        label_attempts=attempts,
    )


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


def gather_label_candidates(
    sb, *, limit: int, min_age: str
) -> list[tuple[str, str, str]]:
    """
    Collect (trade_id, ticker, signal_at) tuples to (re)label this run.

    Combines the most recent trades with every non-terminal signal_outcomes row
    so signals that aged out of the recent-trades window (common on heavy
    pre-market days) still get retried until they reach a terminal status.
    """
    candidates: dict[str, tuple[str, str, str]] = {}

    recent = (
        sb.table("trades")
        .select("id, ticker, created_at")
        .lte("created_at", min_age)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )
    for trade in recent:
        trade_id = trade.get("id")
        ticker = trade.get("ticker")
        created_at = trade.get("created_at")
        if trade_id and ticker and created_at and trade_id not in candidates:
            candidates[str(trade_id)] = (str(trade_id), str(ticker), str(created_at))

    try:
        pending = (
            sb.table("signal_outcomes")
            .select("trade_id, ticker, signal_at")
            .not_.in_("label_status", list(TERMINAL_STATUSES))
            .order("signal_at", desc=True)
            .limit(RETRY_SCAN_LIMIT)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # pragma: no cover - defensive; never block the run
        log.warning("Could not scan non-terminal outcomes for retry: %s", exc)
        pending = []
    for row in pending:
        trade_id = row.get("trade_id")
        ticker = row.get("ticker")
        signal_at = row.get("signal_at")
        if trade_id and ticker and signal_at and trade_id not in candidates:
            candidates[str(trade_id)] = (str(trade_id), str(ticker), str(signal_at))

    return list(candidates.values())


def label_recent_signals(limit: int, *, force: bool = False) -> int:
    now = datetime.now(timezone.utc)
    min_age = (now - timedelta(minutes=15)).isoformat()
    sb = get_supabase_client()
    providers = resolve_bar_providers()
    log.info("Bar providers: %s", ", ".join(p.name for p in providers))
    candidates = gather_label_candidates(sb, limit=limit, min_age=min_age)

    labeled = 0
    for trade_id, ticker, created_at in candidates:
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
        attempts = next_attempt_count(existing)
        if not is_supported_alpaca_stock_symbol(str(ticker)):
            error = (
                "Ticker format is unsupported by Alpaca stock bars endpoint: "
                f"{ticker}"
            )
            log.info("%s trade_id=%s", error, trade_id)
            record = build_no_bars_record(
                trade_id=trade_id,
                ticker=ticker,
                signal_at=signal_at,
                signal_price=trace_price,
                now=now,
                error=error,
                label_attempts=attempts,
            )
            sb.table("signal_outcomes").upsert(record, on_conflict="trade_id").execute()
            labeled += 1
            continue

        # Fetch from the signal up to now (capped at the retry horizon). The bars
        # themselves define where the next live session falls, so off-hours and
        # weekend/holiday signals simply return no bars until that session opens.
        horizon_exceeded = now >= signal_at + MAX_LABEL_HORIZON
        end = min(now, signal_at + MAX_LABEL_HORIZON)

        try:
            bars = get_bars(
                providers,
                str(ticker),
                signal_at,
                end,
                prefer_fallback=is_extended_hours(signal_at),
            )
        except httpx.HTTPStatusError as exc:
            error = alpaca_bar_error_message(exc)
            transient = exc.response.status_code in RETRYABLE_HTTP_STATUSES or (
                exc.response.status_code >= 500
            )
            log.info("%s %s trade_id=%s", error, ticker, trade_id)
            record = _fetch_failure_record(
                trade_id=trade_id,
                ticker=ticker,
                signal_at=signal_at,
                signal_price=trace_price,
                now=now,
                error=error,
                transient=transient,
                horizon_exceeded=horizon_exceeded,
                attempts=attempts,
            )
            sb.table("signal_outcomes").upsert(record, on_conflict="trade_id").execute()
            labeled += 1
            continue
        except httpx.TransportError as exc:
            # Network blip (timeout/connection reset): never a "no data" signal,
            # so keep retrying rather than crashing the whole run.
            error = f"Alpaca bars request error: {type(exc).__name__}."
            log.info("%s %s trade_id=%s", error, ticker, trade_id)
            record = _fetch_failure_record(
                trade_id=trade_id,
                ticker=ticker,
                signal_at=signal_at,
                signal_price=trace_price,
                now=now,
                error=error,
                transient=True,
                horizon_exceeded=horizon_exceeded,
                attempts=attempts,
            )
            sb.table("signal_outcomes").upsert(record, on_conflict="trade_id").execute()
            labeled += 1
            continue

        if not bars:
            # No bars yet. Keep retrying (PARTIAL) until the signal's session has
            # had time to produce data; only after the horizon do we accept that
            # there is genuinely no data (delisted/halted/never traded).
            if horizon_exceeded:
                error = "No Alpaca bars available after waiting for the session window."
                log.info("%s %s trade_id=%s", error, ticker, trade_id)
                record = build_no_bars_record(
                    trade_id=trade_id,
                    ticker=ticker,
                    signal_at=signal_at,
                    signal_price=trace_price,
                    now=now,
                    error=error,
                    label_attempts=attempts,
                )
            else:
                record = build_pending_record(
                    trade_id=trade_id,
                    ticker=ticker,
                    signal_at=signal_at,
                    signal_price=trace_price,
                    now=now,
                    note=AWAITING_BARS_NOTE,
                    label_attempts=attempts,
                )
            sb.table("signal_outcomes").upsert(record, on_conflict="trade_id").execute()
            labeled += 1
            continue

        record = build_outcome_record(
            trade_id=trade_id,
            ticker=ticker,
            signal_at=signal_at,
            signal_price=trace_price,
            bars=bars,
            now=now,
            label_attempts=attempts,
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
