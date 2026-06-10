"""
Offline threshold backtester / replay harness.

Re-runs the *deterministic* execution gate against historical signals using the
already-stored persona opinions and realized outcomes — no LLM calls. This lets
you tune ``buy_sentiment_threshold`` / ``sell_sentiment_threshold`` /
``confidence_threshold`` against realized forward returns for free.

It reuses ``decision_rules.threshold_gate_decision`` — the exact predicate the
live agent uses (analyst.assess_risk) — so results reflect production behavior.
The account/execution-plan portion of the live gate is intentionally excluded:
it depends on live positions and cannot be faithfully replayed.

Usage (from backend/agent, with the service-role env loaded):
    python backtest.py                       # sweep with defaults
    python backtest.py --buy-min 0.4 --buy-max 0.9 --buy-step 0.1 \
                       --conf-min 0.5 --conf-max 0.8 --conf-step 0.1
    python backtest.py --metric hit_rate --min-signals 5
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Any, Iterable

from dotenv import find_dotenv, load_dotenv
from supabase import create_client
from supabase.client import ClientOptions

from decision_rules import threshold_gate_decision

PAGE = 1000


@dataclass
class Signal:
    action: str
    sentiment: float
    calibrated_confidence: float
    quality_score: float
    return_eod: float

    @property
    def direction(self) -> int:
        return -1 if self.action == "SELL" else 1

    @property
    def edge_eod(self) -> float:
        """Realized return signed so positive always means the call was right."""
        return self.direction * self.return_eod


def _supabase():
    dotenv_path = find_dotenv()
    load_dotenv(dotenv_path, override=True) if dotenv_path else load_dotenv()
    return create_client(
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        options=ClientOptions(schema=os.environ.get("SUPABASE_DB_SCHEMA", "public")),
    )


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # drop NaN


def load_signals(client: Any) -> list[Signal]:
    """Join labeled outcomes ⇄ trades ⇄ decision traces into replayable signals."""
    # 1) Realized outcomes (only labeled rows with an EOD return).
    outcomes: dict[str, float] = {}
    start = 0
    while True:
        rows = (
            client.table("signal_outcomes")
            .select("trade_id, return_eod, label_status")
            .in_("label_status", ["LABELED", "PARTIAL"])
            .not_.is_("return_eod", "null")
            .range(start, start + PAGE - 1)
            .execute()
            .data
            or []
        )
        for row in rows:
            ret = _num(row.get("return_eod"))
            if row.get("trade_id") and ret is not None:
                outcomes[row["trade_id"]] = ret
        if len(rows) < PAGE:
            break
        start += PAGE

    if not outcomes:
        return []

    # 2) Trade-level sentiment / confidence / action.
    trades: dict[str, dict[str, Any]] = {}
    ids = list(outcomes)
    for chunk in _chunks(ids, 200):
        rows = (
            client.table("trades")
            .select(
                "id, sentiment_score, confidence_score, calibrated_confidence, "
                "pm_recommendation, trade_action"
            )
            .in_("id", chunk)
            .execute()
            .data
            or []
        )
        for row in rows:
            trades[row["id"]] = row

    # 3) Article-quality score from the decision trace (defaults to passing).
    quality: dict[str, float] = {}
    for chunk in _chunks(ids, 200):
        rows = (
            client.table("trade_decision_traces")
            .select("trade_id, decision_trace")
            .in_("trade_id", chunk)
            .execute()
            .data
            or []
        )
        for row in rows:
            trace = row.get("decision_trace") or {}
            score = _num((trace.get("article_quality") or {}).get("score"))
            if score is not None:
                quality[row["trade_id"]] = score

    signals: list[Signal] = []
    for trade_id, return_eod in outcomes.items():
        trade = trades.get(trade_id)
        if not trade:
            continue
        action = trade.get("pm_recommendation") or trade.get("trade_action")
        if action not in ("BUY", "SELL"):
            continue
        sentiment = _num(trade.get("sentiment_score"))
        confidence = _num(trade.get("calibrated_confidence"))
        if confidence is None:
            confidence = _num(trade.get("confidence_score"))
        if sentiment is None or confidence is None:
            continue
        signals.append(
            Signal(
                action=action,
                sentiment=sentiment,
                calibrated_confidence=confidence,
                # Missing quality ⇒ assume it cleared the floor so the signal
                # is still considered (it was, after all, analyzed and labeled).
                quality_score=quality.get(trade_id, 1.0),
                return_eod=return_eod,
            )
        )
    return signals


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


@dataclass
class Result:
    buy_threshold: float
    confidence_threshold: float
    fired: int
    avg_edge: float
    hit_rate: float
    avg_return: float


def evaluate(
    signals: list[Signal],
    *,
    buy_threshold: float,
    sell_threshold: float,
    confidence_threshold: float,
    quality_floor: float,
) -> Result:
    edges: list[float] = []
    returns: list[float] = []
    for sig in signals:
        gate = threshold_gate_decision(
            action=sig.action,
            sentiment=sig.sentiment,
            calibrated_confidence=sig.calibrated_confidence,
            quality_score=sig.quality_score,
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
            confidence_threshold=confidence_threshold,
            quality_floor=quality_floor,
        )
        if gate.passes:
            edges.append(sig.edge_eod)
            returns.append(sig.return_eod)
    fired = len(edges)
    avg_edge = sum(edges) / fired if fired else 0.0
    hit_rate = sum(1 for e in edges if e > 0) / fired if fired else 0.0
    avg_return = sum(returns) / fired if fired else 0.0
    return Result(
        buy_threshold=buy_threshold,
        confidence_threshold=confidence_threshold,
        fired=fired,
        avg_edge=avg_edge,
        hit_rate=hit_rate,
        avg_return=avg_return,
    )


def _frange(start: float, stop: float, step: float) -> list[float]:
    values = []
    current = start
    while current <= stop + 1e-9:
        values.append(round(current, 4))
        current += step
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buy-min", type=float, default=0.4)
    parser.add_argument("--buy-max", type=float, default=0.9)
    parser.add_argument("--buy-step", type=float, default=0.1)
    parser.add_argument("--conf-min", type=float, default=0.5)
    parser.add_argument("--conf-max", type=float, default=0.8)
    parser.add_argument("--conf-step", type=float, default=0.1)
    parser.add_argument("--quality-floor", type=float, default=0.60)
    parser.add_argument("--min-signals", type=int, default=1)
    parser.add_argument(
        "--metric",
        choices=["avg_edge", "hit_rate", "avg_return"],
        default="avg_edge",
        help="Ranking metric (SELL threshold mirrors BUY as its negative).",
    )
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()

    client = _supabase()
    signals = load_signals(client)
    print(f"Loaded {len(signals)} labeled, directional signals with EOD returns.\n")
    if not signals:
        print("No labeled signals to backtest yet.")
        return

    results = [
        evaluate(
            signals,
            buy_threshold=buy,
            sell_threshold=-buy,
            confidence_threshold=conf,
            quality_floor=args.quality_floor,
        )
        for buy in _frange(args.buy_min, args.buy_max, args.buy_step)
        for conf in _frange(args.conf_min, args.conf_max, args.conf_step)
    ]
    results = [r for r in results if r.fired >= args.min_signals]
    results.sort(key=lambda r: getattr(r, args.metric), reverse=True)

    header = (
        f"{'buy_thr':>8} {'conf_thr':>9} {'fired':>6} "
        f"{'avg_edge':>9} {'hit_rate':>9} {'avg_ret':>9}"
    )
    print(header)
    print("-" * len(header))
    for r in results[: args.top]:
        print(
            f"{r.buy_threshold:>8.2f} {r.confidence_threshold:>9.2f} {r.fired:>6} "
            f"{r.avg_edge * 100:>8.3f}% {r.hit_rate * 100:>8.1f}% "
            f"{r.avg_return * 100:>8.3f}%"
        )
    print(
        f"\nRanked by {args.metric}. avg_edge/return are EOD; edge is signed so "
        "positive = directionally correct. SELL threshold = -buy_threshold."
    )


if __name__ == "__main__":
    main()
