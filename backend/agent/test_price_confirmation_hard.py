"""
Hard / combinatorial tests for the price-confirmation co-signal.

Goes beyond the happy-path suite in test_price_confirmation.py:

  1. Exhaustive band×volume×side truth table for evaluate_price_confirmation.
  2. Boundary math (inclusive >=/<= edges; unrounded-compare vs rounded-report).
  3. Volume/None/zero degenerate inputs and anchor-index domain.
  4. _parse_iso_timestamp format matrix.
  5. The confirm_signal NODE driven end-to-end with a fake Alpaca data client
     across every branch (disabled, no client, sparse bars, no baseline,
     fetch error, confirmed, rejected) × lenient/strict, including the
     logger integration that yields decision_path='unconfirmed'.
"""

import itertools
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from market_intelligence import evaluate_price_confirmation
from logger import _decision_path, trade_observability_fields

MIN_MOVE = 0.002
MAX_MOVE = 0.03
MIN_VOL = 1.2


def two_bar(*, side, zone, volume_ok, vol_none=False):
    """
    Build a 2-bar (ref, current) series + volumes giving a precise directional
    zone and volume condition, with anchor_index=1 (ref = pre-news close).

    zone: 'below' | 'in' | 'above' the [MIN_MOVE, MAX_MOVE] band.
    """
    ref = 100.0
    mag = {"below": MIN_MOVE / 2, "in": (MIN_MOVE + MAX_MOVE) / 2, "above": MAX_MOVE * 2}[zone]
    # For BUY, in-direction is up; for SELL, in-direction is down.
    current = ref * (1 + mag) if side == "BUY" else ref * (1 - mag)
    closes = [ref, current]
    if vol_none:
        volumes = [0.0, 500.0]  # pre avg 0 → ratio None
    else:
        ratio = (MIN_VOL + 0.5) if volume_ok else (MIN_VOL - 0.5)
        volumes = [100.0, 100.0 * ratio]
    return closes, volumes


class TruthTableTests(unittest.TestCase):
    def test_exhaustive_band_volume_side(self) -> None:
        """confirmed == (zone is 'in' AND volume_ok), for every combination."""
        for side, zone, volume_ok in itertools.product(
            ("BUY", "SELL"), ("below", "in", "above"), (True, False)
        ):
            with self.subTest(side=side, zone=zone, volume_ok=volume_ok):
                closes, volumes = two_bar(side=side, zone=zone, volume_ok=volume_ok)
                v = evaluate_price_confirmation(
                    action=side, closes=closes, volumes=volumes, anchor_index=1,
                    min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
                )
                expected = (zone == "in") and volume_ok
                self.assertEqual(v["confirmed"], expected)
                self.assertEqual(v["checks"]["direction_ok"], zone in ("in", "above"))
                self.assertEqual(v["checks"]["not_overextended"], zone in ("below", "in"))
                self.assertEqual(v["checks"]["volume_ok"], volume_ok)

    def test_band_is_mutually_exclusive(self) -> None:
        # No input can fail BOTH direction (too small) and overextension (too big).
        for zone in ("below", "in", "above"):
            closes, volumes = two_bar(side="BUY", zone=zone, volume_ok=True)
            v = evaluate_price_confirmation(
                action="BUY", closes=closes, volumes=volumes, anchor_index=1,
                min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
            )
            both_fail = (not v["checks"]["direction_ok"]) and (not v["checks"]["not_overextended"])
            self.assertFalse(both_fail)

    def test_volume_none_always_blocks_when_required(self) -> None:
        for side, zone in itertools.product(("BUY", "SELL"), ("below", "in", "above")):
            closes, volumes = two_bar(side=side, zone=zone, volume_ok=True, vol_none=True)
            v = evaluate_price_confirmation(
                action=side, closes=closes, volumes=volumes, anchor_index=1,
                min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
            )
            self.assertIsNone(v["volume_ratio"])
            self.assertFalse(v["confirmed"])


