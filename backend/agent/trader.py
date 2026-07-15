"""
Alpaca Paper Trading Executor
===============================
Submits market orders to Alpaca's paper trading sandbox.

Paper trading = real market data, simulated money.
The API key in .env must be a PAPER trading key — never a live key.

Design choices:
  - Market orders (not limit) because news-driven trades prioritize speed
  - TimeInForce.DAY cancels unfilled orders at market close automatically
  - paper=True is hardcoded — this line cannot accidentally go live
  - Returns structured failure metadata instead of raising; the pipeline keeps running
"""

import logging
import os
import threading
import time as _time
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any, Optional

try:
    from alpaca.common.exceptions import APIError
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import (
        LimitOrderRequest,
        MarketOrderRequest,
        StopLossRequest,
        StopOrderRequest,
        TakeProfitRequest,
    )
except ModuleNotFoundError:
    APIError = Exception  # type: ignore[assignment]
    TradingClient = None  # type: ignore[assignment]
    OrderClass = SimpleNamespace(BRACKET="bracket", SIMPLE="simple")  # type: ignore[assignment]
    OrderSide = SimpleNamespace(BUY="buy", SELL="sell")
    TimeInForce = SimpleNamespace(DAY="day", GTC="gtc", IOC="ioc")

    class MarketOrderRequest:  # type: ignore[no-redef]
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class LimitOrderRequest:  # type: ignore[no-redef]
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class StopOrderRequest:  # type: ignore[no-redef]
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class TakeProfitRequest:  # type: ignore[no-redef]
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class StopLossRequest:  # type: ignore[no-redef]
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

import config

log = logging.getLogger("agent.trader")

# Hard ceiling for any single Alpaca REST call. alpaca-py (≤0.26) issues every
# request through a plain requests.Session with NO timeout, so one stalled
# connection blocks the calling thread FOREVER — this silently froze the
# position-monitor safety loop for days while the consumer kept trading
# (README Bug Log: BUG-2026-07-13-01). 20s is generous for Alpaca's API while
# still guaranteeing a wedge becomes a raised requests.Timeout that the
# caller's existing error handling logs and retries.
ALPACA_HTTP_TIMEOUT_SECONDS = 20.0


def harden_alpaca_client(client, timeout_seconds: float = ALPACA_HTTP_TIMEOUT_SECONDS):
    """Install a default per-request timeout on an alpaca-py REST client.

    Wraps the client's underlying ``requests.Session.request`` so every call
    carries a timeout unless the caller passed one explicitly. Idempotent and
    best-effort: if the SDK's internals change shape, the client is returned
    unmodified rather than broken. Returns the client for call-site chaining.
    """
    try:
        session = getattr(client, "_session", None)
        if session is None or getattr(session, "_sentient_timeout_wrapped", False):
            return client
        original_request = session.request

        def _timed_request(*args, **kwargs):
            kwargs.setdefault("timeout", timeout_seconds)
            return original_request(*args, **kwargs)

        session.request = _timed_request
        session._sentient_timeout_wrapped = True
    except Exception as exc:
        log.warning("Could not install HTTP timeout on Alpaca client: %s", exc)
    return client


