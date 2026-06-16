"""
Tests for the short-selling path.

Covers the mirror geometry of short brackets (stop *above* entry, target
*below*), the short trailing stop (ratchets *down* as the short gains), and the
execution-plan gate that only opens a short when both the config flag and the
Alpaca account permit it.
"""

from __future__ import annotations

import config
from decision_rules import build_execution_plan
from position_manager import (
    compute_atr_bracket_prices,
    compute_bracket_prices,
    compute_trailing_stop,
)


# ── Bracket geometry ─────────────────────────────────────────────────────────


def test_flat_bracket_short_is_mirrored():
    p = compute_bracket_prices(100.0, "SELL", stop_loss_pct=0.03, take_profit_pct=0.06)
    assert p is not None
    # short profits as price falls: target below entry, stop above.
    assert p.take_profit_price == 94.0
    assert p.stop_loss_price == 103.0
    assert p.take_profit_price < 100.0 < p.stop_loss_price


def test_flat_bracket_long_unchanged():
    p = compute_bracket_prices(100.0, "BUY", stop_loss_pct=0.03, take_profit_pct=0.06)
    assert p is not None
    assert p.take_profit_price == 106.0
    assert p.stop_loss_price == 97.0


def test_atr_bracket_short_mirrored_and_preserves_rr():
    long_p = compute_atr_bracket_prices(100.0, "BUY", 0.02, stop_mult=2.0, tp_mult=4.0)
    short_p = compute_atr_bracket_prices(100.0, "SELL", 0.02, stop_mult=2.0, tp_mult=4.0)
    assert long_p is not None and short_p is not None
    # Same distances, opposite sides.
    assert short_p.stop_loss_price > 100.0 > short_p.take_profit_price
    assert short_p.stop_loss_pct == long_p.stop_loss_pct
    assert short_p.take_profit_pct == long_p.take_profit_pct


def test_atr_bracket_unknown_action_returns_none():
    assert compute_atr_bracket_prices(100.0, "HOLD", 0.02) is None


# ── Short trailing stop ──────────────────────────────────────────────────────


def test_short_trailing_activates_and_sits_above_price():
    # Short from $100, price fell to $97 → 3% gain, past the 2% activation.
    r = compute_trailing_stop(
        entry_price=100.0, current_price=97.0, current_stop=None,
        trail_pct=0.03, activation_profit_pct=0.02, side="short",
    )
    assert r.should_tighten
    assert r.current_stop > 97.0  # buy-to-cover stop sits above the price


def test_short_trailing_only_ratchets_down():
    # Existing stop at $99; new computed stop must be lower to count as better.
    r = compute_trailing_stop(
        entry_price=100.0, current_price=97.0, current_stop=99.0,
        trail_pct=0.03, activation_profit_pct=0.02, side="short",
    )
    # 97 * 1.03 = 99.91 > 99 → not better for a short, should not tighten.
    assert not r.should_tighten


def test_short_trailing_below_activation_holds():
    r = compute_trailing_stop(
        entry_price=100.0, current_price=99.5, current_stop=None,
        trail_pct=0.03, activation_profit_pct=0.02, side="short",
    )
    assert not r.should_tighten


# ── Execution-plan open-short gate ───────────────────────────────────────────


def _ctx(*, shorting_enabled: bool, qty: float = 0.0):
    return {
        "price": 50.0,
        "account": {
            "buying_power": 100000.0,
            "portfolio_value": 50000.0,
            "equity": 50000.0,
            "shorting_enabled": shorting_enabled,
        },
        "position": {"symbol": "ABC", "qty": qty, "current_price": 50.0},
    }


def test_sell_opens_short_when_enabled_and_account_allows(monkeypatch):
    monkeypatch.setattr(config, "SHORT_SELLING_ENABLED", True, raising=False)
    plan = build_execution_plan(
        action="SELL", order_qty=1, market_context=_ctx(shorting_enabled=True),
    )
    assert plan["position_intent"] == "open_short"
    assert plan.get("is_short") is True
    assert plan["side"] == "sell"
    assert plan["quantity"] >= 1
    assert plan["blocked_reasons"] == []


def test_sell_blocked_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "SHORT_SELLING_ENABLED", False, raising=False)
    plan = build_execution_plan(
        action="SELL", order_qty=1, market_context=_ctx(shorting_enabled=True),
    )
    assert plan["position_intent"] == "no_long_position"
    assert any("disabled by policy" in r for r in plan["blocked_reasons"])


def test_sell_blocked_when_account_disallows(monkeypatch):
    monkeypatch.setattr(config, "SHORT_SELLING_ENABLED", True, raising=False)
    plan = build_execution_plan(
        action="SELL", order_qty=1, market_context=_ctx(shorting_enabled=False),
    )
    assert plan["position_intent"] == "no_long_position"


def test_sell_reduces_existing_long_regardless_of_flag(monkeypatch):
    monkeypatch.setattr(config, "SHORT_SELLING_ENABLED", True, raising=False)
    plan = build_execution_plan(
        action="SELL", order_qty=1, market_context=_ctx(shorting_enabled=True, qty=10),
    )
    assert plan["position_intent"] == "reduce_long"
    assert not plan.get("is_short")
