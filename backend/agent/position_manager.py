"""
Position Manager
=================
Dynamic position sizing, stop-loss / take-profit, trailing stops,
portfolio concentration limits, and daily loss circuit breaker.

Every function in this module is pure-logic: no I/O, no side effects.
The caller (analyst.py, trader.py) decides when and how to use them.

All features default to OFF — enable them via agent_config in Supabase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time
from typing import Any, Optional

log = logging.getLogger("agent.position_manager")


# ── Position Sizing ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PositionSizeResult:
    quantity: int
    notional: float
    scale_factor: float
    method: str
    reasons: list[str] = field(default_factory=list)


def compute_dynamic_position_size(
    *,
    calibrated_confidence: float,
    thesis_quality: str,
    current_price: float,
    portfolio_value: float,
    buying_power: float,
    max_position_pct: float = 0.05,
    min_order_qty: int = 1,
    fallback_qty: int = 1,
) -> PositionSizeResult:
    """
    Scale position size with conviction and thesis quality.

    High-conviction EXECUTABLE signals receive up to max_position_pct of
    the portfolio. Weaker signals get scaled down proportionally.

    Returns a PositionSizeResult with the computed quantity and reasoning.
    """
    if current_price <= 0 or portfolio_value <= 0:
        return PositionSizeResult(
            quantity=fallback_qty,
            notional=0.0,
            scale_factor=0.0,
            method="fallback",
            reasons=["Price or portfolio value unavailable; using fallback quantity."],
        )

    reasons: list[str] = []

    # Tiered scaling based on thesis quality and confidence
    if thesis_quality == "EXECUTABLE" and calibrated_confidence >= 0.85:
        scale = 1.0
        reasons.append("Full allocation: EXECUTABLE thesis with high confidence.")
    elif thesis_quality == "EXECUTABLE" and calibrated_confidence >= 0.78:
        scale = 0.6
        reasons.append("Reduced allocation: EXECUTABLE thesis, moderate confidence.")
    elif thesis_quality == "WATCH" and calibrated_confidence >= 0.70:
        scale = 0.3
        reasons.append("Small allocation: WATCH-quality thesis.")
    else:
        scale = 0.15
        reasons.append("Minimal allocation: weak thesis or low confidence.")

    max_notional = portfolio_value * max_position_pct * scale
    # Never exceed available buying power minus a 2% buffer
    safe_buying_power = buying_power * 0.98
    notional = min(max_notional, safe_buying_power)

    quantity = max(min_order_qty, int(notional / current_price))

    # Double-check we don't exceed buying power
    if quantity * current_price > safe_buying_power:
        quantity = max(min_order_qty, int(safe_buying_power / current_price))

    actual_notional = round(quantity * current_price, 2)

    return PositionSizeResult(
        quantity=quantity,
        notional=actual_notional,
        scale_factor=round(scale, 2),
        method="dynamic",
        reasons=reasons,
    )


# ── Stop-Loss / Take-Profit Parameters ──────────────────────────────────────


@dataclass(frozen=True)
class BracketParams:
    take_profit_price: float
    stop_loss_price: float
    take_profit_pct: float
    stop_loss_pct: float


def compute_bracket_prices(
    entry_price: float,
    action: str,
    *,
    stop_loss_pct: float = 0.03,
    take_profit_pct: float = 0.06,
) -> Optional[BracketParams]:
    """
    Compute take-profit and stop-loss prices for a bracket order.

    For BUY: stop below entry, target above.
    SELL (short) brackets are not supported — returns None.
    """
    if action != "BUY" or entry_price <= 0:
        return None

    return BracketParams(
        take_profit_price=round(entry_price * (1 + take_profit_pct), 2),
        stop_loss_price=round(entry_price * (1 - stop_loss_pct), 2),
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
    )


# ── Trailing Stop Parameters ────────────────────────────────────────────────


@dataclass(frozen=True)
class TrailingStopParams:
    trail_pct: float
    current_stop: float
    should_tighten: bool
    reason: str


def compute_trailing_stop(
    *,
    entry_price: float,
    current_price: float,
    current_stop: Optional[float],
    trail_pct: float = 0.03,
    activation_profit_pct: float = 0.02,
) -> TrailingStopParams:
    """
    Compute a trailing stop that tightens as the position gains.

    Activates once the position is up at least `activation_profit_pct`
    from entry. The stop trails `trail_pct` below the current price, but
    never moves down (only ratchets up).
    """
    if entry_price <= 0 or current_price <= 0:
        return TrailingStopParams(
            trail_pct=trail_pct,
            current_stop=current_stop or 0.0,
            should_tighten=False,
            reason="Invalid price data.",
        )

    gain_pct = (current_price - entry_price) / entry_price
    new_trail_stop = round(current_price * (1 - trail_pct), 2)

    if gain_pct < activation_profit_pct:
        return TrailingStopParams(
            trail_pct=trail_pct,
            current_stop=current_stop or 0.0,
            should_tighten=False,
            reason=f"Position gain {gain_pct:.1%} below activation threshold {activation_profit_pct:.1%}.",
        )

    if current_stop is not None and new_trail_stop <= current_stop:
        return TrailingStopParams(
            trail_pct=trail_pct,
            current_stop=current_stop,
            should_tighten=False,
            reason="Trailing stop would not ratchet up; keeping current stop.",
        )

    return TrailingStopParams(
        trail_pct=trail_pct,
        current_stop=new_trail_stop,
        should_tighten=True,
        reason=f"Tightening stop to ${new_trail_stop} (position up {gain_pct:.1%}).",
    )


# ── Portfolio Concentration ──────────────────────────────────────────────────


def check_portfolio_concentration(
    *,
    ticker: str,
    order_notional: float,
    positions: list[dict[str, Any]],
    portfolio_value: float,
    max_single_ticker_pct: float = 0.10,
    max_sector_pct: float = 0.25,
) -> list[str]:
    """
    Return a list of blocker reasons if the order would exceed concentration limits.
    Empty list = no concentration issues.
    """
    if portfolio_value <= 0:
        return []

    blockers: list[str] = []

    # Single-ticker concentration
    existing_exposure = sum(
        abs(float(p.get("market_value") or 0))
        for p in positions
        if str(p.get("symbol") or "").upper() == ticker.upper()
    )
    new_exposure = existing_exposure + order_notional
    ticker_pct = new_exposure / portfolio_value

    if ticker_pct > max_single_ticker_pct:
        blockers.append(
            f"Position in {ticker} would be {ticker_pct:.1%} of portfolio "
            f"(limit: {max_single_ticker_pct:.0%})."
        )

    return blockers


# ── Daily Loss Circuit Breaker ───────────────────────────────────────────────


@dataclass(frozen=True)
class CircuitBreakerResult:
    is_tripped: bool
    daily_pnl_pct: float
    reason: str


def check_daily_loss_limit(
    *,
    equity: Optional[float],
    last_equity: Optional[float],
    max_daily_loss_pct: float = 0.02,
) -> CircuitBreakerResult:
    """
    Check if the daily loss limit has been breached.

    Uses Alpaca's equity vs last_equity (prior close equity) to compute
    intraday P&L percentage. Returns a tripped breaker if the loss exceeds
    the configured threshold.
    """
    if equity is None or last_equity is None or last_equity <= 0:
        return CircuitBreakerResult(
            is_tripped=False,
            daily_pnl_pct=0.0,
            reason="Equity data unavailable; circuit breaker inactive.",
        )

    daily_pnl_pct = (equity - last_equity) / last_equity

    if daily_pnl_pct < -max_daily_loss_pct:
        return CircuitBreakerResult(
            is_tripped=True,
            daily_pnl_pct=round(daily_pnl_pct, 4),
            reason=(
                f"Daily loss limit reached ({daily_pnl_pct:.2%} vs "
                f"-{max_daily_loss_pct:.0%} limit). Trading paused until next session."
            ),
        )

    return CircuitBreakerResult(
        is_tripped=False,
        daily_pnl_pct=round(daily_pnl_pct, 4),
        reason=f"Daily P&L: {daily_pnl_pct:+.2%} (limit: -{max_daily_loss_pct:.0%}).",
    )


# ── Market Hours ─────────────────────────────────────────────────────────────


def is_regular_market_hours(et_time: time) -> bool:
    """Check if the given Eastern Time is within regular US equity market hours."""
    return time(9, 30) <= et_time <= time(16, 0)


def categorize_signal_timing(et_time: time, weekday: int) -> str:
    """
    Categorize when a signal was published relative to market hours.

    Returns: 'pre_market', 'regular', 'after_hours', or 'weekend'.
    """
    if weekday >= 5:
        return "weekend"
    if et_time < time(9, 30):
        return "pre_market"
    if et_time > time(16, 0):
        return "after_hours"
    return "regular"
