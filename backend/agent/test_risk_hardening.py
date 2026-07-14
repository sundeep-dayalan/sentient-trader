"""
Tests for the 2026-07-10 risk-hardening fixes (see README Bug Log):

  BUG-2026-07-10-01  Circuit breaker trusted a single unvalidated account
                     snapshot — cross-check against portfolio-history equity,
                     fail closed on missing data, and add a total-drawdown
                     floor the daily limit is blind to.
  BUG-2026-07-10-02  Risk gates blocked de-risking SELLs — capital-preservation
                     gates now apply only to risk-increasing orders.
  BUG-2026-07-10-03  Off-hours close cancelled protective legs then failed to
                     liquidate — close_position refuses off-hours and restores
                     stops when the close fails after cancellation.
  BUG-2026-07-10-04  market_hours_awareness was loaded but never enforced.
  BUG-2026-07-10-05  Naked positions were never re-protected — reconciliation
                     sweep + trailing-replacement failure restores the stop.

All offline: tiny fakes, no network, no real Alpaca account.
"""

from __future__ import annotations

import types

import config
import position_monitor as pm
from decision_rules import build_execution_plan
from position_manager import (
    check_total_drawdown,
    equity_snapshot_consistent,
)
from trader import AlpacaTrader, OrderResult


# ── position_manager: total drawdown ─────────────────────────────────────────


def test_total_drawdown_trips_beyond_limit():
    result = check_total_drawdown(
        equity=94_000.0, high_water_mark=100_000.0, max_total_drawdown_pct=0.05
    )
    assert result.is_tripped
    assert result.drawdown_pct == -0.06


def test_total_drawdown_ok_within_limit():
    result = check_total_drawdown(
        equity=97_000.0, high_water_mark=100_000.0, max_total_drawdown_pct=0.05
    )
    assert not result.is_tripped


def test_total_drawdown_inactive_without_data():
    assert not check_total_drawdown(equity=None, high_water_mark=100_000.0).is_tripped
    assert not check_total_drawdown(equity=100_000.0, high_water_mark=None).is_tripped


# ── position_manager: snapshot consistency ───────────────────────────────────


def test_snapshot_consistent_within_tolerance():
    assert equity_snapshot_consistent(49_000.0, 49_300.0) is True


def test_snapshot_inconsistent_on_corrupt_equity():
    # The observed Jul 7 incident: snapshot ~38k vs broker history ~49.2k.
    assert equity_snapshot_consistent(38_100.0, 49_200.0) is False


def test_snapshot_consistency_unknown_when_reference_missing():
    assert equity_snapshot_consistent(49_000.0, None) is None
    assert equity_snapshot_consistent(None, 49_000.0) is None


# ── decision_rules: execution-gate policies ──────────────────────────────────


def _ctx(*, equity=50_000.0, last_equity=50_000.0, reference_equity=None,
         equity_hwm=None, market_open=None, position_qty=0.0, price=100.0):
    account = {
        "buying_power": 1_000_000.0,
        "portfolio_value": equity,
        "equity": equity,
        "last_equity": last_equity,
    }
    if reference_equity is not None:
        account["reference_equity"] = reference_equity
    if equity_hwm is not None:
        account["equity_hwm"] = equity_hwm
    if market_open is not None:
        account["market_open"] = market_open
    return {
        "price": price,
        "account": account,
        "position": {"symbol": "MU", "qty": position_qty},
    }


def _breaker_on(monkeypatch, **overrides):
    defaults = dict(
        CIRCUIT_BREAKER_ENABLED=True,
        MAX_DAILY_LOSS_PCT=0.02,
        MAX_TOTAL_DRAWDOWN_PCT=0.05,
        MARKET_HOURS_AWARENESS_ENABLED=False,
    )
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setattr(config, key, value, raising=False)


def test_breaker_fails_closed_when_equity_missing(monkeypatch):
    _breaker_on(monkeypatch)
    ctx = _ctx()
    ctx["account"]["equity"] = None
    plan = build_execution_plan(action="BUY", order_qty=1, market_context=ctx)
    assert any("failing closed" in r for r in plan["blocked_reasons"])


