"""
Market Intelligence
====================
Technical indicators, signal momentum aggregation, and volatility context
that enriches the LLM debate with real market structure data.

All functions are stateless/pure except SignalMomentumTracker which uses Redis.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("agent.market_intelligence")


# ── Technical Indicators ─────────────────────────────────────────────────────


def compute_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Compute Relative Strength Index from a list of closing prices."""
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0, d) for d in deltas]
    losses = [max(0, -d) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_sma(closes: list[float], period: int = 20) -> Optional[float]:
    """Simple Moving Average."""
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 2)


def compute_ema(closes: list[float], period: int = 20) -> Optional[float]:
    """Exponential Moving Average."""
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return round(ema, 2)


def compute_volume_ratio(
    current_volume: float, recent_volumes: list[float]
) -> Optional[float]:
    """Ratio of current volume to average recent volume."""
    if not recent_volumes or current_volume <= 0:
        return None
    avg = sum(recent_volumes) / len(recent_volumes)
    if avg <= 0:
        return None
    return round(current_volume / avg, 2)


def compute_price_range_position(
    current: float, high_52w: float, low_52w: float
) -> Optional[float]:
    """Where current price sits in the 52-week range (0.0 = at low, 1.0 = at high)."""
    if high_52w <= low_52w or current <= 0:
        return None
    return round((current - low_52w) / (high_52w - low_52w), 3)


def build_technical_context(
    closes: list[float],
    volumes: list[float] | None = None,
    current_price: float | None = None,
) -> dict[str, Any]:
    """
    Build a technical indicators dict from recent price/volume data.

    Returns a dict with available indicators — callers should merge this
    into market_context. Missing data results in None values (not errors).
    """
    ctx: dict[str, Any] = {}

    if not closes:
        return ctx

    ctx["rsi_14"] = compute_rsi(closes, 14)
    ctx["sma_20"] = compute_sma(closes, 20)
    ctx["ema_12"] = compute_ema(closes, 12)
    ctx["ema_26"] = compute_ema(closes, 26)

    if len(closes) >= 2:
        ctx["price_52w_high"] = round(max(closes), 2)
        ctx["price_52w_low"] = round(min(closes), 2)
        price = current_price or closes[-1]
        ctx["range_position"] = compute_price_range_position(
            price, ctx["price_52w_high"], ctx["price_52w_low"]
        )

    if volumes and len(volumes) >= 2:
        ctx["volume_ratio"] = compute_volume_ratio(volumes[-1], volumes[:-1])

    # MACD (12,26 EMA difference)
    if ctx.get("ema_12") is not None and ctx.get("ema_26") is not None:
        ctx["macd"] = round(ctx["ema_12"] - ctx["ema_26"], 2)

    return ctx


def format_technical_prompt_block(tech: dict[str, Any]) -> str:
    """Format technical indicators for inclusion in LLM prompts."""
    if not tech:
        return ""

    lines: list[str] = ["\nTECHNICAL INDICATORS:"]
    if tech.get("rsi_14") is not None:
        rsi = tech["rsi_14"]
        label = (
            "(oversold)"
            if rsi < 30
            else "(overbought)" if rsi > 70 else "(neutral)"
        )
        lines.append(f"- RSI(14): {rsi:.1f} {label}")

    if tech.get("sma_20") is not None:
        lines.append(f"- SMA(20): ${tech['sma_20']:.2f}")

    if tech.get("macd") is not None:
        signal = "bullish" if tech["macd"] > 0 else "bearish"
        lines.append(f"- MACD: {tech['macd']:+.2f} ({signal})")

    if tech.get("volume_ratio") is not None:
        vr = tech["volume_ratio"]
        label = (
            "(high volume)"
            if vr > 2.0
            else "(above avg)" if vr > 1.2 else "(normal)"
        )
        lines.append(f"- Volume ratio: {vr:.1f}x {label}")

    if tech.get("range_position") is not None:
        rp = tech["range_position"]
        label = (
            "(near high)"
            if rp > 0.85
            else "(near low)" if rp < 0.15 else ""
        )
        lines.append(f"- Range position: {rp:.0%} of 52-week range {label}")

    return "\n".join(lines) if len(lines) > 1 else ""