@dataclass
class OrderResult:
    submitted: bool
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _floatish(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _boolish(value) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


def _enum_token(value: Any) -> str:
    """
    Normalize an Alpaca enum (or raw string) to a stable, lowercase,
    prefix-free token. The Python SDK sometimes returns an enum whose `str()`
    is prefixed — `OrderSide.BUY`, `OrderType.STOP`, `OrderStatus.PENDING_NEW`
    — and sometimes a raw lowercase string (`"accepted"`). Callers compare
    against bare tokens (`"buy"`, `"stop"`, `"filled"`), so mixed forms
    silently break every such check.

    Returns "" for empty/None so callers can still distinguish "no value".
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # Strip Python enum prefix if present (e.g. "OrderSide.BUY")
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.lower()


def _normalize_status(value: Any) -> str:
    """Normalize Alpaca's order `status` to a stable lowercase token.

    Mixed enum/string forms (`OrderStatus.PENDING_NEW` vs `"accepted"`) would
    otherwise break every `== "filled"` check across the DB.
    """
    return _enum_token(value)


def _normalize_side(value: Any) -> str:
    """Normalize a position side to exactly "long" or "short".

    The SDK returns `PositionSide.SHORT` enums whose `str()` is prefixed; raw
    REST returns "short". Un-normalized, every `side in ("long", "short")`
    check silently fails — which made the position monitor skip its ENTIRE
    book on every sweep (README Bug Log: BUG-2026-07-14-01).
    """
    token = _enum_token(value)
    return "short" if token == "short" else "long"


def _parse_iso_utc(value: Any):
    """Parse an ISO-8601 string (raw REST) to an aware datetime; None if not."""
    from datetime import datetime, timezone
    if value is None or hasattr(value, "timestamp"):
        return value  # already a datetime (or None)
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, TypeError):
        return None


class AlpacaTrader:
    """
    Wraps alpaca-py's TradingClient for paper trading.
    Always operates in paper=True mode — no real money can ever be at risk.
    """

    # Class-level fallbacks for the TTL caches so instances constructed via
    # ``__new__`` (the offline-test pattern in this repo) still work; __init__
    # replaces them with per-instance state.
    _cache_lock = threading.Lock()
    _clock_cache: tuple[float, Optional[dict]] = (0.0, None)
    _risk_context_cache: tuple[float, Optional[dict]] = (0.0, None)

    def __init__(self) -> None:
        self._dry_run = os.environ.get("MOCK_ALPACA", "false").lower() == "true"
        if TradingClient is None:
            raise RuntimeError(
                "alpaca-py is not installed. Install backend/agent requirements before running the agent."
            )
        self._client = harden_alpaca_client(TradingClient(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_SECRET_KEY"],
            paper=True,  # Hardcoded — this service must never touch live funds
        ))
        # Lazy data client for last-mile bracket-price sanity checks.
        self._data_client = None
        # Small TTL caches for risk plumbing. Guarded by a lock because the
        # position monitor thread and the consumer thread share this trader.
        self._cache_lock = threading.Lock()
        self._clock_cache: tuple[float, Optional[dict]] = (0.0, None)
        self._risk_context_cache: tuple[float, Optional[dict]] = (0.0, None)
        if self._dry_run:
            log.info("Alpaca trader initialized in MOCK mode (Dry run)")
        else:
            log.info("Alpaca trader initialized (paper trading mode)")

    def _latest_trade_price(self, ticker: str) -> Optional[float]:
        """
        Re-fetch the live trade price immediately before order submission.
        Used to validate bracket TP/SL against Alpaca's `base_price` so we
        don't get rejected for stale snapshots. Returns None on any failure
        — callers must treat None as "skip the sanity check" not an error.
        """
        if self._dry_run:
            return None
        try:
            if self._data_client is None:
                from alpaca.data.historical import StockHistoricalDataClient
                from alpaca.data.requests import StockLatestTradeRequest
                self._data_client = harden_alpaca_client(StockHistoricalDataClient(
                    api_key=os.environ["ALPACA_API_KEY"],
                    secret_key=os.environ["ALPACA_SECRET_KEY"],
                ))
                self._StockLatestTradeRequest = StockLatestTradeRequest
            trades = self._data_client.get_stock_latest_trade(
                self._StockLatestTradeRequest(symbol_or_symbols=ticker)
            )
            trade = trades.get(ticker) if trades else None
            if trade and getattr(trade, "price", None):
                return float(trade.price)
        except Exception as exc:
            log.debug("Live price re-fetch failed for %s: %s", ticker, exc)
        return None

    def _can_open_bracket(self, ticker: str) -> bool:
        """True only when ``ticker`` is flat with no working orders.

        Alpaca rejects a bracket unless it *opens* a fresh position ("bracket
        orders must be entry orders"). Stacking a bracket on an existing
        position or a still-working order for the same symbol fails and loses
        the whole trade, so callers fall back to a plain order when this returns
        False. Best-effort: the underlying lookups already swallow transient
        broker errors (treating them as flat/no-orders), so an unverifiable
        state lets the bracket proceed — no worse than before this guard.
        """
        try:
            position = self.get_position_context(ticker)
            if str(position.get("side", "flat")).lower() != "flat":
                return False
            if abs(position.get("qty") or 0.0) > 0:
                return False
            if self.get_open_orders(ticker):
                return False
            return True
        except Exception as exc:
            log.warning("Bracket eligibility check failed for %s: %s", ticker, exc)
            return False

    def place_order(
        self,
        ticker: str,
        action: str,
        quantity: Optional[int] = None,
        client_order_id: Optional[str] = None,
        limit_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
    ) -> OrderResult:
        """
        Submit a market or limit order and return structured Alpaca execution metadata.
        Failed orders return submitted=False — errors are logged but don't crash the pipeline.

        When limit_price is provided, submits a limit IOC order instead of market DAY.

        When take_profit_price and stop_loss_price are provided, submits a native
        Alpaca bracket order (order_class=BRACKET) that atomically attaches
        take-profit and stop-loss legs to the primary order. This avoids the
        "potential wash trade" error that occurs when submitting separate orders.
        A BUY brackets long (target above, stop below); a SELL whose legs are
        mirrored (target below < stop above) opens a *protected short*.
        """
        qty = quantity if quantity is not None else config.ORDER_QTY
        side = OrderSide.BUY if action == "BUY" else OrderSide.SELL

        if self._dry_run:
            import uuid

            mock_id = str(uuid.uuid4())
            log.info(
                "MOCK ORDER (Dry Run): %s %d %s → order_id=%s",
                action,
                qty,
                ticker,
                mock_id,
            )
            return OrderResult(
                submitted=True,
                order_id=mock_id,
                client_order_id=client_order_id,
                status="accepted",
            )

        # Determine if this should be a bracket order. A BUY brackets with the
        # target above and stop below entry; a SELL that *opens a short* brackets
        # mirrored — target below, stop above — which we detect by the legs'
        # geometry (take_profit < stop_loss). A reduce-long SELL passes no legs
        # and never brackets.
        have_legs = (
            take_profit_price is not None
            and stop_loss_price is not None
            and take_profit_price > 0
            and stop_loss_price > 0
        )
        is_short_bracket = (
            action == "SELL" and have_legs and take_profit_price < stop_loss_price
        )
        use_bracket = (action == "BUY" and have_legs) or is_short_bracket

        # ── Bug #1 guard: a bracket must be an *entry* order ──────────────────
        # Alpaca only accepts a bracket (entry + attached TP/SL legs) when it
        # opens a position from flat — "bracket orders must be entry orders".
        # If we already hold the name, or have an unfilled order working for it,
        # the bracket is rejected and the *whole* trade is dropped. In that case
        # fall back to a plain order so the trade still goes through; the
        # position monitor attaches/maintains a protective stop afterwards.
        if use_bracket and not self._can_open_bracket(ticker):
            log.info(
                "Bracket skipped for %s: an existing position or working order "
                "means this isn't an entry order. Falling back to a simple order.",
                ticker,
            )
            use_bracket = False
            take_profit_price = None
            stop_loss_price = None

        # ── Bracket sanity guard ──────────────────────────────────────────────
        # Alpaca rejects brackets where TP/SL violate the live `base_price`:
        #   - take_profit.limit_price must be >= base_price + 0.01
        #   - stop_loss.stop_price    must be <= base_price - 0.01
        # If the live quote drifted past either bound (we saw +6.71% on NTAP,
        # -3.87% on ATS), nudge the offending leg by a tiny epsilon so the
        # whole order isn't rejected and the position ends up unprotected.
        # If a leg is still infeasible after the nudge, drop the bracket
        # entirely and submit a simple order — better a naked fill than no
        # fill at all (the position monitor will attach a stop afterwards).
        if use_bracket:
            live_price = self._latest_trade_price(ticker)
            if live_price and live_price > 0:
                if is_short_bracket:
                    # Short legs mirror the long guard: take-profit must sit at or
                    # below base_price-0.01 (buy back lower), stop at or above
                    # base_price+0.01 (buy back higher). Feasible requires sl > tp.
                    tp_adjusted = min(round(take_profit_price, 2), round(live_price - 0.01, 2))
                    sl_adjusted = max(round(stop_loss_price, 2), round(live_price + 0.01, 2))
                    infeasible_order = sl_adjusted <= tp_adjusted
                else:
                    tp_min = round(live_price + 0.01, 2)
                    sl_max = round(live_price - 0.01, 2)
                    tp_adjusted = max(round(take_profit_price, 2), tp_min)
                    sl_adjusted = min(round(stop_loss_price, 2), sl_max)
                    infeasible_order = tp_adjusted <= sl_adjusted
                tp_drift = abs(tp_adjusted - take_profit_price) / max(take_profit_price, 0.01)
                sl_drift = abs(sl_adjusted - stop_loss_price) / max(stop_loss_price, 0.01)
                # Cap the auto-adjust at 2% — beyond that the price has run
                # too far for the original thesis; abandon the bracket.
                if tp_drift > 0.02 or sl_drift > 0.02 or infeasible_order:
                    log.warning(
                        "Bracket aborted for %s: live=$%.2f drifted past TP=$%.2f / SL=$%.2f "
                        "(tp_drift=%.2f%% sl_drift=%.2f%%). Falling back to simple order.",
                        ticker, live_price, take_profit_price, stop_loss_price,
                        tp_drift * 100, sl_drift * 100,
                    )
                    use_bracket = False
                    take_profit_price = None
                    stop_loss_price = None
                else:
                    if tp_adjusted != round(take_profit_price, 2):
                        log.info(
                            "Bracket TP nudged for %s: $%.2f → $%.2f (live=$%.2f)",
                            ticker, take_profit_price, tp_adjusted, live_price,
                        )
                        take_profit_price = tp_adjusted
                    if sl_adjusted != round(stop_loss_price, 2):
                        log.info(
                            "Bracket SL nudged for %s: $%.2f → $%.2f (live=$%.2f)",
                            ticker, stop_loss_price, sl_adjusted, live_price,
                        )
                        stop_loss_price = sl_adjusted

        # ── Time-in-force selection ───────────────────────────────────────────
        # Brackets use GTC so the protective TP/SL legs outlive the session; a
        # simple limit uses IOC (fill what's available now, cancel the rest); a
        # simple market uses DAY. Bug #2: hard-to-borrow assets can *only* be
        # shorted with a DAY order, and we don't know which tickers are
        # hard-to-borrow up front — so we submit normally and retry once as DAY
        # if Alpaca rejects with that specific reason (see the submit loop).
        is_limit = limit_price is not None and limit_price > 0
        if use_bracket:
            time_in_force = TimeInForce.GTC
        elif is_limit:
            time_in_force = TimeInForce.IOC
        else:
            time_in_force = TimeInForce.DAY

        def _build_request(tif):
            """Build the order request for a given time-in-force."""
            kwargs = dict(
                symbol=ticker,
                qty=qty,
                side=side,
                time_in_force=tif,
                client_order_id=client_order_id,
            )
            if use_bracket:
                kwargs["order_class"] = OrderClass.BRACKET
                kwargs["take_profit"] = TakeProfitRequest(
                    limit_price=round(take_profit_price, 2)
                )
                kwargs["stop_loss"] = StopLossRequest(
                    stop_price=round(stop_loss_price, 2)
                )
            if is_limit:
                kwargs["limit_price"] = round(limit_price, 2)
                return LimitOrderRequest(**kwargs)
            return MarketOrderRequest(**kwargs)

        kind = "BRACKET" if use_bracket else "SIMPLE"
        legs_note = (
            " (TP=$%.2f, SL=$%.2f)" % (take_profit_price, stop_loss_price)
            if use_bracket
            else ""
        )
        if is_limit:
            log.info(
                "Submitting %s LIMIT order: %s %d %s @ $%.2f%s",
                kind, action, qty, ticker, round(limit_price, 2), legs_note,
            )
        else:
            log.info(
                "Submitting %s MARKET order: %s %d %s%s",
                kind, action, qty, ticker, legs_note,
            )

        # Submit, retrying once as a DAY order if the asset is hard-to-borrow.
        retried_as_day = False
        while True:
            order_request = _build_request(time_in_force)
            try:
                order = self._client.submit_order(order_data=order_request)
                order_id = str(getattr(order, "id", "") or "")
                status = _normalize_status(getattr(order, "status", None))
                lookup_error: Optional[str] = None

                if not order_id and client_order_id:
                    try:
                        order = self._client.get_order_by_client_id(client_order_id)
                        order_id = str(getattr(order, "id", "") or "")
                        status = _normalize_status(getattr(order, "status", None)) or status
                    except Exception as exc:
                        lookup_error = str(exc)

                if not order_id:
                    error = "Alpaca order submission returned no order_id."
                    if lookup_error:
                        error = f"{error} Lookup by client_order_id failed: {lookup_error}"
                    log.error(
                        "Order submission for %s %s returned no Alpaca order_id "
                        "(client_order_id=%s status=%s)",
                        action,
                        ticker,
                        client_order_id,
                        status or "unknown",
                    )
                    return OrderResult(
                        submitted=False,
                        client_order_id=client_order_id,
                        status=status or None,
                        error=error,
                    )

                log.info(
                    "%s order submitted: %s %d %s → order_id=%s",
                    kind,
                    action,
                    qty,
                    ticker,
                    order_id,
                )
                return OrderResult(
                    submitted=True,
                    order_id=order_id,
                    client_order_id=client_order_id,
                    status=status,
                )

            except APIError as e:
                # Bug #2: a hard-to-borrow asset can only be shorted with a DAY
                # order. Retry once as DAY rather than dropping the trade — this
                # is cheaper and safer than pre-fetching every asset's
                # borrow status. Any other rejection still fails fast.
                if (
                    not retried_as_day
                    and time_in_force != TimeInForce.DAY
                    and "only day orders are allowed" in str(e).lower()
                ):
                    log.info(
                        "Retrying %s %s as a DAY order — Alpaca flagged it "
                        "hard-to-borrow.",
                        action, ticker,
                    )
                    retried_as_day = True
                    time_in_force = TimeInForce.DAY
                    continue
                # Log and continue — we record the analysis in Supabase either way
                log.warning("Order failed for %s %s: %s", action, ticker, e)
                return OrderResult(
                    submitted=False,
                    client_order_id=client_order_id,
                    error=str(e),
                )

    def place_bracket_orders(
        self,
        ticker: str,
        quantity: int,
        entry_price: float,
        stop_loss_pct: float = 0.03,
        take_profit_pct: float = 0.06,
    ) -> dict:
        """
        DEPRECATED: Bracket legs are now attached atomically via place_order().

        This method is kept for backwards compatibility but now only computes
        and returns the bracket price levels. It does NOT submit separate orders
        (which Alpaca rejects as "potential wash trade").

        The actual bracket submission happens in place_order() when
        take_profit_price and stop_loss_price are provided.
        """
        tp_price = round(entry_price * (1 + take_profit_pct), 2)
        sl_price = round(entry_price * (1 - stop_loss_pct), 2)

        log.info(
            "Bracket prices computed for %s: entry=$%.2f TP=$%.2f (+%.1f%%) SL=$%.2f (-%.1f%%)",
            ticker, entry_price, tp_price, take_profit_pct * 100,
            sl_price, stop_loss_pct * 100,
        )

        return {
            "take_profit_price": tp_price,
            "stop_loss_price": sl_price,
            "entry_price": entry_price,
            "method": "atomic_bracket",
            "status": "attached_to_primary_order",
            "errors": [],
        }

    def verify_fill(self, order_id: str) -> dict:
        """
        Check the fill status of an order.

        Returns filled quantity, average price, and status.
        Non-blocking — returns whatever status is current.
        """
        if self._dry_run:
            return {
                "filled_qty": 0,
                "filled_avg_price": 0,
                "status": "mock",
            }
        try:
            order = self._client.get_order_by_id(order_id)
            return {
                "filled_qty": _floatish(getattr(order, "filled_qty", None)) or 0,
                "filled_avg_price": _floatish(getattr(order, "filled_avg_price", None)) or 0,
                "status": _normalize_status(getattr(order, "status", None)),
            }
        except Exception as exc:
            log.warning("Could not verify fill for order %s: %s", order_id, exc)
            return {
                "filled_qty": 0,
                "filled_avg_price": 0,
                "status": "unknown",
                "error": str(exc),
            }

    def get_all_positions(self) -> list[dict]:
        """Return all open positions as a list of dicts."""
        try:
            positions = self._client.get_all_positions()
            return [
                {
                    "symbol": str(getattr(p, "symbol", "") or ""),
                    "qty": _floatish(getattr(p, "qty", None)) or 0.0,
                    # Shares not tied up in working orders. qty == qty_available
                    # means NO order (bracket leg, stop, anything) holds these
                    # shares — i.e. the position is unprotected.
                    "qty_available": _floatish(getattr(p, "qty_available", None)),
                    "side": _normalize_side(getattr(p, "side", None)),
                    "market_value": _floatish(getattr(p, "market_value", None)),
                    "cost_basis": _floatish(getattr(p, "cost_basis", None)),
                    "avg_entry_price": _floatish(getattr(p, "avg_entry_price", None)),
                    "current_price": _floatish(getattr(p, "current_price", None)),
                    "unrealized_pl": _floatish(getattr(p, "unrealized_pl", None)),
                    "unrealized_plpc": _floatish(getattr(p, "unrealized_plpc", None)),
                }
                for p in positions
            ]
        except Exception as exc:
            log.warning("Could not fetch all positions: %s", exc)
            return []

    def get_account_context(self) -> Optional[dict]:
        """Small, JSON-safe account snapshot used by risk gating and prompts."""
        try:
            account = self._client.get_account()
            return {
                "status": str(getattr(account, "status", "") or ""),
                "currency": str(getattr(account, "currency", "") or ""),
                "trading_blocked": _boolish(getattr(account, "trading_blocked", None)),
                "transfers_blocked": _boolish(
                    getattr(account, "transfers_blocked", None)
                ),
                "account_blocked": _boolish(getattr(account, "account_blocked", None)),
                "shorting_enabled": _boolish(
                    getattr(account, "shorting_enabled", None)
                ),
                "pattern_day_trader": _boolish(
                    getattr(account, "pattern_day_trader", None)
                ),
                "buying_power": _floatish(getattr(account, "buying_power", None)),
                "regt_buying_power": _floatish(
                    getattr(account, "regt_buying_power", None)
                ),
                "daytrading_buying_power": _floatish(
                    getattr(account, "daytrading_buying_power", None)
                ),
                "cash": _floatish(getattr(account, "cash", None)),
                "portfolio_value": _floatish(getattr(account, "portfolio_value", None)),
                "equity": _floatish(getattr(account, "equity", None)),
                "last_equity": _floatish(getattr(account, "last_equity", None)),
                "maintenance_margin": _floatish(
                    getattr(account, "maintenance_margin", None)
                ),
                "daytrade_count": getattr(account, "daytrade_count", None),
            }
        except Exception as exc:
            log.warning("Could not fetch Alpaca account context: %s", exc)
            return None

    # ── Market clock ──────────────────────────────────────────────────────────

    _CLOCK_CACHE_TTL_SECONDS = 60.0

    def get_market_clock(self) -> Optional[dict]:
        """Cached Alpaca market clock. Returns None when the clock can't be read.

        Cached for 60s: the clock backs per-signal gating and the position
        monitor loop, and neither needs sub-minute precision. Returning None on
        failure (instead of guessing) lets each caller pick its own fail
        direction — the entry gate stays permissive (the broker's own
        rejections are the backstop) while ``close_position`` refuses to strip
        protection it can't replace.
        """
        now = _time.time()
        with self._cache_lock:
            fetched_at, cached = self._clock_cache
            if cached is not None and now - fetched_at < self._CLOCK_CACHE_TTL_SECONDS:
                return cached
        try:
            clock = self._client.get_clock()
            result = {
                "is_open": bool(getattr(clock, "is_open", False)),
                "next_open": getattr(clock, "next_open", None),
                "next_close": getattr(clock, "next_close", None),
            }
        except Exception as exc:
            log.warning("Could not fetch Alpaca market clock: %s", exc)
            return None
        with self._cache_lock:
            self._clock_cache = (now, result)
        return result

    def is_market_open(self) -> Optional[bool]:
        """True/False from the cached market clock; None when unknown."""
        clock = self.get_market_clock()
        return None if clock is None else clock["is_open"]

    # ── Independent equity reference (circuit-breaker corroboration) ─────────

    _RISK_CONTEXT_CACHE_TTL_SECONDS = 300.0

    def get_risk_context(self) -> Optional[dict]:
        """Equity figures from Alpaca's *portfolio-history* endpoint.

        The account snapshot (``get_account_context``) is a single point-in-time
        read whose ``equity`` can be corrupted by a bad mark — we observed it
        reporting a −22.7% "daily loss" while the broker's own history showed
        −0.3% (README Bug Log: BUG-2026-07-10-01). Portfolio history is an
        independently computed series, so it corroborates (or refutes) the
        snapshot before the circuit breaker acts on it, and its 1-month maximum
        gives the high-water mark for the total-drawdown breaker.

        Returns ``{"reference_equity", "equity_hwm", "as_of_epoch"}`` with any
        member possibly None, or None entirely on failure. Cached for 5 minutes —
        risk references don't need tick precision, and this keeps the two extra
        REST calls off the per-signal hot path.
        """
        now = _time.time()
        with self._cache_lock:
            fetched_at, cached = self._risk_context_cache
            if cached is not None and now - fetched_at < self._RISK_CONTEXT_CACHE_TTL_SECONDS:
                return cached

        reference_equity: Optional[float] = None
        equity_hwm: Optional[float] = None
        try:
            # 1-month daily series → high-water mark. Includes the base_value so
            # the HWM can't be *below* where the account started the window.
            daily = self._client.get(
                "/account/portfolio/history",
                data={"period": "1M", "timeframe": "1D"},
            )
            equities = [
                _floatish(v) for v in (daily or {}).get("equity") or []
            ]
            positives = [v for v in equities if v is not None and v > 0]
            base_value = _floatish((daily or {}).get("base_value"))
            if base_value is not None and base_value > 0:
                positives.append(base_value)
            if positives:
                equity_hwm = max(positives)
        except Exception as exc:
            log.warning("Portfolio-history HWM fetch failed: %s", exc)

        try:
            # Latest intraday point → independent reference for today's equity.
            intraday = self._client.get(
                "/account/portfolio/history",
                data={
                    "period": "1D",
                    "timeframe": "15Min",
                    "intraday_reporting": "extended_hours",
                },
            )
            for v in reversed((intraday or {}).get("equity") or []):
                value = _floatish(v)
                if value is not None and value > 0:
                    reference_equity = value
                    break
        except Exception as exc:
            log.warning("Portfolio-history intraday fetch failed: %s", exc)

        if reference_equity is None and equity_hwm is None:
            return None
        result = {
            "reference_equity": reference_equity,
            "equity_hwm": equity_hwm,
            "as_of_epoch": now,
        }
        with self._cache_lock:
            self._risk_context_cache = (now, result)
        return result

    def get_position_context(self, ticker: str) -> dict:
        """Return the current ticker position, or an explicit flat position."""
        flat = {
            "symbol": ticker,
            "qty": 0.0,
            "side": "flat",
            "market_value": 0.0,
            "cost_basis": 0.0,
            "avg_entry_price": None,
            "current_price": None,
            "unrealized_pl": 0.0,
            "unrealized_plpc": 0.0,
            "unrealized_intraday_pl": 0.0,
            "unrealized_intraday_plpc": 0.0,
        }

        try:
            position = self._client.get_open_position(ticker)
            return {
                "symbol": str(getattr(position, "symbol", ticker)),
                "qty": _floatish(getattr(position, "qty", None)) or 0.0,
                "side": _normalize_side(getattr(position, "side", None)),
                "market_value": _floatish(getattr(position, "market_value", None)),
                "cost_basis": _floatish(getattr(position, "cost_basis", None)),
                "avg_entry_price": _floatish(
                    getattr(position, "avg_entry_price", None)
                ),
                "current_price": _floatish(getattr(position, "current_price", None)),
                "unrealized_pl": _floatish(getattr(position, "unrealized_pl", None)),
                "unrealized_plpc": _floatish(
                    getattr(position, "unrealized_plpc", None)
                ),
                "unrealized_intraday_pl": _floatish(
                    getattr(position, "unrealized_intraday_pl", None)
                ),
                "unrealized_intraday_plpc": _floatish(
                    getattr(position, "unrealized_intraday_plpc", None)
                ),
            }
        except APIError as exc:
            status_code = getattr(exc, "status_code", None)
            message = str(exc).lower()
            missing_position = (
                "position does not exist" in message or "not found" in message
            )
            if status_code not in (404, 422) and not missing_position:
                log.warning("Could not fetch Alpaca position for %s: %s", ticker, exc)
            return flat
        except Exception as exc:
            log.warning("Could not fetch Alpaca position for %s: %s", ticker, exc)
            return flat

    # Non-terminal order statuses — everything that still holds shares or could
    # still fill. Critically includes ``held``: a bracket's stop-loss leg sits
    # in ``held`` while its take-profit sibling is ``new``, and Alpaca's
    # ``status=open`` filter EXCLUDES ``held`` — so querying ``open`` makes every
    # bracket-native stop invisible, causing false "POSITION HAS NO STOP" alarms
    # and silently breaking trailing-stop ratcheting on bracket positions
    # (README Bug Log: BUG-2026-07-15-02).
    _ACTIVE_ORDER_STATUSES = {
        "new", "accepted", "pending_new", "accepted_for_bidding",
        "partially_filled", "held", "pending_replace", "pending_cancel",
    }

    def get_open_orders(self, ticker: Optional[str] = None) -> list[dict]:
        """Return all *working* orders (incl. held bracket legs), opt. by ticker.

        Fetched via RAW REST rather than the SDK's typed models, for reasons
        accumulated across production incidents:
          1. alpaca-py 0.26's Order model predates ``position_intent`` and
             silently drops it — which turned the intent-filtered reaper and
             the zombie gauge into no-ops (BUG-2026-07-14-01).
          2. The endpoint's default ``limit`` is 50; with ~100 working orders
             the OLDEST half — exactly where zombies live — was invisible.
          3. Raw JSON carries plain lowercase tokens, immune to the enum-prefix
             normalization bugs that have bitten the SDK path repeatedly.
          4. We query ``status=all`` and filter to non-terminal statuses
             client-side, because ``status=open`` drops ``held`` bracket stop
             legs (BUG-2026-07-15-02). Working orders are always recent, so the
             500-row window reliably contains them.
        """
        if self._dry_run:
            return []
        try:
            params: dict[str, Any] = {"status": "all", "limit": 500,
                                      "nested": "false"}
            if ticker:
                params["symbols"] = ticker
            raw = self._client.get("/orders", data=params) or []
            raw = [
                o for o in raw
                if _normalize_status(o.get("status")) in self._ACTIVE_ORDER_STATUSES
            ]
            return [
                {
                    "id": str(o.get("id") or ""),
                    "symbol": str(o.get("symbol") or ""),
                    "side": _enum_token(o.get("side")),
                    "type": _enum_token(o.get("type")),
                    "position_intent": _enum_token(o.get("position_intent")),
                    "qty": _floatish(o.get("qty")) or 0.0,
                    "filled_qty": _floatish(o.get("filled_qty")) or 0.0,
                    "created_at": _parse_iso_utc(o.get("created_at")),
                    "stop_price": _floatish(o.get("stop_price")),
                    "limit_price": _floatish(o.get("limit_price")),
                    "status": _normalize_status(o.get("status")),
                    "order_class": str(o.get("order_class") or ""),
                    "legs": [
                        {
                            "id": str(leg.get("id") or ""),
                            "type": _enum_token(leg.get("type")),
                            "side": _enum_token(leg.get("side")),
                            "stop_price": _floatish(leg.get("stop_price")),
                            "limit_price": _floatish(leg.get("limit_price")),
                            "status": _normalize_status(leg.get("status")),
                        }
                        for leg in (o.get("legs") or [])
                    ],
                }
                for o in raw
            ]
        except Exception as exc:
            log.warning("Could not fetch open orders%s: %s",
                        f" for {ticker}" if ticker else "", exc)
            return []

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID. Returns True on success."""
        if self._dry_run:
            log.info("MOCK CANCEL (Dry Run): order_id=%s", order_id)
            return True
        try:
            self._client.cancel_order_by_id(order_id)
            log.info("Cancelled order: %s", order_id)
            return True
        except Exception as exc:
            log.warning("Could not cancel order %s: %s", order_id, exc)
            return False

    def reap_stale_entry_orders(self, max_age_seconds: float) -> int:
        """Cancel unfilled *entry* orders older than ``max_age_seconds``.

        An entry that is still open with zero fills after a few minutes is a
        missed catalyst — under GTC it would otherwise linger for ~90 days and
        could fill weeks later on dead news. Cancelling a bracket parent cancels
        its child legs too, which is correct since no position exists.
        (See README Bug Log: BUG-2026-06-08-02)

        We reap strictly by position_intent in {``buy_to_open``,
        ``sell_to_open``} — the two intents that *open* positions — never by
        side. Side is the wrong axis in both directions: a protected short's
        take-profit/stop-loss legs are BUY side (``buy_to_close``), so a
        buy-side filter cancels the short's protection (README Bug Log:
        BUG-2026-07-01-01); and a ``sell_to_open`` short entry is SELL side, so
        the old buy-side pre-filter made short-entry zombies invisible to the
        reaper forever — four of them survived 4+ days in prod (README Bug Log:
        BUG-2026-07-13-02). When Alpaca doesn't report an intent we skip (fail
        safe toward keeping protection).

        Returns the number of orders cancelled.
        """
        if self._dry_run:
            return 0
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        reaped = 0
        for o in self.get_open_orders():
            # Only genuine entry orders may be reaped. *_to_close orders are a
            # position's protection; cancelling them is exactly the bug class
            # we're guarding against. Missing/empty intent → skip rather than
            # risk stripping protection.
            if o.get("position_intent") not in ("buy_to_open", "sell_to_open"):
                continue
            if (o.get("filled_qty") or 0.0) > 0:
                continue  # partially filled — a position exists, leave it
            created = o.get("created_at")
            if created is None:
                continue
            try:
                age = (now - created).total_seconds()
            except Exception:
                continue
            if age < max_age_seconds:
                continue
            if self.cancel_order(o["id"]):
                reaped += 1
                log.info(
                    "Reaped stale entry order %s (%s, age=%.0fs, unfilled)",
                    o["id"], o.get("symbol"), age,
                )
        return reaped

    def place_stop_order(
        self,
        ticker: str,
        quantity: int,
        stop_price: float,
        side: str = "sell",
    ) -> OrderResult:
        """Place a standalone stop order (used for trailing stop replacement)."""
        if self._dry_run:
            import uuid
            mock_id = str(uuid.uuid4())
            log.info(
                "MOCK STOP ORDER (Dry Run): %s %d %s @ stop=$%.2f → order_id=%s",
                side, quantity, ticker, stop_price, mock_id,
            )
            return OrderResult(
                submitted=True,
                order_id=mock_id,
                status="accepted",
            )

        order_side = OrderSide.SELL if side.lower() == "sell" else OrderSide.BUY
        try:
            order_request = StopOrderRequest(
                symbol=ticker,
                qty=quantity,
                side=order_side,
                stop_price=round(stop_price, 2),
                time_in_force=TimeInForce.GTC,
            )
            order = self._client.submit_order(order_data=order_request)
            order_id = str(getattr(order, "id", "") or "")
            status = _normalize_status(getattr(order, "status", None))
            log.info(
                "Stop order submitted: %s %d %s @ stop=$%.2f → order_id=%s",
                side, quantity, ticker, stop_price, order_id,
            )
            return OrderResult(
                submitted=True,
                order_id=order_id,
                status=status,
            )
        except APIError as e:
            log.warning("Stop order failed for %s %s: %s", side, ticker, e)
            return OrderResult(submitted=False, error=str(e))

    def replace_stop_order(self, order_id: str, stop_price: float) -> OrderResult:
        """Atomically move an existing stop order's trigger price via PATCH.

        Replacement is broker-side atomic: the order keeps working at the old
        price until the new one is accepted, so there is NO window where the
        position is unprotected — unlike cancel-then-place, which stripped 66
        positions of their stops in one sweep when the second step was rejected
        (README Bug Log: BUG-2026-07-14-02). Returns the replacement order's id
        on success (Alpaca issues a new id).
        """
        if self._dry_run:
            import uuid
            return OrderResult(submitted=True, order_id=str(uuid.uuid4()),
                               status="accepted")
        try:
            from alpaca.trading.requests import ReplaceOrderRequest
            order = self._client.replace_order_by_id(
                order_id, ReplaceOrderRequest(stop_price=round(stop_price, 2))
            )
            return OrderResult(
                submitted=True,
                order_id=str(getattr(order, "id", "") or ""),
                status=_normalize_status(getattr(order, "status", None)),
            )
        except Exception as e:
            log.warning("Stop replace failed for order %s: %s", order_id, e)
            return OrderResult(submitted=False, error=str(e))

    def close_position(self, ticker: str) -> OrderResult:
        """Flatten the entire position in ``ticker`` with a market order.

        Cancels any working orders for the symbol first — a leftover protective
        stop on a now-flat position could later fire and open an *unwanted* new
        position — then asks Alpaca to liquidate. Works for both longs and
        shorts; Alpaca submits the offsetting side automatically. Used by the
        time-based exit (see position_monitor).

        Two hazards guarded here (README Bug Log: BUG-2026-07-10-03):

        1. *Market closed*: the cancel step succeeds 24/7 but the market-order
           liquidation is rejected outside regular hours — which used to strip a
           position's protective stops and then fail to flatten it, leaving it
           naked overnight. So when the market isn't verifiably open, we refuse
           up front and leave every working order intact; the caller simply
           retries next cycle.
        2. *Close fails after cancel*: any protective stop we cancelled is
           re-placed at its previous price so the position is never left less
           protected than we found it.
        """
        if self._dry_run:
            import uuid
            mock_id = str(uuid.uuid4())
            log.info("MOCK CLOSE (Dry Run): %s → order_id=%s", ticker, mock_id)
            return OrderResult(submitted=True, order_id=mock_id, status="accepted")

        if self.is_market_open() is not True:
            return OrderResult(
                submitted=False,
                error=(
                    "Market is not verifiably open; deferring close so protective "
                    "orders are not cancelled ahead of a liquidation that would "
                    "be rejected."
                ),
            )

        # Snapshot the protective stops we're about to cancel so they can be
        # restored if the liquidation itself fails.
        cancelled_stops: list[dict] = []
        try:
            for o in self.get_open_orders(ticker):
                if not o.get("id"):
                    continue
                if o.get("type") == "stop" and o.get("stop_price"):
                    cancelled_stops.append(o)
                for leg in o.get("legs", []) or []:
                    if (
                        leg.get("type") == "stop"
                        and leg.get("stop_price")
                        and str(leg.get("status", "")) in ("new", "accepted", "pending_new", "held")
                    ):
                        cancelled_stops.append({**leg, "symbol": ticker, "qty": o.get("qty")})
                self.cancel_order(o["id"])
            order = self._client.close_position(ticker)
            order_id = str(getattr(order, "id", "") or "")
            status = _normalize_status(getattr(order, "status", None))
            log.info("Close-position submitted: %s → order_id=%s", ticker, order_id)
            return OrderResult(submitted=True, order_id=order_id, status=status)
        except Exception as e:
            log.warning("Close-position failed for %s: %s", ticker, e)
            self._restore_protective_stops(ticker, cancelled_stops)
            return OrderResult(submitted=False, error=str(e))

    def _restore_protective_stops(self, ticker: str, stops: list[dict]) -> None:
        """Re-place protective stops that were cancelled ahead of a failed close.

        Best-effort: sizes each stop from its original qty, falling back to the
        live position size. Failure here is logged CRITICAL — a naked position
        is exactly the state this trade-off exists to prevent — and the position
        monitor's stop reconciliation pass is the second line of defence.
        """
        if not stops:
            return
        position_qty: Optional[float] = None
        for stop in stops:
            try:
                qty = _floatish(stop.get("qty"))
                if not qty or qty <= 0:
                    if position_qty is None:
                        position_qty = abs(
                            _floatish(self.get_position_context(ticker).get("qty")) or 0.0
                        )
                    qty = position_qty
                if not qty or int(qty) < 1:
                    raise ValueError(f"no usable qty (order={stop.get('qty')})")
                result = self.place_stop_order(
                    ticker=ticker,
                    quantity=int(qty),
                    stop_price=float(stop["stop_price"]),
                    side=str(stop.get("side") or "sell"),
                )
                if not result.submitted:
                    raise RuntimeError(result.error or "submit failed")
                log.info(
                    "Restored protective stop for %s @ $%.2f after failed close",
                    ticker, float(stop["stop_price"]),
                )
            except Exception as exc:
                log.critical(
                    "POSITION MAY BE UNPROTECTED: could not restore stop for %s "
                    "(stop_price=%s) after a failed close: %s",
                    ticker, stop.get("stop_price"), exc,
                )

    def get_position_entry_time(self, ticker: str, side: str = "long"):
        """Best-effort UTC timestamp of the fill that opened the position.

        Lets the time-based exit survive a process restart without resetting
        every position's hold clock. Scans recently-closed orders for the
        symbol and returns the most recent *entry-side* fill (BUY for a long,
        SELL for a short). Returns None when it can't be determined, in which
        case the caller falls back to when it first observed the position.
        """
        if self._dry_run:
            return None
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            entry_side = "buy" if str(side).lower() == "long" else "sell"
            request = GetOrdersRequest(
                status=QueryOrderStatus.CLOSED,
                symbols=[ticker],
                limit=50,
            )
            orders = self._client.get_orders(filter=request)
            entry_fills = [
                getattr(o, "filled_at", None)
                for o in orders
                if _enum_token(getattr(o, "side", None)) == entry_side
                and getattr(o, "filled_at", None)
            ]
            if entry_fills:
                return max(entry_fills)
        except Exception as exc:
            log.debug("Entry-time lookup failed for %s: %s", ticker, exc)
        return None

