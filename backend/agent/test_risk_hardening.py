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