# ── Signal Momentum Aggregator ───────────────────────────────────────────────


class SignalMomentumTracker:
    """
    Track recent signal sentiment per ticker using Redis sorted sets.

    This lets the synthesizer know if multiple signals are converging
    on the same ticker in a short window — a strong directional signal.
    """

    SIGNAL_KEY_PREFIX = "signal_momentum"
    DEFAULT_WINDOW_SECONDS = 3600  # 1 hour

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    def record_signal(
        self,
        ticker: str,
        sentiment: float,
        confidence: float,
        action: str,
    ) -> None:
        """Record a signal for momentum tracking."""
        key = f"{self.SIGNAL_KEY_PREFIX}:{ticker.upper()}"
        now = time.time()
        entry = json.dumps(
            {
                "sentiment": sentiment,
                "confidence": confidence,
                "action": action,
                "ts": now,
            },
            separators=(",", ":"),
        )
        self._redis.zadd(key, {entry: now})
        self._redis.expire(key, self.DEFAULT_WINDOW_SECONDS * 2)
        # Trim entries older than the window
        self._redis.zremrangebyscore(key, 0, now - self.DEFAULT_WINDOW_SECONDS)

    def get_momentum(
        self,
        ticker: str,
        window_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Get aggregated signal momentum for a ticker."""
        window = window_seconds or self.DEFAULT_WINDOW_SECONDS
        key = f"{self.SIGNAL_KEY_PREFIX}:{ticker.upper()}"
        cutoff = time.time() - window

        raw_entries = self._redis.zrangebyscore(key, cutoff, "+inf")
        if not raw_entries:
            return {
                "signal_count": 0,
                "bullish_count": 0,
                "bearish_count": 0,
                "neutral_count": 0,
                "avg_sentiment": 0.0,
                "avg_confidence": 0.0,
                "dominant_action": "HOLD",
            }

        entries = []
        for raw in raw_entries:
            try:
                entries.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                continue

        if not entries:
            return {
                "signal_count": 0,
                "bullish_count": 0,
                "bearish_count": 0,
                "neutral_count": 0,
                "avg_sentiment": 0.0,
                "avg_confidence": 0.0,
                "dominant_action": "HOLD",
            }

        buy_count = sum(1 for e in entries if e.get("action") == "BUY")
        sell_count = sum(1 for e in entries if e.get("action") == "SELL")
        hold_count = sum(1 for e in entries if e.get("action") == "HOLD")
        sentiments = [float(e.get("sentiment") or 0) for e in entries]
        confidences = [float(e.get("confidence") or 0) for e in entries]

        dominant = "HOLD"
        if buy_count > sell_count and buy_count > hold_count:
            dominant = "BUY"
        elif sell_count > buy_count and sell_count > hold_count:
            dominant = "SELL"

        return {
            "signal_count": len(entries),
            "bullish_count": buy_count,
            "bearish_count": sell_count,
            "neutral_count": hold_count,
            "avg_sentiment": round(sum(sentiments) / len(sentiments), 3),
            "avg_confidence": round(sum(confidences) / len(confidences), 3),
            "dominant_action": dominant,
        }

    def format_momentum_prompt(self, ticker: str) -> str:
        """Format signal momentum for inclusion in LLM prompts."""
        momentum = self.get_momentum(ticker)
        if momentum["signal_count"] <= 1:
            return ""
        return (
            f"\nSIGNAL MOMENTUM ({ticker}):\n"
            f"- {momentum['signal_count']} signals in the last hour\n"
            f"- Bullish: {momentum['bullish_count']}, "
            f"Bearish: {momentum['bearish_count']}, "
            f"Neutral: {momentum['neutral_count']}\n"
            f"- Avg sentiment: {momentum['avg_sentiment']:+.2f}, "
            f"Avg confidence: {momentum['avg_confidence']:.2f}\n"
            f"- Dominant direction: {momentum['dominant_action']}"
        )
