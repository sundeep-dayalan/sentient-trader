"""
Read-only HOLD calibration probe.

Question: is the agent too conservative? We look at every signal the committee
told us to HOLD, attach the forward returns the outcome labeler already recorded,
and measure how the price actually moved *in the sentiment-implied direction*
after the HOLD. If HOLDs reliably drifted the way sentiment leaned, the
buy/sell-sentiment and confidence thresholds are gating away real edge.

Nothing here writes. It only SELECTs from trades + signal_outcomes.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from statistics import mean

from dotenv import find_dotenv, load_dotenv


def client(schema: str):
    from supabase import create_client
    from supabase.client import ClientOptions

    return create_client(
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        options=ClientOptions(schema=schema),
    )


def fetch_all(table_query, page=1000):
    """Page through a PostgREST query."""
    rows, start = [], 0
    while True:
        chunk = table_query.range(start, start + page - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        start += page


def pct(x):
    return "n/a" if x is None else f"{x*100:+.3f}%"


def analyze(schema: str):
    sb = client(schema)
    # Pull recent decisions (cap generous; the project is small).
    trades = fetch_all(
        sb.table("trades").select(
            "id, created_at, ticker, sentiment_score, confidence_score, "
            "calibrated_confidence, trade_action, pm_recommendation, "
            "risk_should_trade, gate_reason, decision_path, executed_action"
        ).order("created_at", desc=True)
    )
    if not trades:
        print(f"[{schema}] no trades rows.")
        return

    outcomes = fetch_all(
        sb.table("signal_outcomes").select(
            "trade_id, return_15m, return_1h, return_eod, label_status"
        )
    )
    o_by_id = {o["trade_id"]: o for o in outcomes}

    def action(t):
        return t.get("pm_recommendation") or t.get("trade_action")

    def conviction(t):
        c = t.get("calibrated_confidence")
        return c if c is not None else t.get("confidence_score")

    n = len(trades)
    actions = defaultdict(int)
    for t in trades:
        actions[action(t)] += 1

    print(f"\n================  schema = {schema}  ================")
    print(f"total decisions: {n}")
    print("action mix:", dict(actions))

    holds = [t for t in trades if action(t) == "HOLD"]
    print(f"\nHOLD decisions: {len(holds)} ({len(holds)/n*100:.1f}% of all)")

    # Why were they held? Bucket by gate_reason / risk veto.
    why = defaultdict(int)
    for t in holds:
        gr = (t.get("gate_reason") or "").strip()
        if t.get("risk_should_trade") is False:
            why["risk_veto"] += 1
        elif gr:
            why[gr] += 1
        else:
            why["committee_hold(no_gate)"] += 1
    print("HOLD reasons:", dict(why))

    # Forward-return analysis for HOLDs that the labeler scored.
    labeled = []
    for t in holds:
        o = o_by_id.get(t["id"])
        if not o or o.get("label_status") not in ("LABELED", "PARTIAL"):
            continue
        if o.get("return_eod") is None and o.get("return_1h") is None and o.get("return_15m") is None:
            continue
        s = t.get("sentiment_score")
        if s is None:
            continue
        sdir = 1 if s > 0 else (-1 if s < 0 else 0)
        labeled.append((t, o, sdir))

    print(f"\nHOLDs with forward-return labels: {len(labeled)}")
    if not labeled:
        print("  (nothing labeled yet — can't judge missed moves)")
        return

    def edge(horizon):
        vals = []
        for t, o, sdir in labeled:
            r = o.get(horizon)
            if r is None or sdir == 0:
                continue
            vals.append(sdir * r)  # signed by sentiment direction
        return vals

    for h in ("return_15m", "return_1h", "return_eod"):
        vals = edge(h)
        if not vals:
            print(f"  {h}: no data")
            continue
        wins = sum(1 for v in vals if v > 0)
        big = sum(1 for v in vals if v > 0.005)  # moved >0.5% the right way
        print(
            f"  {h}: n={len(vals):3d}  avg_sentiment_edge={pct(mean(vals))}  "
            f"hit_rate={wins/len(vals):.2f}  moves>0.5%_right={big}"
        )

    # The near-miss cohort: HOLDs whose sentiment was strongly directional but
    # confidence fell just short. These are the ones a lower threshold catches.
    print("\nNear-miss HOLDs (|sentiment|>=0.8 AND committee not risk-vetoed):")
    near = [
        (t, o, sdir)
        for (t, o, sdir) in labeled
        if abs(t.get("sentiment_score") or 0) >= 0.8 and t.get("risk_should_trade") is not False
    ]
    if not near:
        print("  none")
    else:
        eod = [sdir * o["return_eod"] for t, o, sdir in near if o.get("return_eod") is not None]
        if eod:
            wins = sum(1 for v in eod if v > 0)
            print(
                f"  n={len(near)}  scored_eod={len(eod)}  "
                f"avg_eod_edge={pct(mean(eod))}  hit_rate={wins/len(eod):.2f}"
            )
        # Show the most extreme missed moves.
        ranked = sorted(
            [(t, o, sdir) for t, o, sdir in near if o.get("return_eod") is not None],
            key=lambda x: x[2] * x[1]["return_eod"],
            reverse=True,
        )
        print("  biggest missed up-moves (sentiment-aligned):")
        for t, o, sdir in ranked[:8]:
            conv = conviction(t)
            print(
                f"    {t['created_at'][:16]}  {t['ticker']:6s}  "
                f"sent={t['sentiment_score']:+.2f} conf={conv:.2f}  "
                f"eod_edge={pct(sdir*o['return_eod'])}  gate={t.get('gate_reason') or '-'}"
            )

    # Confidence-bucket view: among directional-sentiment HOLDs, does higher
    # committee conviction predict a bigger missed move? (calibration signal)
    print("\nDirectional HOLDs by conviction bucket (eod edge):")
    buckets = defaultdict(list)
    for t, o, sdir in labeled:
        if sdir == 0 or o.get("return_eod") is None:
            continue
        if abs(t.get("sentiment_score") or 0) < 0.5:
            continue
        c = conviction(t) or 0
        b = (
            "0.90+" if c >= 0.9 else
            "0.80-0.90" if c >= 0.8 else
            "0.70-0.80" if c >= 0.7 else
            "0.50-0.70" if c >= 0.5 else "<0.50"
        )
        buckets[b].append(sdir * o["return_eod"])
    for b in ("0.90+", "0.80-0.90", "0.70-0.80", "0.50-0.70", "<0.50"):
        v = buckets.get(b)
        if not v:
            continue
        wins = sum(1 for x in v if x > 0)
        print(f"  conf {b:10s} n={len(v):3d}  avg_eod_edge={pct(mean(v))}  hit_rate={wins/len(v):.2f}")


def deep_dive(schema: str):
    """Risk-veto cohort vs executed-trade baseline — where is the edge being lost?"""
    sb = client(schema)
    trades = fetch_all(
        sb.table("trades").select(
            "id, created_at, ticker, sentiment_score, confidence_score, "
            "calibrated_confidence, trade_action, pm_recommendation, "
            "risk_should_trade, gate_reason, executed_action"
        ).order("created_at", desc=True)
    )
    outcomes = fetch_all(
        sb.table("signal_outcomes").select(
            "trade_id, return_15m, return_1h, return_eod, label_status"
        )
    )
    o_by_id = {o["trade_id"]: o for o in outcomes}

    def action(t):
        return t.get("pm_recommendation") or t.get("trade_action")

    def sdir(t):
        s = t.get("sentiment_score")
        return 0 if not s else (1 if s > 0 else -1)

    def labeled_eod(t):
        o = o_by_id.get(t["id"])
        if not o or o.get("label_status") not in ("LABELED", "PARTIAL"):
            return None
        return o.get("return_eod")

    def cohort_stats(rows, signed=True):
        vals = []
        for t in rows:
            r = labeled_eod(t)
            if r is None:
                continue
            d = sdir(t) if signed else 1
            if d == 0:
                continue
            vals.append(d * r)
        if not vals:
            return None
        wins = sum(1 for v in vals if v > 0)
        big = sum(1 for v in vals if v > 0.005)
        return len(vals), mean(vals), wins / len(vals), big

    print(f"\n========  DEEP DIVE: where the edge goes  (schema={schema})  ========")

    cohorts = {
        "risk-vetoed HOLDs, |sent|>=0.8": [
            t for t in trades
            if action(t) == "HOLD" and t.get("risk_should_trade") is False
            and abs(t.get("sentiment_score") or 0) >= 0.8
        ],
        "risk-vetoed HOLDs, |sent|>=0.9": [
            t for t in trades
            if action(t) == "HOLD" and t.get("risk_should_trade") is False
            and abs(t.get("sentiment_score") or 0) >= 0.9
        ],
        "ALL risk-vetoed HOLDs": [
            t for t in trades
            if action(t) == "HOLD" and t.get("risk_should_trade") is False
        ],
        "EXECUTED BUY/SELL (realized)": [
            t for t in trades
            if t.get("executed_action") in ("BUY", "SELL")
        ],
    }
    for name, rows in cohorts.items():
        st = cohort_stats(rows)
        if st is None:
            print(f"  {name:34s}: n_total={len(rows):5d}  scored=0")
            continue
        nscored, avg, hit, big = st
        print(
            f"  {name:34s}: n_total={len(rows):5d}  scored={nscored:4d}  "
            f"avg_edge_eod={pct(avg)}  hit={hit:.2f}  moves>0.5%={big}"
        )


def main():
    p = find_dotenv()
    load_dotenv(p, override=False) if p else load_dotenv(override=False)
    schemas = sys.argv[1:] or ["sentient_trader", "sentient_trader_dev"]
    for s in schemas:
        try:
            analyze(s)
            deep_dive(s)
        except Exception as e:
            print(f"[{s}] error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
