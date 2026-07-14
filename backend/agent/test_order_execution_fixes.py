"""
Tests for three execution fixes found in the 2026-06-25 health review:

  Bug #1  A bracket order must be an *entry* order — if a position or working
          order already exists, fall back to a plain order instead of letting
          Alpaca reject (and drop) the whole trade.
  Bug #2  Hard-to-borrow assets can only be shorted with a DAY order — retry
          once as DAY on that specific rejection instead of dropping the trade.
  Bug #3  Time-based exit — flatten a position once it has been held past the
          max-hold window, harvesting the early move before it decays.

These drive ``AlpacaTrader``/``position_monitor`` against tiny fake clients so
no network or real Alpaca account is needed.
"""

from __future__ import annotations

import time
import types

import config
import position_monitor as pm
import trader as trader_mod
from trader import AlpacaTrader, OrderResult


# ── Fakes ────────────────────────────────────────────────────────────────────


class _FakeOrder:
    def __init__(self, oid: str = "oid-1", status: str = "accepted") -> None:
        self.id = oid
        self.status = status


class _FakeClient:
    """Minimal stand-in for alpaca-py's TradingClient."""

    def __init__(self, *, position=None, open_orders=None, htb_symbols=()) -> None:
        self._position = position
        self._open_orders = open_orders or []
        self._htb = set(htb_symbols)
        self.submitted: list = []  # every order_data we were asked to submit

    # used by get_position_context (via _can_open_bracket)
    def get_open_position(self, ticker):
        if self._position is None:
            raise trader_mod.APIError("position does not exist")
        return self._position

    # used by get_open_orders (via _can_open_bracket) — raw REST path
    def get(self, path, data=None):
        assert path == "/orders"
        return [_raw_order(o) for o in self._open_orders]

    def submit_order(self, order_data=None):
        self.submitted.append(order_data)
        symbol = getattr(order_data, "symbol", None)
        tif = trader_mod._enum_token(getattr(order_data, "time_in_force", None))
        # Simulate Alpaca rejecting a non-DAY order for a hard-to-borrow asset.
        if symbol in self._htb and tif != "day":
            raise trader_mod.APIError(
                '{"code":42210000,"message":"only day orders are allowed '
                'for hard-to-borrow asset"}'
            )
        return _FakeOrder()


def _raw_order(o) -> dict:
    """Render a fake order object as the raw REST dict get_open_orders now consumes."""
    return {
        "id": getattr(o, "id", ""),
        "symbol": getattr(o, "symbol", ""),
        "side": getattr(o, "side", None),
        "type": getattr(o, "type", None),
        "position_intent": getattr(o, "position_intent", None),
        "qty": getattr(o, "qty", None),
        "filled_qty": getattr(o, "filled_qty", None),
        "created_at": getattr(o, "created_at", None),
        "stop_price": getattr(o, "stop_price", None),
        "limit_price": getattr(o, "limit_price", None),
        "status": getattr(o, "status", None),
        "order_class": getattr(o, "order_class", ""),
        "legs": getattr(o, "legs", None) or [],
    }


def _make_trader(client: _FakeClient) -> AlpacaTrader:
    t = AlpacaTrader.__new__(AlpacaTrader)  # bypass __init__ (no real client/env)
    t._dry_run = False
    t._client = client
    t._data_client = None
    # Skip the live-price bracket sanity check — irrelevant here and offline.
    t._latest_trade_price = lambda ticker: None  # type: ignore[assignment]
    return t


def _is_bracket(order_data) -> bool:
    return trader_mod._enum_token(getattr(order_data, "order_class", None)) == "bracket"


# ── Bug #1: bracket only on an entry (flat) position ──────────────────────────


def test_bracket_used_when_flat():
    client = _FakeClient(position=None, open_orders=[])
    trader = _make_trader(client)

    res = trader.place_order(
        "MU", "BUY", quantity=1, take_profit_price=110.0, stop_loss_price=90.0
    )

    assert res.submitted
    assert len(client.submitted) == 1
    assert _is_bracket(client.submitted[0])  # genuine entry → bracket attached


def test_bracket_falls_back_to_simple_when_position_exists():
    held = types.SimpleNamespace(symbol="MU", qty="10", side="long")
    client = _FakeClient(position=held, open_orders=[])
    trader = _make_trader(client)

    res = trader.place_order(
        "MU", "BUY", quantity=1, take_profit_price=110.0, stop_loss_price=90.0
    )

    assert res.submitted  # the trade still goes through …
    assert not _is_bracket(client.submitted[0])  # … but as a plain order


def test_bracket_falls_back_when_working_order_exists():
    client = _FakeClient(position=None, open_orders=[_FakeOrder()])
    trader = _make_trader(client)

    res = trader.place_order(
        "MU", "BUY", quantity=1, take_profit_price=110.0, stop_loss_price=90.0
    )

    assert res.submitted
    assert not _is_bracket(client.submitted[0])


