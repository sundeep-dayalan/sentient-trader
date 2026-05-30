"""
Enhanced Feature Observability
===============================
Tracks every enhanced feature activation per signal, recording what ran,
what decisions it influenced, and what it would have changed.

Provides:
  1. Per-signal feature telemetry (stored in decision_trace JSONB)
  2. Redis-backed counters for feature usage (lightweight metrics)
  3. Structured logging for grep/search in production logs

Every feature writes to a shared FeatureReport that gets embedded
in the decision_trace as `enhanced_features` — queryable via SQL.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("agent.observability")


@dataclass
class FeatureActivation:
    """Record of a single feature's activation for one signal."""

    feature: str
    enabled: bool
    activated: bool  # Was it actually used (not just enabled)?
    outcome: str  # What happened: "applied", "skipped", "blocked", "error"
    details: dict[str, Any] = field(default_factory=dict)
    impact: Optional[str] = None  # Human-readable impact description
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "feature": self.feature,
            "enabled": self.enabled,
            "activated": self.activated,
            "outcome": self.outcome,
        }
        if self.details:
            d["details"] = self.details
        if self.impact:
            d["impact"] = self.impact
        if self.error:
            d["error"] = self.error
        return d


class FeatureReport:
    """
    Accumulates feature activations across a single signal's pipeline.

    Usage:
        report = FeatureReport(ticker="NVDA")
        report.record("circuit_breaker", enabled=True, activated=True,
                       outcome="tripped", details={"daily_pnl_pct": -0.025})
        ...
        trace["enhanced_features"] = report.to_dict()
    """

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._activations: list[FeatureActivation] = []

    def record(
        self,
        feature: str,
        *,
        enabled: bool,
        activated: bool = False,
        outcome: str = "skipped",
        details: Optional[dict[str, Any]] = None,
        impact: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Record a feature activation or skip."""
        activation = FeatureActivation(
            feature=feature,
            enabled=enabled,
            activated=activated,
            outcome=outcome,
            details=details or {},
            impact=impact,
            error=error,
        )
        self._activations.append(activation)

        # Structured log line for each feature
        if activated:
            log.info(
                "Feature [%s] %s: outcome=%s impact=%s %s",
                self.ticker,
                feature,
                outcome,
                impact or "none",
                json.dumps(details, default=str, separators=(",", ":"))
                if details
                else "",
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for inclusion in decision_trace JSONB."""
        active = [a for a in self._activations if a.activated]
        skipped = [a for a in self._activations if a.enabled and not a.activated]

        return {
            "report_version": 1,
            "ticker": self.ticker,
            "started_at": self.started_at,
            "total_features_enabled": sum(
                1 for a in self._activations if a.enabled
            ),
            "total_features_activated": len(active),
            "total_features_skipped": len(skipped),
            "summary": {
                "active": [a.feature for a in active],
                "skipped": [
                    f"{a.feature}:{a.outcome}" for a in skipped
                ],
                "errors": [
                    f"{a.feature}:{a.error}"
                    for a in self._activations
                    if a.error
                ],
            },
            "activations": [a.to_dict() for a in self._activations],
        }


# ── Redis Counters ───────────────────────────────────────────────────────────


class FeatureMetrics:
    """
    Lightweight Redis-backed counters for feature usage.

    Stores daily counters per feature so you can answer:
    "How many times did the circuit breaker trip this week?"

    Keys: enhanced_metrics:{date}:{feature}:{outcome}
    TTL: 30 days
    """

    METRICS_PREFIX = "enhanced_metrics"
    METRICS_TTL = 30 * 24 * 3600  # 30 days

    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client

    def _ensure_redis(self):
        if self._redis is None:
            try:
                from redis_client import create_redis_client
                self._redis = create_redis_client()
            except Exception:
                return False
        return True

    def increment(
        self,
        feature: str,
        outcome: str,
        ticker: Optional[str] = None,
    ) -> None:
        """Increment a feature counter for today."""
        if not self._ensure_redis():
            return
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            # Global counter
            key = f"{self.METRICS_PREFIX}:{today}:{feature}:{outcome}"
            self._redis.incr(key)
            self._redis.expire(key, self.METRICS_TTL)
            # Per-ticker counter (for top-ticker analysis)
            if ticker:
                ticker_key = f"{self.METRICS_PREFIX}:{today}:{feature}:{outcome}:{ticker}"
                self._redis.incr(ticker_key)
                self._redis.expire(ticker_key, self.METRICS_TTL)
        except Exception:
            pass  # Metrics are best-effort

    def get_daily_counts(
        self,
        feature: str,
        date: Optional[str] = None,
    ) -> dict[str, int]:
        """Get all outcome counts for a feature on a given date."""
        if not self._ensure_redis():
            return {}
        try:
            day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            pattern = f"{self.METRICS_PREFIX}:{day}:{feature}:*"
            counts: dict[str, int] = {}
            cursor = 0
            while True:
                cursor, keys = self._redis.scan(cursor, match=pattern, count=100)
                for key in keys:
                    key_str = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                    parts = key_str.split(":")
                    # enhanced_metrics:date:feature:outcome[:ticker]
                    if len(parts) >= 4:
                        outcome = parts[3]
                        if len(parts) == 4:  # Global counter only
                            val = self._redis.get(key)
                            counts[outcome] = int(val) if val else 0
                if cursor == 0:
                    break
            return counts
        except Exception:
            return {}

    def get_feature_summary(
        self,
        date: Optional[str] = None,
    ) -> dict[str, dict[str, int]]:
        """Get all feature counts for a given date."""
        if not self._ensure_redis():
            return {}
        features = [
            "circuit_breaker", "source_credibility", "technical_indicators",
            "signal_momentum", "feedback_loop", "dynamic_sizing",
            "bracket_orders", "concentration_limits", "limit_orders",
            "semantic_dedup", "structured_synthesis", "llm_fallback",
            "market_hours",
        ]
        return {f: self.get_daily_counts(f, date) for f in features}


# ── Singleton metrics instance ───────────────────────────────────────────────

_metrics: Optional[FeatureMetrics] = None


def get_metrics() -> FeatureMetrics:
    """Get or create the global FeatureMetrics instance."""
    global _metrics
    if _metrics is None:
        _metrics = FeatureMetrics()
    return _metrics


# ── Helper to build a complete report for one signal ─────────────────────────


def build_feature_report(
    ticker: str,
    *,
    market_context: Optional[dict] = None,
    article_quality: Optional[dict] = None,
    execution_plan: Optional[dict] = None,
    execution: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Build a retrospective feature report from the completed state.

    Called from _build_decision_trace to capture what each feature did
    during this signal's processing.
    """
    try:
        import config as cfg
    except Exception:
        return {"error": "config unavailable"}

    report = FeatureReport(ticker)
    metrics = get_metrics()
    ctx = market_context or {}
    plan = execution_plan or {}
    exec_data = execution or {}

    # ── Circuit Breaker ──────────────────────────────────────────────────
    cb_enabled = getattr(cfg, "CIRCUIT_BREAKER_ENABLED", False)
    cb_data = plan.get("circuit_breaker")
    if cb_enabled and cb_data and cb_data.get("tripped"):
        report.record(
            "circuit_breaker",
            enabled=True,
            activated=True,
            outcome="tripped",
            details={"daily_pnl_pct": cb_data.get("daily_pnl_pct")},
            impact="Blocked all trading for the rest of the session.",
        )
        metrics.increment("circuit_breaker", "tripped", ticker)
    elif cb_enabled:
        report.record("circuit_breaker", enabled=True, activated=True, outcome="clear")
        metrics.increment("circuit_breaker", "clear", ticker)
    else:
        report.record("circuit_breaker", enabled=False, outcome="disabled")

    # ── Source Credibility ────────────────────────────────────────────────
    sc_enabled = getattr(cfg, "SOURCE_CREDIBILITY_ENABLED", False)
    quality = article_quality or {}
    sc_reasons = [r for r in quality.get("reasons", []) if "credibility" in r.lower()]
    sc_flags = [f for f in quality.get("flags", []) if "credibility" in f.lower()]
    if sc_enabled and (sc_reasons or sc_flags):
        report.record(
            "source_credibility",
            enabled=True,
            activated=True,
            outcome="applied",
            details={
                "reasons": sc_reasons,
                "flags": sc_flags,
                "score_after": quality.get("score"),
            },
            impact=f"Score adjusted to {quality.get('score')} ({quality.get('grade')}).",
        )
        metrics.increment("source_credibility", "applied", ticker)
    elif sc_enabled:
        report.record("source_credibility", enabled=True, activated=False, outcome="no_adjustment")
    else:
        report.record("source_credibility", enabled=False, outcome="disabled")

    # ── Technical Indicators ─────────────────────────────────────────────
    ti_enabled = getattr(cfg, "TECHNICAL_INDICATORS_ENABLED", False)
    tech = ctx.get("technical_indicators")
    if ti_enabled and tech:
        report.record(
            "technical_indicators",
            enabled=True,
            activated=True,
            outcome="computed",
            details={
                "rsi_14": tech.get("rsi_14"),
                "macd": tech.get("macd"),
                "sma_20": tech.get("sma_20"),
                "volume_ratio": tech.get("volume_ratio"),
                "source": tech.get("_source"),
            },
            impact=f"RSI={tech.get('rsi_14')}, MACD={tech.get('macd')} injected into LLM prompts.",
        )
        metrics.increment("technical_indicators", "computed", ticker)
    elif ti_enabled:
        ti_reason = ctx.get("technical_indicators_unavailable_reason") or "unknown"
        report.record(
            "technical_indicators",
            enabled=True,
            activated=False,
            outcome="data_unavailable",
            details={"reason": ti_reason},
        )
        metrics.increment("technical_indicators", "data_unavailable", ticker)
    else:
        report.record("technical_indicators", enabled=False, outcome="disabled")

    # ── Dynamic Position Sizing ──────────────────────────────────────────
    ds_enabled = getattr(cfg, "DYNAMIC_POSITION_SIZING_ENABLED", False)
    sizing_method = plan.get("sizing_method", "fixed")
    if ds_enabled and sizing_method == "dynamic":
        report.record(
            "dynamic_sizing",
            enabled=True,
            activated=True,
            outcome="applied",
            details={
                "scale_factor": plan.get("sizing_scale"),
                "quantity": plan.get("quantity"),
                "reasons": plan.get("sizing_reasons"),
            },
            impact=f"Sized to {plan.get('quantity')} shares (scale={plan.get('sizing_scale')}).",
        )
        metrics.increment("dynamic_sizing", "applied", ticker)
    elif ds_enabled:
        report.record("dynamic_sizing", enabled=True, activated=False, outcome="not_applicable")
    else:
        report.record("dynamic_sizing", enabled=False, outcome="disabled")

    # ── Concentration Limits ─────────────────────────────────────────────
    cl_enabled = getattr(cfg, "CONCENTRATION_LIMITS_ENABLED", False)
    conc_blocks = [r for r in plan.get("blocked_reasons", []) if "portfolio" in r.lower() or "position" in r.lower()]
    if cl_enabled and conc_blocks:
        report.record(
            "concentration_limits",
            enabled=True,
            activated=True,
            outcome="blocked",
            details={"blockers": conc_blocks},
            impact="Order blocked due to concentration limits.",
        )
        metrics.increment("concentration_limits", "blocked", ticker)
    elif cl_enabled:
        report.record("concentration_limits", enabled=True, activated=True, outcome="clear")
        metrics.increment("concentration_limits", "clear", ticker)
    else:
        report.record("concentration_limits", enabled=False, outcome="disabled")

    # ── Bracket Orders ───────────────────────────────────────────────────
    bo_enabled = getattr(cfg, "BRACKET_ORDERS_ENABLED", False)
    bracket = exec_data.get("bracket_orders")
    if bo_enabled and bracket:
        errors = bracket.get("errors", [])
        report.record(
            "bracket_orders",
            enabled=True,
            activated=True,
            outcome="placed" if not errors else "partial_error",
            details={
                "take_profit_order_id": bracket.get("take_profit_order_id"),
                "stop_loss_order_id": bracket.get("stop_loss_order_id"),
                "errors": errors,
            },
            impact="TP and SL orders placed for position protection.",
            error="; ".join(errors) if errors else None,
        )
        metrics.increment("bracket_orders", "placed" if not errors else "error", ticker)
    elif bo_enabled:
        report.record("bracket_orders", enabled=True, activated=False, outcome="no_buy_fill")
    else:
        report.record("bracket_orders", enabled=False, outcome="disabled")

    # ── Limit Orders ─────────────────────────────────────────────────────
    lo_enabled = getattr(cfg, "USE_LIMIT_ORDERS", False)
    limit_price = exec_data.get("limit_price")
    if lo_enabled and limit_price:
        report.record(
            "limit_orders",
            enabled=True,
            activated=True,
            outcome="submitted",
            details={"limit_price": limit_price},
            impact=f"Limit order at ${limit_price}.",
        )
        metrics.increment("limit_orders", "submitted", ticker)
    elif lo_enabled:
        report.record("limit_orders", enabled=True, activated=False, outcome="not_applicable")
    else:
        report.record("limit_orders", enabled=False, outcome="disabled")

    # ── Fill Verification ────────────────────────────────────────────────
    fill = exec_data.get("fill_verification")
    if fill:
        report.record(
            "fill_verification",
            enabled=True,
            activated=True,
            outcome=fill.get("status", "unknown"),
            details=fill,
            impact=f"Filled {fill.get('filled_qty')} @ ${fill.get('filled_avg_price')}.",
        )
        metrics.increment("fill_verification", fill.get("status", "unknown"), ticker)

    # ── Structured Synthesis ─────────────────────────────────────────────
    ss_enabled = getattr(cfg, "STRUCTURED_SYNTHESIS_ENABLED", False)
    report.record(
        "structured_synthesis",
        enabled=ss_enabled,
        activated=ss_enabled,
        outcome="applied" if ss_enabled else "disabled",
        impact="5-point synthesis framework used." if ss_enabled else None,
    )
    if ss_enabled:
        metrics.increment("structured_synthesis", "applied", ticker)

    # ── Signal Momentum ──────────────────────────────────────────────────
    sm_enabled = getattr(cfg, "SIGNAL_MOMENTUM_ENABLED", False)
    report.record(
        "signal_momentum",
        enabled=sm_enabled,
        activated=sm_enabled,
        outcome="tracked" if sm_enabled else "disabled",
    )
    if sm_enabled:
        metrics.increment("signal_momentum", "tracked", ticker)

    # ── Feedback Loop ────────────────────────────────────────────────────
    fl_enabled = getattr(cfg, "FEEDBACK_LOOP_ENABLED", False)
    report.record(
        "feedback_loop",
        enabled=fl_enabled,
        activated=fl_enabled,
        outcome="queried" if fl_enabled else "disabled",
    )
    if fl_enabled:
        metrics.increment("feedback_loop", "queried", ticker)

    # ── Market Hours ─────────────────────────────────────────────────────
    mh_enabled = getattr(cfg, "MARKET_HOURS_AWARENESS_ENABLED", False)
    report.record(
        "market_hours",
        enabled=mh_enabled,
        activated=mh_enabled,
        outcome="categorized" if mh_enabled else "disabled",
    )

    # ── Price-Move Gate ──────────────────────────────────────────────────
    pmg_enabled = getattr(cfg, "PRICE_MOVE_GATE_ENABLED", False)
    pmg_data = exec_data.get("price_move_gate")
    if pmg_enabled and isinstance(pmg_data, dict):
        was_blocked = pmg_data.get("blocked", False)
        report.record(
            "price_move_gate",
            enabled=True,
            activated=True,
            outcome="blocked" if was_blocked else "passed",
            details={
                "snapshot_price": pmg_data.get("snapshot_price"),
                "live_price": pmg_data.get("live_price"),
                "move_pct": pmg_data.get("move_pct"),
                "threshold_pct": pmg_data.get("threshold_pct"),
            },
            impact=(
                f"Order blocked: price moved {pmg_data.get('move_pct', 0):.1%} "
                f"exceeding {pmg_data.get('threshold_pct', 0):.0%} threshold."
                if was_blocked
                else f"Price move {pmg_data.get('move_pct', 0):.1%} within threshold."
            ),
        )
        metrics.increment("price_move_gate", "blocked" if was_blocked else "passed", ticker)
    elif pmg_enabled:
        report.record("price_move_gate", enabled=True, activated=False, outcome="no_data")
    else:
        report.record("price_move_gate", enabled=False, outcome="disabled")

    return report.to_dict()