def test_breaker_never_blocks_a_derisking_sell(monkeypatch):
    _breaker_on(monkeypatch)
    # Equity data missing AND market closed — worst case — but the SELL reduces
    # an existing long, so no capital-preservation gate may touch it.
    _breaker_on(monkeypatch, MARKET_HOURS_AWARENESS_ENABLED=True)
    ctx = _ctx(position_qty=10.0, market_open=False)
    ctx["account"]["equity"] = None
    plan = build_execution_plan(action="SELL", order_qty=5, market_context=ctx)
    assert plan["blocked_reasons"] == []
    assert plan["quantity"] == 5


def test_breaker_blocks_on_inconsistent_snapshot_with_data_reason(monkeypatch):
    _breaker_on(monkeypatch)
    # Jul 7 scenario: snapshot says -22.7%, broker history says otherwise.
    ctx = _ctx(equity=38_100.0, last_equity=49_300.0, reference_equity=49_200.0)
    plan = build_execution_plan(action="BUY", order_qty=1, market_context=ctx)
    assert any("snapshot inconsistent" in r.lower() for r in plan["blocked_reasons"])
    # It must NOT report a phantom daily-loss percentage as the reason.
    assert not any("Daily loss limit" in r for r in plan["blocked_reasons"])


def test_breaker_daily_loss_still_trips_when_snapshot_corroborated(monkeypatch):
    _breaker_on(monkeypatch)
    # A real -3% day: snapshot and reference agree.
    ctx = _ctx(equity=48_500.0, last_equity=50_000.0, reference_equity=48_600.0)
    plan = build_execution_plan(action="BUY", order_qty=1, market_context=ctx)
    assert any("Daily loss limit" in r for r in plan["blocked_reasons"])


def test_total_drawdown_floor_blocks_slow_bleed(monkeypatch):
    _breaker_on(monkeypatch)
    # Down only 0.4% on the day (daily breaker silent) but 6% off the HWM.
    ctx = _ctx(equity=47_000.0, last_equity=47_200.0,
               reference_equity=47_050.0, equity_hwm=50_000.0)
    plan = build_execution_plan(action="BUY", order_qty=1, market_context=ctx)
    assert any("Total drawdown limit" in r for r in plan["blocked_reasons"])
    assert plan["drawdown_breaker"]["tripped"]


def test_healthy_account_is_not_blocked(monkeypatch):
    _breaker_on(monkeypatch)
    ctx = _ctx(equity=49_900.0, last_equity=50_000.0,
               reference_equity=49_950.0, equity_hwm=50_000.0)
    plan = build_execution_plan(action="BUY", order_qty=1, market_context=ctx)
    assert plan["blocked_reasons"] == []


def test_market_hours_gate_blocks_entry_when_closed(monkeypatch):
    _breaker_on(monkeypatch, CIRCUIT_BREAKER_ENABLED=False,
                MARKET_HOURS_AWARENESS_ENABLED=True)
    plan = build_execution_plan(
        action="BUY", order_qty=1, market_context=_ctx(market_open=False)
    )
    assert any("Market is closed" in r for r in plan["blocked_reasons"])


def test_market_hours_gate_permissive_when_clock_unknown(monkeypatch):
    _breaker_on(monkeypatch, CIRCUIT_BREAKER_ENABLED=False,
                MARKET_HOURS_AWARENESS_ENABLED=True)
    # market_open absent (clock endpoint failed) → broker rejection is the
    # backstop; we must not halt all trading on a flaky clock.
    plan = build_execution_plan(
        action="BUY", order_qty=1, market_context=_ctx()
    )
    assert plan["blocked_reasons"] == []


# ── trader.close_position: protection-preserving flatten ─────────────────────


def _make_trader() -> AlpacaTrader:
    t = AlpacaTrader.__new__(AlpacaTrader)  # bypass __init__ (offline)
    t._dry_run = False
    t._data_client = None
    return t


def test_close_refuses_when_market_not_open():
    trader = _make_trader()
    trader.is_market_open = lambda: False  # type: ignore[method-assign]
    trader.get_open_orders = lambda t=None: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("must not touch working orders when refusing to close")
    )
    result = trader.close_position("MU")
    assert not result.submitted
    assert "deferring close" in (result.error or "").lower()