class BoundaryMathTests(unittest.TestCase):
    def test_exactly_min_move_is_inclusive(self) -> None:
        # current/ref chosen so directional == MIN_MOVE exactly.
        ref = 100.0
        closes = [ref, ref * (1 + MIN_MOVE)]
        volumes = [100.0, 200.0]
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=1,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertTrue(v["checks"]["direction_ok"])
        self.assertTrue(v["confirmed"])

    def test_exactly_max_move_is_inclusive_not_chase(self) -> None:
        ref = 100.0
        closes = [ref, ref * (1 + MAX_MOVE)]
        volumes = [100.0, 200.0]
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=1,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertTrue(v["checks"]["not_overextended"])
        self.assertTrue(v["confirmed"])

    def test_just_above_max_blocks(self) -> None:
        ref = 100.0
        closes = [ref, ref * (1 + MAX_MOVE + 1e-6)]
        volumes = [100.0, 200.0]
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=1,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertFalse(v["checks"]["not_overextended"])

    def test_just_below_min_blocks_even_though_rounds_to_min(self) -> None:
        # directional rounds to 0.002 at 5dp but is strictly below the threshold;
        # the gate must compare the UNROUNDED value, so this blocks.
        ref = 100.0
        closes = [ref, ref * (1 + MIN_MOVE - 1e-7)]
        volumes = [100.0, 200.0]
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=1,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertEqual(v["reaction_pct"], round(MIN_MOVE - 1e-7, 5))  # rounds to 0.002
        self.assertFalse(v["checks"]["direction_ok"])  # but unrounded compare blocks

    def test_exactly_volume_ratio_is_inclusive(self) -> None:
        ref = 100.0
        closes = [ref, ref * (1 + 0.005)]
        volumes = [100.0, 100.0 * MIN_VOL]  # ratio exactly 1.2
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=1,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertEqual(v["volume_ratio"], MIN_VOL)
        self.assertTrue(v["checks"]["volume_ok"])


