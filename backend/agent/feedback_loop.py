"""
Feedback Loop
==============
Uses historical signal outcomes to calibrate future confidence scores.

Queries the signal_outcomes table to compute per-ticker, per-action
accuracy rates and generates confidence adjustments and prompt context.

All adjustments are additive/subtractive — they shift the calibrated
confidence by a small amount, never override the LLM debate output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger("agent.feedback_loop")


@dataclass(frozen=True)
class HistoricalAccuracy:
    total_signals: int
    win_rate_1h: Optional[float]
    avg_return_1h: Optional[float]
    win_rate_eod: Optional[float]
    avg_return_eod: Optional[float]
    confidence_adjustment: float
    prompt_note: str


def compute_historical_accuracy(
    outcomes: list[dict[str, Any]],
    action: str,
) -> HistoricalAccuracy:
    """
    Compute win rate and confidence adjustment from historical outcomes.

    Expects outcomes with return_1h and return_eod fields.
    A 'win' for BUY = positive return; for SELL = negative return.
    """
    if not outcomes:
        return HistoricalAccuracy(
            total_signals=0,
            win_rate_1h=None,
            avg_return_1h=None,
            win_rate_eod=None,
            avg_return_eod=None,
            confidence_adjustment=0.0,
            prompt_note="",
        )

    valid_1h = [
        o for o in outcomes
        if o.get("return_1h") is not None
    ]
    valid_eod = [
        o for o in outcomes
        if o.get("return_eod") is not None
    ]

    # For BUY signals, a win is positive return
    # For SELL signals, a win is negative return (avoided loss)
    is_buy = action == "BUY"

    win_rate_1h: Optional[float] = None
    avg_return_1h: Optional[float] = None
    if valid_1h:
        wins = sum(
            1 for o in valid_1h
            if (float(o["return_1h"]) > 0) == is_buy
        )
        win_rate_1h = round(wins / len(valid_1h), 3)
        avg_return_1h = round(
            sum(float(o["return_1h"]) for o in valid_1h) / len(valid_1h), 4
        )

    win_rate_eod: Optional[float] = None
    avg_return_eod: Optional[float] = None
    if valid_eod:
        wins = sum(
            1 for o in valid_eod
            if (float(o["return_eod"]) > 0) == is_buy
        )
        win_rate_eod = round(wins / len(valid_eod), 3)
        avg_return_eod = round(
            sum(float(o["return_eod"]) for o in valid_eod) / len(valid_eod), 4
        )

    # Confidence adjustment: shift based on historical accuracy
    # Win rate > 60% → boost; < 40% → penalize
    reference_win_rate = win_rate_1h if win_rate_1h is not None else win_rate_eod
    if reference_win_rate is not None and len(outcomes) >= 5:
        # Scale: 70% win rate → +0.04; 30% win rate → -0.04
        adjustment = round((reference_win_rate - 0.50) * 0.20, 4)
        adjustment = max(-0.08, min(0.08, adjustment))  # Cap at ±8%
    else:
        adjustment = 0.0

    # Build prompt note
    prompt_note = ""
    if len(outcomes) >= 3 and reference_win_rate is not None:
        direction = "BUY" if is_buy else "SELL"
        quality = (
            "strong" if reference_win_rate >= 0.65
            else "weak" if reference_win_rate < 0.40
            else "mixed"
        )
        prompt_note = (
            f"HISTORICAL ACCURACY NOTE: Recent {direction} signals have a "
            f"{quality} track record ({reference_win_rate:.0%} win rate over "
            f"{len(outcomes)} signals)."
        )

    return HistoricalAccuracy(
        total_signals=len(outcomes),
        win_rate_1h=win_rate_1h,
        avg_return_1h=avg_return_1h,
        win_rate_eod=win_rate_eod,
        avg_return_eod=avg_return_eod,
        confidence_adjustment=adjustment,
        prompt_note=prompt_note,
    )


def query_recent_outcomes(
    supabase_client,
    ticker: str,
    action: str,
    days: int = 30,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Query signal_outcomes joined with trades for recent accuracy data.

    This is called before the synthesizer to provide historical context.
    Failures return an empty list — never blocks the pipeline.
    """
    try:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        result = (
            supabase_client.table("signal_outcomes")
            .select("trade_id, return_1h, return_eod, signal_price, label_status")
            .in_("label_status", ["LABELED", "PARTIAL"])
            .gte("signal_at", cutoff)
            .limit(limit)
            .execute()
        )
        rows = result.data or []

        # Filter to matching action by querying the trades table
        if not rows:
            return []

        trade_ids = [r["trade_id"] for r in rows if r.get("trade_id")]
        if not trade_ids:
            return []

        trades_result = (
            supabase_client.table("trades")
            .select("id, ticker, trade_action")
            .in_("id", trade_ids[:50])
            .eq("ticker", ticker)
            .eq("trade_action", action)
            .execute()
        )
        matching_trade_ids = {
            t["id"] for t in (trades_result.data or [])
        }

        return [r for r in rows if r.get("trade_id") in matching_trade_ids]

    except Exception as exc:
        log.warning("Could not query historical outcomes for %s: %s", ticker, exc)
        return []