def test_close_restores_stop_when_liquidation_fails():
    trader = _make_trader()
    trader.is_market_open = lambda: True  # type: ignore[method-assign]
    cancelled: list[str] = []
    restored: list[tuple] = []
    trader.get_open_orders = lambda t=None: [  # type: ignore[method-assign]
        {"id": "stop-1", "type": "stop", "side": "sell", "stop_price": 95.0,
         "qty": 10.0, "legs": []},
    ]
    trader.cancel_order = lambda oid: cancelled.append(oid) or True  # type: ignore[method-assign]
    trader.place_stop_order = lambda **kw: restored.append(  # type: ignore[method-assign]
        (kw["ticker"], kw["quantity"], kw["stop_price"], kw["side"])
    ) or OrderResult(submitted=True, order_id="restored-1", status="accepted")

    def _boom(ticker):
        raise RuntimeError("liquidation rejected")

    trader._client = types.SimpleNamespace(close_position=_boom)

    result = trader.close_position("MU")
    assert not result.submitted
    assert cancelled == ["stop-1"]
    assert restored == [("MU", 10, 95.0, "sell")]  # protection re-armed


# ── position_monitor: protective-stop reconciliation ─────────────────────────


class _ReconTrader:
    def __init__(self, open_orders_by_symbol=None) -> None:
        self._orders = open_orders_by_symbol or {}
        self.placed: list[dict] = []

    def get_open_orders(self, symbol=None):
        return self._orders.get(symbol, [])

    def place_stop_order(self, *, ticker, quantity, stop_price, side):
        self.placed.append(
            {"ticker": ticker, "qty": quantity, "stop": stop_price, "side": side}
        )
        return OrderResult(submitted=True, order_id=f"stop-{ticker}", status="accepted")


def _position(symbol, side, qty, qty_available, entry, current):
    return {
        "symbol": symbol, "side": side, "qty": qty,
        "qty_available": qty_available,
        "avg_entry_price": entry, "current_price": current,
    }


def test_reconciliation_arms_stop_on_naked_long(monkeypatch):
    pm._clear_tracking()
    monkeypatch.setattr(config, "STOP_LOSS_PCT", 0.03, raising=False)
    trader = _ReconTrader()
    # In-profit long: anchor is the LOWER of entry/current → entry-based stop.
    pm._ensure_protective_stops(trader, [_position("B", "long", 18, 18, 36.05, 36.68)])
    assert trader.placed == [
        {"ticker": "B", "qty": 18, "stop": round(36.05 * 0.97, 2), "side": "sell"}
    ]


def test_reconciliation_arms_buy_stop_on_naked_short_without_forcing_loss(monkeypatch):
    pm._clear_tracking()
    monkeypatch.setattr(config, "STOP_LOSS_PCT", 0.03, raising=False)
    trader = _ReconTrader()
    # Short already 11% underwater (the observed MAGS case): the stop anchors
    # to the HIGHER of entry/current, giving policy room from *here* instead of
    # liquidating instantly — bounded future risk, no forced realization.
    pm._ensure_protective_stops(
        trader, [_position("MAGS", "short", -6, -6, 60.75, 67.68)]
    )
    assert trader.placed == [
        {"ticker": "MAGS", "qty": 6, "stop": round(67.68 * 1.03, 2), "side": "buy"}
    ]


def test_reconciliation_skips_protected_position(monkeypatch):
    pm._clear_tracking()
    monkeypatch.setattr(config, "STOP_LOSS_PCT", 0.03, raising=False)
    protected_stop = {"id": "s1", "symbol": "AMZN", "side": "sell", "type": "stop",
                      "status": "new", "stop_price": 230.0, "legs": []}
    trader = _ReconTrader({"AMZN": [protected_stop]})
    pm._ensure_protective_stops(
        trader, [_position("AMZN", "long", 4, 1, 240.0, 245.0)]
    )
    assert trader.placed == []


def test_reconciliation_never_cancels_to_make_room(monkeypatch):
    pm._clear_tracking()
    monkeypatch.setattr(config, "STOP_LOSS_PCT", 0.03, raising=False)
    # All shares held by a lone take-profit limit (no stop): escalation only.
    tp_only = {"id": "tp1", "symbol": "KGS", "side": "sell", "type": "limit",
               "status": "new", "limit_price": 75.0, "legs": []}
    trader = _ReconTrader({"KGS": [tp_only]})
    pm._ensure_protective_stops(
        trader, [_position("KGS", "long", 12, 0, 68.70, 67.84)]
    )
    assert trader.placed == []  # nothing placed, nothing cancelled