class DomainAndDegenerateTests(unittest.TestCase):
    def test_anchor_index_out_of_range(self) -> None:
        for bad in (-1, 2, 99):
            v = evaluate_price_confirmation(
                action="BUY", closes=[100.0, 100.5], volumes=[100.0, 200.0],
                anchor_index=bad, min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE,
                min_volume_ratio=MIN_VOL,
            )
            self.assertFalse(v["data_available"])
            self.assertFalse(v["confirmed"])

    def test_empty_inputs(self) -> None:
        v = evaluate_price_confirmation(
            action="BUY", closes=[], volumes=[], anchor_index=0,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertFalse(v["data_available"])

    def test_zero_anchor_price_is_invalid(self) -> None:
        v = evaluate_price_confirmation(
            action="BUY", closes=[0.0, 100.0], volumes=[100.0, 200.0], anchor_index=1,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertFalse(v["data_available"])
        self.assertIn("Invalid anchor price", v["reason"])

    def test_negative_volume_threshold_skips_check(self) -> None:
        v = evaluate_price_confirmation(
            action="BUY", closes=[100.0, 100.5], volumes=[100.0, 1.0], anchor_index=1,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=-1.0,
        )
        self.assertTrue(v["checks"]["volume_ok"])

    def test_flat_move_blocks_both_sides(self) -> None:
        for side in ("BUY", "SELL"):
            v = evaluate_price_confirmation(
                action=side, closes=[100.0, 100.0], volumes=[100.0, 500.0], anchor_index=1,
                min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
            )
            self.assertEqual(v["directional_move_pct"], 0.0)
            self.assertFalse(v["confirmed"])

    def test_unknown_action_is_treated_as_sell_documented(self) -> None:
        # Documents the contract: anything not 'BUY' is scored as SELL. In the
        # graph this is unreachable (confirm only runs for approved BUY/SELL).
        v = evaluate_price_confirmation(
            action="HOLD", closes=[100.0, 99.5], volumes=[100.0, 200.0], anchor_index=1,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertEqual(v["action"], "SELL")
        self.assertTrue(v["confirmed"])  # downward move confirms a 'SELL'

    def test_longer_series_anchor_in_middle(self) -> None:
        # 5 pre-news flat bars, sharp move + volume after; anchor at index 5.
        closes = [50.0] * 5 + [50.0, 50.2, 50.4]
        volumes = [100.0] * 5 + [400.0, 400.0, 400.0]
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=5,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        # ref = closes[4] = 50.0, current = 50.4 → +0.8%
        self.assertEqual(v["anchor_price"], 50.0)
        self.assertTrue(v["confirmed"])
        self.assertEqual(v["bars_pre_news"], 5)


class ParseIsoTests(unittest.TestCase):
    def setUp(self) -> None:
        import analyst
        self.parse = analyst._parse_iso_timestamp

    def test_format_matrix(self) -> None:
        cases = [
            "2026-06-01T14:54:14Z",
            "2026-06-01T14:54:14+00:00",
            "2026-06-01T14:54:14.016776Z",
            "2026-06-01T10:54:14-04:00",
            "2026-06-01T14:54:14",            # naive → assumed UTC
        ]
        for s in cases:
            with self.subTest(s=s):
                dt = self.parse(s)
                self.assertIsNotNone(dt)
                self.assertIsNotNone(dt.tzinfo)

    def test_invalid_and_empty(self) -> None:
        for bad in (None, "", "not-a-date", "2026-13-99T99:99:99Z"):
            self.assertIsNone(self.parse(bad))

    def test_naive_assumed_utc_equals_z(self) -> None:
        a = self.parse("2026-06-01T14:54:14")
        b = self.parse("2026-06-01T14:54:14Z")
        self.assertEqual(a, b)


# ── Node-level branch matrix with a fake Alpaca data client ──────────────────


class FakeBars:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, ticker):
        return self._mapping.get(ticker)


class FakeDataClient:
    """Configurable stand-in for StockHistoricalDataClient."""
    next_bars = None        # list of bar objects for ticker "T"
    raise_on_fetch = False

    def __init__(self, *a, **k):
        pass

    def get_stock_bars(self, request):
        if FakeDataClient.raise_on_fetch:
            raise RuntimeError("boom")
        return FakeBars({"T": FakeDataClient.next_bars})


def _bar(ts, close, volume):
    return SimpleNamespace(timestamp=ts, close=close, volume=volume)


class ConfirmNodeBranchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("ALPACA_API_KEY", "test")
        os.environ.setdefault("ALPACA_SECRET_KEY", "test")

    def setUp(self):
        import analyst
        import config
        self.analyst = analyst
        self.config = config
        self._orig_client = analyst.StockHistoricalDataClient
        analyst.StockHistoricalDataClient = FakeDataClient
        FakeDataClient.next_bars = None
        FakeDataClient.raise_on_fetch = False
        # Enabled + default thresholds for most tests.
        config.PRICE_CONFIRMATION_ENABLED = True
        config.CONFIRM_MIN_MOVE_PCT = MIN_MOVE
        config.CONFIRM_MAX_MOVE_PCT = MAX_MOVE
        config.CONFIRM_MIN_VOLUME_RATIO = MIN_VOL
        config.CONFIRM_LOOKBACK_MINUTES = 30
        config.CONFIRM_REQUIRE_DATA = False
        self.node = analyst._make_confirm_signal_node()
        self.published = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.analyst.StockHistoricalDataClient = self._orig_client

    def _state(self, action="BUY"):
        news = SimpleNamespace(ticker="T", published_at=self.published.isoformat())
        return {
            "news": news,
            "analysis": SimpleNamespace(action=action),
            "should_trade": True,
            "risk_gate": {"step": "assess_risk", "should_trade": True,
                          "reason": "Signal passed thesis, source-quality, account, and execution-plan gates."},
        }

    def _bars_confirmed(self):
        # 3 pre-news flat bars then +0.5% on 3x volume.
        base = self.published
        pre = [_bar(base - timedelta(minutes=m), 100.0, 100.0) for m in (3, 2, 1)]
        post = [_bar(base, 100.0, 300.0), _bar(base + timedelta(minutes=1), 100.5, 300.0)]
        return pre + post

    def _bars_rejected_down(self):
        base = self.published
        pre = [_bar(base - timedelta(minutes=m), 100.0, 100.0) for m in (3, 2, 1)]
        post = [_bar(base, 100.0, 300.0), _bar(base + timedelta(minutes=1), 99.5, 300.0)]
        return pre + post

    # ---- branch: disabled ----
    def test_disabled_is_passthrough(self):
        self.config.PRICE_CONFIRMATION_ENABLED = False
        node = self.analyst._make_confirm_signal_node()
        self.assertEqual(node(self._state()), {})

    # ---- branch: confirmed ----
    def test_confirmed_passes_through_without_dropping_should_trade(self):
        FakeDataClient.next_bars = self._bars_confirmed()
        out = self.node(self._state("BUY"))
        self.assertIn("price_confirmation", out)
        self.assertTrue(out["price_confirmation"]["confirmed"])
        self.assertNotIn("should_trade", out)  # stays True from assess_risk

    # ---- branch: rejected → block + observability ----
    def test_rejected_blocks_and_rewrites_gate_reason(self):
        FakeDataClient.next_bars = self._bars_rejected_down()
        out = self.node(self._state("BUY"))
        self.assertFalse(out["should_trade"])
        self.assertTrue(out["risk_gate"]["reason"].startswith("Price-confirmation gate:"))
        self.assertEqual(out["risk_gate"]["should_trade"], False)
        self.assertIn("pre_confirmation_reason", out["risk_gate"])
        # End-to-end: trace → decision_path 'unconfirmed' + gate_reason surfaced.
        trace = {
            "portfolio_manager_decision": {"model": "qwen"},
            "llm_operations": [{"step": "portfolio_manager_synthesis"}],
            "risk_gate": out["risk_gate"],
            "price_confirmation": out["price_confirmation"],
        }
        fields = trade_observability_fields(decision_trace=trace, trade_action="BUY", order_id=None)
        self.assertEqual(fields["decision_path"], "unconfirmed")
        self.assertFalse(fields["risk_should_trade"])
        self.assertIn("Price-confirmation gate", fields["gate_reason"])

    def test_sell_confirmed_on_downmove(self):
        FakeDataClient.next_bars = self._bars_rejected_down()  # downward move
        out = self.node(self._state("SELL"))
        self.assertIn("price_confirmation", out)
        self.assertTrue(out["price_confirmation"]["confirmed"])
        self.assertNotIn("should_trade", out)

    # ---- branch: sparse bars (<3) ----
    def test_sparse_bars_lenient_pass(self):
        FakeDataClient.next_bars = [_bar(self.published, 100.0, 100.0)]
        out = self.node(self._state())
        self.assertIn("price_confirmation", out)
        self.assertTrue(out["price_confirmation"]["passed"])
        self.assertNotIn("should_trade", out)

    def test_sparse_bars_strict_block(self):
        self.config.CONFIRM_REQUIRE_DATA = True
        node = self.analyst._make_confirm_signal_node()
        FakeDataClient.next_bars = [_bar(self.published, 100.0, 100.0)]
        out = node(self._state())
        self.assertFalse(out["should_trade"])
        self.assertIn("strict", out["risk_gate"]["reason"].lower())

    # ---- branch: all bars pre-news (anchor not found) ----
    def test_all_bars_before_news_is_insufficient(self):
        base = self.published
        FakeDataClient.next_bars = [_bar(base - timedelta(minutes=m), 100.0, 100.0) for m in (3, 2, 1)]
        out = self.node(self._state())
        self.assertIn("price_confirmation", out)
        self.assertTrue(out["price_confirmation"]["passed"])  # lenient
        self.assertNotIn("should_trade", out)

    # ---- branch: no pre-news baseline (anchor_index == 0) ----
    def test_no_pre_news_baseline_is_insufficient(self):
        base = self.published
        # First bar already at/after news → anchor_index 0 → no baseline.
        FakeDataClient.next_bars = [
            _bar(base, 100.0, 100.0),
            _bar(base + timedelta(minutes=1), 100.5, 300.0),
            _bar(base + timedelta(minutes=2), 100.7, 300.0),
        ]
        out = self.node(self._state())
        self.assertTrue(out["price_confirmation"]["passed"])  # lenient pass-through
        self.assertIn("baseline", out["price_confirmation"]["reason"].lower())

    # ---- branch: fetch raises ----
    def test_fetch_error_lenient_pass(self):
        FakeDataClient.raise_on_fetch = True
        out = self.node(self._state())
        self.assertTrue(out["price_confirmation"]["passed"])
        self.assertIn("fetch failed", out["price_confirmation"]["reason"])

    def test_fetch_error_strict_block(self):
        self.config.CONFIRM_REQUIRE_DATA = True
        node = self.analyst._make_confirm_signal_node()
        FakeDataClient.raise_on_fetch = True
        out = node(self._state())
        self.assertFalse(out["should_trade"])

    # ---- branch: empty bar list ----
    def test_empty_bars_lenient_pass(self):
        FakeDataClient.next_bars = []
        out = self.node(self._state())
        self.assertTrue(out["price_confirmation"]["passed"])

    # ---- malformed published_at falls back to now() and still resolves ----
    def test_bad_published_at_does_not_crash(self):
        FakeDataClient.next_bars = self._bars_confirmed()
        st = self._state()
        st["news"] = SimpleNamespace(ticker="T", published_at="garbage")
        out = self.node(st)  # must not raise
        self.assertIsInstance(out, dict)


class DecisionPathPrecedenceTests(unittest.TestCase):
    def test_freshness_beats_confirmation(self):
        trace = {
            "risk_gate": {"step": "freshness_gate"},
            "price_confirmation": {"passed": False},
        }
        self.assertEqual(_decision_path(trace, "HOLD"), "expired")

    def test_pre_screen_beats_confirmation(self):
        trace = {
            "portfolio_manager_decision": {"model": "deterministic-pre-screen"},
            "price_confirmation": {"passed": False},
        }
        self.assertEqual(_decision_path(trace, "HOLD"), "pre_screen")

    def test_confirmation_block_beats_full_debate(self):
        trace = {
            "portfolio_manager_decision": {"model": "qwen"},
            "llm_operations": [{"step": "synth"}],
            "price_confirmation": {"passed": False},
        }
        self.assertEqual(_decision_path(trace, "BUY"), "unconfirmed")

    def test_missing_confirmation_key_is_full_debate(self):
        trace = {
            "portfolio_manager_decision": {"model": "qwen"},
            "llm_operations": [{"step": "synth"}],
        }
        self.assertEqual(_decision_path(trace, "BUY"), "full_debate")

    def test_non_dict_trace_is_legacy(self):
        self.assertEqual(_decision_path(None, "HOLD"), "legacy")


if __name__ == "__main__":
    unittest.main()