# ── Bug #2: hard-to-borrow short retries as a DAY order ───────────────────────


def test_short_on_hard_to_borrow_retries_as_day():
    client = _FakeClient(position=None, open_orders=[], htb_symbols={"ATLN"})
    trader = _make_trader(client)

    # SELL with mirrored legs (target below < stop above) opens a short bracket,
    # whose default time-in-force is GTC — which Alpaca rejects for an HTB name.
    res = trader.place_order(
        "ATLN", "SELL", quantity=1, take_profit_price=90.0, stop_loss_price=110.0
    )

    assert res.submitted
    assert len(client.submitted) == 2  # first GTC rejected, retried once
    assert trader_mod._enum_token(client.submitted[0].time_in_force) != "day"
    assert trader_mod._enum_token(client.submitted[1].time_in_force) == "day"


def test_non_htb_order_is_not_retried():
    client = _FakeClient(position=None, open_orders=[])
    trader = _make_trader(client)

    res = trader.place_order(
        "AAPL", "SELL", quantity=1, take_profit_price=90.0, stop_loss_price=110.0
    )

    assert res.submitted
    assert len(client.submitted) == 1  # accepted first try, no retry


# ── Bug #3: time-based exit ───────────────────────────────────────────────────


class _ExitTrader:
    def __init__(self) -> None:
        self.closed: list[str] = []

    def get_position_entry_time(self, ticker, side="long"):
        return None  # force the first-observed fallback clock

    def close_position(self, ticker):
        self.closed.append(ticker)
        return OrderResult(submitted=True, order_id="close-1", status="accepted")


def test_time_exit_holds_then_closes(monkeypatch):
    pm._clear_tracking()
    monkeypatch.setattr(config, "MAX_POSITION_HOLD_SECONDS", 3600, raising=False)
    trader = _ExitTrader()

    # Freshly seen → clock starts now, well under the window → no close.
    assert pm._maybe_time_exit(trader, "MU", "long") is False
    assert trader.closed == []

    # Age the clock past the window → it flattens exactly once.
    pm._position_first_seen["MU"] = time.time() - 4000
    assert pm._maybe_time_exit(trader, "MU", "long") is True
    assert trader.closed == ["MU"]

    # A close is already in flight → don't submit a second one.
    assert pm._maybe_time_exit(trader, "MU", "long") is True
    assert trader.closed == ["MU"]

    pm._clear_tracking()


# ── Bug #4: reaper must not cancel a short's protective buy_to_close legs ──────


class _FakeOpenOrder:
    """Stand-in for an alpaca-py order returned by get_orders(OPEN)."""

    def __init__(self, *, oid, side, position_intent, filled_qty="0",
                 age_seconds=3600.0, symbol="MSTR") -> None:
        from datetime import datetime, timedelta, timezone

        self.id = oid
        self.symbol = symbol
        self.side = side
        self.type = "stop"
        self.position_intent = position_intent
        self.qty = "10"
        self.filled_qty = filled_qty
        self.created_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        self.stop_price = "100"
        self.limit_price = None
        self.status = "new"
        self.order_class = "bracket"
        self.legs = []


class _ReapClient:
    def __init__(self, open_orders) -> None:
        self._open_orders = open_orders
        self.cancelled: list[str] = []

    def get(self, path, data=None):
        assert path == "/orders"
        return [_raw_order(o) for o in self._open_orders]

    def cancel_order_by_id(self, order_id):
        self.cancelled.append(order_id)


def test_reaper_skips_short_protective_buy_to_close_leg():
    # An old, unfilled BUY leg that protects a short (buy_to_close) must survive.
    protective = _FakeOpenOrder(oid="prot-1", side="buy",
                                position_intent="buy_to_close", age_seconds=4000)
    client = _ReapClient([protective])
    trader = _make_trader(client)

    reaped = trader.reap_stale_entry_orders(600)

    assert reaped == 0
    assert client.cancelled == []  # the short keeps its stop/take-profit


def test_reaper_still_cancels_stale_buy_to_open_entry():
    # A genuine zombie entry (buy_to_open, unfilled, old) is still reaped.
    zombie = _FakeOpenOrder(oid="entry-1", side="buy",
                            position_intent="buy_to_open", age_seconds=4000)
    client = _ReapClient([zombie])
    trader = _make_trader(client)

    reaped = trader.reap_stale_entry_orders(600)

    assert reaped == 1
    assert client.cancelled == ["entry-1"]


def test_reaper_skips_intentless_buy_order():
    # No intent reported → err toward safety and leave it alone.
    unknown = _FakeOpenOrder(oid="mystery-1", side="buy",
                             position_intent=None, age_seconds=4000)
    client = _ReapClient([unknown])
    trader = _make_trader(client)

    assert trader.reap_stale_entry_orders(600) == 0
    assert client.cancelled == []