# ── BUG-2026-07-13-01: wedge-proofing the monitor ─────────────────────────────


def test_harden_alpaca_client_injects_default_timeout():
    from trader import harden_alpaca_client

    calls: list[dict] = []

    class _Session:
        def request(self, *args, **kwargs):
            calls.append(kwargs)
            return "ok"

    class _Client:
        _session = _Session()

    client = _Client()
    harden_alpaca_client(client, timeout_seconds=7.0)
    client._session.request("GET", "/v2/clock")
    assert calls[0]["timeout"] == 7.0
    # An explicit caller timeout must win over the default.
    client._session.request("GET", "/v2/clock", timeout=3.0)
    assert calls[1]["timeout"] == 3.0
    # Idempotent: wrapping twice must not stack wrappers.
    first_wrapper = client._session.request
    harden_alpaca_client(client, timeout_seconds=7.0)
    assert client._session.request is first_wrapper


def test_monitor_loop_exits_when_superseded():
    # A zombie loop whose generation was bumped must exit immediately —
    # before touching the trader — instead of double-managing orders.
    class _MustNotBeUsed:
        def __getattr__(self, name):
            raise AssertionError("superseded loop must not act")

    pm._monitor_generation[0] = 5
    try:
        pm._monitor_loop(_MustNotBeUsed(), lock=None, generation=4)  # returns at once
    finally:
        pm._monitor_generation[0] = 0


# ── BUG-2026-07-13-02: reaper must also reap sell_to_open (short-entry) zombies ─


def test_reaper_cancels_stale_sell_to_open_zombie():
    from test_order_execution_fixes import _FakeOpenOrder, _ReapClient, _make_trader as _mk

    zombie = _FakeOpenOrder(oid="short-entry-1", side="sell",
                            position_intent="sell_to_open", age_seconds=4000)
    client = _ReapClient([zombie])
    trader = _mk(client)

    assert trader.reap_stale_entry_orders(600) == 1
    assert client.cancelled == ["short-entry-1"]


def test_reaper_still_skips_sell_to_close_protection():
    from test_order_execution_fixes import _FakeOpenOrder, _ReapClient, _make_trader as _mk

    # A long's protective stop leg is sell-side sell_to_close — must survive.
    protective = _FakeOpenOrder(oid="prot-long-1", side="sell",
                                position_intent="sell_to_close", age_seconds=4000)
    client = _ReapClient([protective])
    trader = _mk(client)

    assert trader.reap_stale_entry_orders(600) == 0
    assert client.cancelled == []


# ── Invariant gauges published to /metrics via worker health ─────────────────


def test_compute_invariants_counts_naked_zombies_and_book_size():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    positions = [
        # naked: every share free, no working order holds any of them
        {"qty": 12, "qty_available": 12},
        # protected: shares held by bracket legs / stops
        {"qty": 4, "qty_available": 0},
        # partially held still counts as protected (some order exists)
        {"qty": 9, "qty_available": 4},
        # naked short (negative quantities)
        {"qty": -6, "qty_available": -6},
        # fractional dust — ignored
        {"qty": 0.4, "qty_available": 0.4},
        # broker didn't report availability — nothing safe to infer
        {"qty": 3, "qty_available": None},
    ]
    orders = [
        # zombie: unfilled long entry, 2h old
        {"position_intent": "buy_to_open", "filled_qty": 0.0,
         "created_at": now - timedelta(hours=2)},
        # zombie: unfilled short entry, 1 day old
        {"position_intent": "sell_to_open", "filled_qty": 0.0,
         "created_at": now - timedelta(days=1)},
        # fresh entry — not stale yet
        {"position_intent": "buy_to_open", "filled_qty": 0.0,
         "created_at": now - timedelta(minutes=2)},
        # partially filled — a position exists, not a zombie
        {"position_intent": "buy_to_open", "filled_qty": 3.0,
         "created_at": now - timedelta(hours=5)},
        # protective leg — never a zombie regardless of age
        {"position_intent": "sell_to_close", "filled_qty": 0.0,
         "created_at": now - timedelta(days=30)},
    ]

    inv = pm.compute_invariants(positions, orders, now_epoch=now.timestamp())
    assert inv == {
        "positions_without_stop": 2,
        "stale_entry_orders": 2,
        "open_positions": 6,
    }


