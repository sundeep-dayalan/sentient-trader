"""
Tests for the price-confirmation co-signal.

Covers the pure verdict function (direction / volume / overextension band, and
missing-data handling) and the logger integration that makes a confirmation
block queryable as decision_path='unconfirmed'.
"""

import unittest

from market_intelligence import evaluate_price_confirmation
from logger import _decision_path, trade_observability_fields


# Defaults mirroring config.py so the band semantics are exercised directly.
MIN_MOVE = 0.002
MAX_MOVE = 0.03
MIN_VOL = 1.2


def _bars(prices, vols):
    return list(prices), list(vols)


class EvaluatePriceConfirmationTests(unittest.TestCase):
    def test_buy_confirmed_on_in_direction_move_and_volume(self) -> None:
        # 3 pre-news bars (baseline), then a +0.5% move on doubled volume.
        closes = [100.0, 100.0, 100.0, 100.3, 100.5]
        volumes = [100.0, 100.0, 100.0, 200.0, 220.0]
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=3,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertTrue(v["confirmed"])
        self.assertTrue(v["data_available"])
        self.assertGreater(v["directional_move_pct"], MIN_MOVE)
        self.assertGreaterEqual(v["volume_ratio"], MIN_VOL)

    def test_buy_rejected_when_tape_moves_against(self) -> None:
        # News is bullish but price fell after — direction fails.
        closes = [100.0, 100.0, 100.0, 99.7, 99.5]
        volumes = [100.0, 100.0, 100.0, 200.0, 220.0]
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=3,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertFalse(v["confirmed"])
        self.assertFalse(v["checks"]["direction_ok"])

    def test_sell_confirmed_on_downward_move(self) -> None:
        # SELL is confirmed by a downward in-direction move.
        closes = [50.0, 50.0, 50.0, 49.8, 49.6]
        volumes = [100.0, 100.0, 100.0, 200.0, 200.0]
        v = evaluate_price_confirmation(
            action="SELL", closes=closes, volumes=volumes, anchor_index=3,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertTrue(v["confirmed"])
        self.assertGreater(v["directional_move_pct"], 0)

    def test_rejected_when_overextended_chase(self) -> None:
        # +5% already ran past the 3% upper band — don't chase.
        closes = [100.0, 100.0, 100.0, 103.0, 105.0]
        volumes = [100.0, 100.0, 100.0, 300.0, 300.0]
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=3,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertFalse(v["confirmed"])
        self.assertFalse(v["checks"]["not_overextended"])

    def test_rejected_on_weak_volume(self) -> None:
        # Direction is fine but volume did not pick up — no participation.
        closes = [100.0, 100.0, 100.0, 100.3, 100.5]
        volumes = [100.0, 100.0, 100.0, 100.0, 100.0]
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=3,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertFalse(v["confirmed"])
        self.assertFalse(v["checks"]["volume_ok"])

    def test_volume_check_skipped_when_threshold_zero(self) -> None:
        closes = [100.0, 100.0, 100.0, 100.3, 100.5]
        volumes = [100.0, 100.0, 100.0, 100.0, 100.0]
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=3,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=0.0,
        )
        self.assertTrue(v["confirmed"])

    def test_insufficient_bars_reports_no_data(self) -> None:
        # A single bar cannot yield a reference + reaction.
        v = evaluate_price_confirmation(
            action="BUY", closes=[100.0], volumes=[100.0], anchor_index=0,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertFalse(v["confirmed"])
        self.assertFalse(v["data_available"])

    def test_anchor_uses_pre_news_close_as_reference(self) -> None:
        # Full move from the pre-news close (100 → 100.5 = +0.5%), not from the
        # first post-news bar (100.3), so a 0.5% reaction clears the 0.2% floor.
        closes = [100.0, 100.0, 100.0, 100.3, 100.5]
        volumes = [100.0, 100.0, 100.0, 200.0, 220.0]
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=3,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertAlmostEqual(v["reaction_pct"], 0.005, places=4)
        self.assertEqual(v["anchor_price"], 100.0)

    def test_no_volume_baseline_fails_volume_check(self) -> None:
        # anchor_index=0 → no pre-news bars to form a baseline.
        closes = [100.0, 100.3, 100.6]
        volumes = [200.0, 200.0, 200.0]
        v = evaluate_price_confirmation(
            action="BUY", closes=closes, volumes=volumes, anchor_index=0,
            min_move_pct=MIN_MOVE, max_move_pct=MAX_MOVE, min_volume_ratio=MIN_VOL,
        )
        self.assertIsNone(v["volume_ratio"])
        self.assertFalse(v["checks"]["volume_ok"])
        self.assertFalse(v["confirmed"])


class DecisionPathIntegrationTests(unittest.TestCase):
    def _full_debate_trace(self, price_confirmation):
        return {
            "portfolio_manager_decision": {"model": "qwen", "action": "BUY"},
            "llm_operations": [{"step": "portfolio_manager_synthesis"}],
            "risk_gate": {
                "should_trade": False,
                "reason": "Price-confirmation gate: Tape did not confirm BUY: ...",
                "inputs": {"calibrated_confidence": 0.82},
            },
            "price_confirmation": price_confirmation,
        }

    def test_blocked_confirmation_is_unconfirmed_path(self) -> None:
        trace = self._full_debate_trace({"passed": False, "confirmed": False})
        self.assertEqual(_decision_path(trace, "BUY"), "unconfirmed")

    def test_confirmed_pass_stays_full_debate(self) -> None:
        trace = self._full_debate_trace({"passed": True, "confirmed": True})
        self.assertEqual(_decision_path(trace, "BUY"), "full_debate")

    def test_lenient_missing_data_pass_stays_full_debate(self) -> None:
        # Missing-data pass-through keeps passed True → not an 'unconfirmed' block.
        trace = self._full_debate_trace(
            {"passed": True, "confirmed": False, "data_available": False}
        )
        self.assertEqual(_decision_path(trace, "BUY"), "full_debate")

    def test_blocked_confirmation_surfaces_gate_reason(self) -> None:
        trace = self._full_debate_trace({"passed": False, "confirmed": False})
        fields = trade_observability_fields(
            decision_trace=trace, trade_action="BUY", order_id=None,
        )
        self.assertEqual(fields["decision_path"], "unconfirmed")
        self.assertFalse(fields["risk_should_trade"])
        self.assertIn("Price-confirmation gate", fields["gate_reason"])


if __name__ == "__main__":
    unittest.main()
