"""Tests for the shared threshold gate and the offline backtester."""

import unittest

from decision_rules import threshold_gate_decision
from backtest import Signal, evaluate


class ThresholdGateTests(unittest.TestCase):
    def _gate(self, **overrides):
        params = dict(
            action="BUY",
            sentiment=0.9,
            calibrated_confidence=0.85,
            quality_score=0.7,
            buy_threshold=0.8,
            sell_threshold=-0.8,
            confidence_threshold=0.9,
            quality_floor=0.6,
        )
        params.update(overrides)
        return threshold_gate_decision(**params)

    def test_strong_buy_passes(self):
        self.assertTrue(self._gate().passes)

    def test_confidence_threshold_capped_at_080(self):
        # confidence_threshold of 0.9 is capped to 0.80, so 0.85 clears it.
        gate = self._gate(calibrated_confidence=0.81, confidence_threshold=0.99)
        self.assertEqual(gate.effective_confidence_threshold, 0.80)
        self.assertTrue(gate.is_confident)

    def test_weak_sentiment_blocks(self):
        self.assertFalse(self._gate(sentiment=0.5).passes)

    def test_low_quality_blocks(self):
        self.assertFalse(self._gate(quality_score=0.4).passes)

    def test_sell_uses_sell_threshold(self):
        self.assertTrue(self._gate(action="SELL", sentiment=-0.85).passes)
        self.assertFalse(self._gate(action="SELL", sentiment=-0.5).passes)

    def test_hold_never_passes(self):
        self.assertFalse(self._gate(action="HOLD").passes)


class BacktestEvaluateTests(unittest.TestCase):
    def test_edge_sign_and_hit_rate(self):
        signals = [
            # Strong BUY that went up → edge positive, a hit.
            Signal("BUY", 0.9, 0.85, 0.7, return_eod=0.02),
            # Strong SELL that went down → edge positive (dir -1 * -0.03), a hit.
            Signal("SELL", -0.9, 0.85, 0.7, return_eod=-0.03),
            # Weak BUY that should be filtered out by the gate entirely.
            Signal("BUY", 0.1, 0.85, 0.7, return_eod=0.10),
        ]
        result = evaluate(
            signals,
            buy_threshold=0.8,
            sell_threshold=-0.8,
            confidence_threshold=0.8,
            quality_floor=0.6,
        )
        self.assertEqual(result.fired, 2)  # weak BUY excluded
        self.assertAlmostEqual(result.hit_rate, 1.0)
        self.assertAlmostEqual(result.avg_edge, (0.02 + 0.03) / 2)

    def test_no_signals_fire(self):
        signals = [Signal("BUY", 0.1, 0.1, 0.1, return_eod=0.05)]
        result = evaluate(
            signals,
            buy_threshold=0.8,
            sell_threshold=-0.8,
            confidence_threshold=0.8,
            quality_floor=0.6,
        )
        self.assertEqual(result.fired, 0)
        self.assertEqual(result.avg_edge, 0.0)
        self.assertEqual(result.hit_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