def test_publish_health_never_raises(monkeypatch):
    class _ExplodingRedis:
        def hset(self, *a, **k):
            raise RuntimeError("redis down")

    # Must swallow: the safety loop may never die over a health write.
    pm._publish_health(_ExplodingRedis(), {"status": "healthy"})
    pm._publish_health(None, {"status": "healthy"})  # no redis configured


# ── BUG-2026-07-14-01: the monitor saw no positions and half-blind orders ────


def test_normalize_side_handles_sdk_enum_prefix():
    from trader import _normalize_side

    assert _normalize_side("PositionSide.SHORT") == "short"
    assert _normalize_side("PositionSide.LONG") == "long"
    assert _normalize_side("short") == "short"
    assert _normalize_side("long") == "long"
    assert _normalize_side(None) == "long"  # conservative default


def test_get_open_orders_uses_raw_rest_with_high_limit_and_intent():
    from test_order_execution_fixes import _make_trader

    captured: dict = {}

    class _RawClient:
        def get(self, path, data=None):
            captured["path"] = path
            captured["data"] = data
            return [{
                "id": "o1", "symbol": "PSHG", "side": "buy", "type": "limit",
                "position_intent": "buy_to_open", "qty": "439",
                "filled_qty": "0", "created_at": "2026-07-14T13:36:01.739919Z",
                "stop_price": None, "limit_price": "1.68", "status": "new",
                "order_class": "bracket", "legs": None,
            }]

    trader = _make_trader.__wrapped__(_RawClient()) if hasattr(_make_trader, "__wrapped__") else _make_trader(_RawClient())
    orders = trader.get_open_orders()

    assert captured["path"] == "/orders"
    assert captured["data"]["limit"] == 500          # default-50 truncation fixed
    assert orders[0]["position_intent"] == "buy_to_open"  # intent survives (raw)
    assert orders[0]["created_at"].year == 2026      # ISO string parsed to datetime
    # A raw zombie like this must now be reapable end-to-end:
    trader.cancel_order = lambda oid: True  # type: ignore[method-assign]
    assert trader.reap_stale_entry_orders(600) == 1


# ── BUG-2026-07-14-02: trailing must MOVE stops atomically, never cancel+place ─


class _TrailTrader:
    def __init__(self, open_orders):
        self._orders = open_orders
        self.replaced: list[tuple] = []
        self.placed: list[dict] = []
        self.cancelled: list[str] = []
        self.replace_ok = True

    def get_open_orders(self, symbol=None):
        return self._orders

    def replace_stop_order(self, order_id, stop_price):
        self.replaced.append((order_id, stop_price))
        if self.replace_ok:
            return OrderResult(submitted=True, order_id=f"{order_id}-v2", status="accepted")
        return OrderResult(submitted=False, error="rejected")

    def place_stop_order(self, *, ticker, quantity, stop_price, side):
        self.placed.append({"ticker": ticker, "qty": quantity, "stop": stop_price})
        return OrderResult(submitted=True, order_id="new-stop", status="accepted")

    def cancel_order(self, oid):  # must never be called by trailing
        self.cancelled.append(oid)
        return True


def _bracket_stop(symbol, stop_price):
    return {"id": "leg-1", "symbol": symbol, "side": "sell", "type": "stop",
            "status": "new", "stop_price": stop_price, "legs": []}


def test_trailing_replaces_existing_stop_atomically(monkeypatch):
    pm._clear_tracking()
    monkeypatch.setattr(config, "TRAILING_STOP_PCT", 0.03, raising=False)
    monkeypatch.setattr(config, "TRAILING_STOP_ACTIVATION_PCT", 0.02, raising=False)
    trader = _TrailTrader([_bracket_stop("ADI", 373.30)])

    # Long up ~3.1%: trail to 385.04 should REPLACE the bracket leg in place.
    pm._manage_trailing_stop(trader, "ADI", entry_price=385.01,
                             current_price=396.95, position_qty=1, side="long")

    assert trader.replaced == [("leg-1", 385.04)]
    assert trader.cancelled == []          # the unprotected window is gone
    assert trader.placed == []
    assert pm._trailing_stop_orders["ADI"] == "leg-1-v2"
    pm._clear_tracking()


def test_trailing_replace_failure_keeps_old_stop(monkeypatch):
    pm._clear_tracking()
    monkeypatch.setattr(config, "TRAILING_STOP_PCT", 0.03, raising=False)
    monkeypatch.setattr(config, "TRAILING_STOP_ACTIVATION_PCT", 0.02, raising=False)
    trader = _TrailTrader([_bracket_stop("ADI", 373.30)])
    trader.replace_ok = False

    pm._manage_trailing_stop(trader, "ADI", entry_price=385.01,
                             current_price=396.95, position_qty=1, side="long")

    assert trader.cancelled == []          # old stop untouched → still protected
    assert trader.placed == []
    assert "ADI" not in pm._trailing_stop_orders  # cache cleared for re-derive
    pm._clear_tracking()


def test_trailing_places_fresh_stop_when_none_exists(monkeypatch):
    pm._clear_tracking()
    monkeypatch.setattr(config, "TRAILING_STOP_PCT", 0.03, raising=False)
    monkeypatch.setattr(config, "TRAILING_STOP_ACTIVATION_PCT", 0.02, raising=False)
    trader = _TrailTrader([])

    pm._manage_trailing_stop(trader, "CCL", entry_price=27.25,
                             current_price=26.47, position_qty=40, side="short")

    assert trader.replaced == []
    assert len(trader.placed) == 1 and trader.placed[0]["ticker"] == "CCL"
    pm._clear_tracking()


# ── Proactive: time-exit pacing (rate-limit thundering herd at first open) ───


def test_time_exit_respects_per_iteration_budget(monkeypatch):
    from test_order_execution_fixes import _ExitTrader
    import time as _t

    pm._clear_tracking()
    monkeypatch.setattr(config, "MAX_POSITION_HOLD_SECONDS", 3600, raising=False)
    trader = _ExitTrader()
    # 20 positions all aged far past the hold window
    for i in range(20):
        pm._position_first_seen[f"SYM{i}"] = _t.time() - 90_000

    budget = [8]
    submitted = sum(
        1 for i in range(20)
        if pm._maybe_time_exit(trader, f"SYM{i}", "long", budget)
    )
    assert submitted == 8          # capped this iteration
    assert len(trader.closed) == 8
    assert budget[0] == 0

    # Next iteration: fresh budget → the already-closing 8 short-circuit as
    # True without resubmitting, and 8 MORE positions get their closes.
    budget2 = [8]
    for i in range(20):
        pm._maybe_time_exit(trader, f"SYM{i}", "long", budget2)
    assert len(trader.closed) == 16
    pm._clear_tracking()


# ── Stale-mark recovery: re-anchor rejected stops to the broker's live price ─


def test_reconciliation_reanchors_stop_from_rejection_price(monkeypatch):
    pm._clear_tracking()
    monkeypatch.setattr(config, "STOP_LOSS_PCT", 0.03, raising=False)

    class _StaleMarkTrader:
        def __init__(self):
            self.attempts: list[float] = []

        def get_open_orders(self, symbol=None):
            return []

        def place_stop_order(self, *, ticker, quantity, stop_price, side):
            self.attempts.append(stop_price)
            if len(self.attempts) == 1:
                # Position mark says $357 but Alpaca knows it's $328.28.
                return OrderResult(submitted=False, error=(
                    '{"code":42210000,"market_price":"328.28",'
                    '"message":"stop price must be less than current price",'
                    '"stop_price":"346.92"}'
                ))
            return OrderResult(submitted=True, order_id="stop-retry",
                               status="accepted")

    trader = _StaleMarkTrader()
    pm._ensure_protective_stops(
        trader, [_position("WST", "long", 1, 1, 359.85, 357.65)]
    )

    assert len(trader.attempts) == 2
    assert trader.attempts[1] == round(328.28 * 0.97, 2)  # anchored to LIVE price
    assert pm._current_stop_prices["WST"] == trader.attempts[1]
    pm._clear_tracking()
