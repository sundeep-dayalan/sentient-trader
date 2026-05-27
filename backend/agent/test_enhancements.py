"""
Tests for the new enhanced trading modules.

Tests position sizing, circuit breaker, source credibility,
market intelligence, and cache deduplication improvements.
"""

import unittest
from datetime import time


class PositionManagerTests(unittest.TestCase):
    """Tests for position_manager.py"""

    def test_dynamic_sizing_high_conviction(self) -> None:
        from position_manager import compute_dynamic_position_size

        result = compute_dynamic_position_size(
            calibrated_confidence=0.90,
            thesis_quality="EXECUTABLE",
            current_price=150.0,
            portfolio_value=100_000.0,
            buying_power=50_000.0,
            max_position_pct=0.05,
        )
        self.assertEqual(result.method, "dynamic")
        self.assertEqual(result.scale_factor, 1.0)
        # Full allocation: 100k * 5% * 1.0 = 5000 / 150 = 33 shares
        self.assertEqual(result.quantity, 33)
        self.assertGreater(result.notional, 0)

    def test_dynamic_sizing_moderate_conviction(self) -> None:
        from position_manager import compute_dynamic_position_size

        result = compute_dynamic_position_size(
            calibrated_confidence=0.80,
            thesis_quality="EXECUTABLE",
            current_price=150.0,
            portfolio_value=100_000.0,
            buying_power=50_000.0,
            max_position_pct=0.05,
        )
        self.assertEqual(result.scale_factor, 0.6)
        # 100k * 5% * 0.6 = 3000 / 150 = 20 shares
        self.assertEqual(result.quantity, 20)

    def test_dynamic_sizing_low_conviction(self) -> None:
        from position_manager import compute_dynamic_position_size

        result = compute_dynamic_position_size(
            calibrated_confidence=0.50,
            thesis_quality="WEAK",
            current_price=150.0,
            portfolio_value=100_000.0,
            buying_power=50_000.0,
            max_position_pct=0.05,
        )
        self.assertEqual(result.scale_factor, 0.15)
        # 100k * 5% * 0.15 = 750 / 150 = 5 shares
        self.assertEqual(result.quantity, 5)

    def test_dynamic_sizing_respects_buying_power(self) -> None:
        from position_manager import compute_dynamic_position_size

        result = compute_dynamic_position_size(
            calibrated_confidence=0.95,
            thesis_quality="EXECUTABLE",
            current_price=150.0,
            portfolio_value=100_000.0,
            buying_power=300.0,  # Very limited buying power
            max_position_pct=0.05,
        )
        # Should cap at buying power: 300 * 0.98 / 150 = 1 share
        self.assertLessEqual(result.quantity * 150, 300)

    def test_dynamic_sizing_fallback_on_zero_price(self) -> None:
        from position_manager import compute_dynamic_position_size

        result = compute_dynamic_position_size(
            calibrated_confidence=0.90,
            thesis_quality="EXECUTABLE",
            current_price=0.0,
            portfolio_value=100_000.0,
            buying_power=50_000.0,
            fallback_qty=3,
        )
        self.assertEqual(result.method, "fallback")
        self.assertEqual(result.quantity, 3)

    def test_bracket_prices_buy(self) -> None:
        from position_manager import compute_bracket_prices

        params = compute_bracket_prices(100.0, "BUY", stop_loss_pct=0.03, take_profit_pct=0.06)
        self.assertIsNotNone(params)
        self.assertEqual(params.take_profit_price, 106.0)
        self.assertEqual(params.stop_loss_price, 97.0)

    def test_bracket_prices_hold_returns_none(self) -> None:
        from position_manager import compute_bracket_prices

        params = compute_bracket_prices(100.0, "HOLD")
        self.assertIsNone(params)

    def test_trailing_stop_activates(self) -> None:
        from position_manager import compute_trailing_stop

        result = compute_trailing_stop(
            entry_price=100.0,
            current_price=105.0,  # 5% gain
            current_stop=None,
            trail_pct=0.03,
            activation_profit_pct=0.02,
        )
        self.assertTrue(result.should_tighten)
        # Stop should be 105 * (1 - 0.03) = 101.85
        self.assertEqual(result.current_stop, 101.85)

    def test_trailing_stop_does_not_activate_below_threshold(self) -> None:
        from position_manager import compute_trailing_stop

        result = compute_trailing_stop(
            entry_price=100.0,
            current_price=101.0,  # Only 1% gain, below 2% threshold
            current_stop=None,
            trail_pct=0.03,
            activation_profit_pct=0.02,
        )
        self.assertFalse(result.should_tighten)

    def test_trailing_stop_does_not_ratchet_down(self) -> None:
        from position_manager import compute_trailing_stop

        result = compute_trailing_stop(
            entry_price=100.0,
            current_price=103.0,
            current_stop=101.0,  # Existing stop already at 101
            trail_pct=0.03,
            activation_profit_pct=0.02,
        )
        # New trail stop would be 103 * 0.97 = 99.91, below existing 101
        self.assertFalse(result.should_tighten)
        self.assertEqual(result.current_stop, 101.0)

    def test_circuit_breaker_trips(self) -> None:
        from position_manager import check_daily_loss_limit

        result = check_daily_loss_limit(
            equity=97_000.0,  # 3% loss, clearly beyond 2% limit
            last_equity=100_000.0,
            max_daily_loss_pct=0.02,
        )
        self.assertTrue(result.is_tripped)
        self.assertLess(result.daily_pnl_pct, -0.02)

    def test_circuit_breaker_ok(self) -> None:
        from position_manager import check_daily_loss_limit

        result = check_daily_loss_limit(
            equity=99_500.0,
            last_equity=100_000.0,
            max_daily_loss_pct=0.02,
        )
        self.assertFalse(result.is_tripped)

    def test_circuit_breaker_no_data(self) -> None:
        from position_manager import check_daily_loss_limit

        result = check_daily_loss_limit(equity=None, last_equity=None)
        self.assertFalse(result.is_tripped)

    def test_portfolio_concentration_blocks(self) -> None:
        from position_manager import check_portfolio_concentration

        blockers = check_portfolio_concentration(
            ticker="NVDA",
            order_notional=6_000.0,
            positions=[{"symbol": "NVDA", "market_value": 6_000.0}],
            portfolio_value=100_000.0,
            max_single_ticker_pct=0.10,
        )
        # Existing 6k + new 6k = 12k = 12% > 10% limit
        self.assertEqual(len(blockers), 1)
        self.assertIn("NVDA", blockers[0])

    def test_portfolio_concentration_ok(self) -> None:
        from position_manager import check_portfolio_concentration

        blockers = check_portfolio_concentration(
            ticker="NVDA",
            order_notional=3_000.0,
            positions=[{"symbol": "NVDA", "market_value": 5_000.0}],
            portfolio_value=100_000.0,
            max_single_ticker_pct=0.10,
        )
        # 5k + 3k = 8k = 8% < 10% limit
        self.assertEqual(len(blockers), 0)

    def test_market_hours_regular(self) -> None:
        from position_manager import is_regular_market_hours

        self.assertTrue(is_regular_market_hours(time(10, 0)))
        self.assertTrue(is_regular_market_hours(time(15, 30)))

    def test_market_hours_premarket(self) -> None:
        from position_manager import is_regular_market_hours

        self.assertFalse(is_regular_market_hours(time(8, 0)))

    def test_signal_timing(self) -> None:
        from position_manager import categorize_signal_timing

        self.assertEqual(categorize_signal_timing(time(8, 0), 0), "pre_market")
        self.assertEqual(categorize_signal_timing(time(10, 0), 1), "regular")
        self.assertEqual(categorize_signal_timing(time(17, 0), 2), "after_hours")
        self.assertEqual(categorize_signal_timing(time(10, 0), 5), "weekend")


class SourceCredibilityTests(unittest.TestCase):
    """Tests for source_credibility.py"""

    def test_tier_1_source(self) -> None:
        from source_credibility import source_credibility_score, source_tier

        score = source_credibility_score("Reuters")
        self.assertEqual(score, 0.15)
        self.assertEqual(source_tier("Reuters"), "TIER_1")

    def test_tier_4_source(self) -> None:
        from source_credibility import source_credibility_score, source_tier

        score = source_credibility_score("GlobeNewsWire")
        self.assertLess(score, 0)
        self.assertEqual(source_tier("GlobeNewsWire"), "TIER_4")

    def test_unknown_source(self) -> None:
        from source_credibility import source_credibility_score, source_tier

        score = source_credibility_score("SomeRandomBlog")
        self.assertEqual(score, 0.0)
        self.assertEqual(source_tier("SomeRandomBlog"), "TIER_3")

    def test_none_source(self) -> None:
        from source_credibility import source_credibility_score

        self.assertEqual(source_credibility_score(None), 0.0)

    def test_credibility_prompt_note(self) -> None:
        from source_credibility import source_credibility_prompt_note

        note = source_credibility_prompt_note("Bloomberg")
        self.assertIn("HIGH", note)

        note = source_credibility_prompt_note("Accesswire")
        self.assertIn("LOW", note)

        note = source_credibility_prompt_note("Benzinga")
        self.assertEqual(note, "")


class MarketIntelligenceTests(unittest.TestCase):
    """Tests for market_intelligence.py"""

    def test_rsi_calculation(self) -> None:
        from market_intelligence import compute_rsi

        # 15 increasing prices → RSI should be high (all gains)
        closes = [100 + i for i in range(20)]
        rsi = compute_rsi(closes, 14)
        self.assertIsNotNone(rsi)
        self.assertGreater(rsi, 70)

        # 15 decreasing prices → RSI should be low (all losses)
        closes = [100 - i for i in range(20)]
        rsi = compute_rsi(closes, 14)
        self.assertIsNotNone(rsi)
        self.assertLess(rsi, 30)

    def test_rsi_insufficient_data(self) -> None:
        from market_intelligence import compute_rsi

        self.assertIsNone(compute_rsi([100, 101, 102], 14))

    def test_sma(self) -> None:
        from market_intelligence import compute_sma

        closes = [100.0] * 20
        self.assertEqual(compute_sma(closes, 20), 100.0)
        self.assertIsNone(compute_sma([100.0] * 5, 20))

    def test_volume_ratio(self) -> None:
        from market_intelligence import compute_volume_ratio

        # Current volume is 2x average
        ratio = compute_volume_ratio(200, [100, 100, 100])
        self.assertEqual(ratio, 2.0)

    def test_build_technical_context(self) -> None:
        from market_intelligence import build_technical_context

        closes = [100 + i * 0.5 for i in range(30)]
        volumes = [1000 + i * 10 for i in range(30)]
        ctx = build_technical_context(closes, volumes)

        self.assertIn("rsi_14", ctx)
        self.assertIn("sma_20", ctx)
        self.assertIn("macd", ctx)
        self.assertIn("volume_ratio", ctx)

    def test_format_technical_prompt_block(self) -> None:
        from market_intelligence import format_technical_prompt_block

        tech = {"rsi_14": 25.5, "sma_20": 150.0, "macd": -2.5, "volume_ratio": 3.0}
        block = format_technical_prompt_block(tech)
        self.assertIn("RSI", block)
        self.assertIn("oversold", block)
        self.assertIn("high volume", block)

    def test_empty_technical_context(self) -> None:
        from market_intelligence import build_technical_context, format_technical_prompt_block

        self.assertEqual(build_technical_context([]), {})
        self.assertEqual(format_technical_prompt_block({}), "")


class FeedbackLoopTests(unittest.TestCase):
    """Tests for feedback_loop.py"""

    def test_empty_outcomes(self) -> None:
        from feedback_loop import compute_historical_accuracy

        acc = compute_historical_accuracy([], "BUY")
        self.assertEqual(acc.total_signals, 0)
        self.assertEqual(acc.confidence_adjustment, 0.0)
        self.assertEqual(acc.prompt_note, "")

    def test_high_win_rate_boosts_confidence(self) -> None:
        from feedback_loop import compute_historical_accuracy

        outcomes = [
            {"return_1h": 0.02, "return_eod": 0.03} for _ in range(8)
        ] + [
            {"return_1h": -0.01, "return_eod": -0.02} for _ in range(2)
        ]
        acc = compute_historical_accuracy(outcomes, "BUY")
        self.assertEqual(acc.total_signals, 10)
        self.assertEqual(acc.win_rate_1h, 0.8)  # 8/10
        self.assertGreater(acc.confidence_adjustment, 0)
        self.assertIn("strong", acc.prompt_note)

    def test_low_win_rate_penalizes_confidence(self) -> None:
        from feedback_loop import compute_historical_accuracy

        outcomes = [
            {"return_1h": 0.02, "return_eod": 0.03} for _ in range(2)
        ] + [
            {"return_1h": -0.01, "return_eod": -0.02} for _ in range(8)
        ]
        acc = compute_historical_accuracy(outcomes, "BUY")
        self.assertEqual(acc.win_rate_1h, 0.2)  # 2/10
        self.assertLess(acc.confidence_adjustment, 0)
        self.assertIn("weak", acc.prompt_note)

    def test_sell_win_rate(self) -> None:
        from feedback_loop import compute_historical_accuracy

        # For SELL signals, negative return = win
        outcomes = [
            {"return_1h": -0.03, "return_eod": -0.05} for _ in range(7)
        ] + [
            {"return_1h": 0.01, "return_eod": 0.02} for _ in range(3)
        ]
        acc = compute_historical_accuracy(outcomes, "SELL")
        self.assertEqual(acc.win_rate_1h, 0.7)  # 7/10 negative returns = wins for SELL


class CacheDeduplicationTests(unittest.TestCase):
    """Tests for the enhanced semantic dedup in cache.py"""

    def test_jaccard_similarity(self) -> None:
        from cache import _jaccard_similarity

        a = {"nvidia", "beats", "earnings", "q3"}
        b = {"nvda", "earnings", "top", "estimates"}
        sim = _jaccard_similarity(a, b)
        # "earnings" is common, 1/6 = 0.167
        self.assertGreater(sim, 0)

    def test_normalize_headline_words(self) -> None:
        from cache import _normalize_headline_words

        words = _normalize_headline_words("NVIDIA beats Q3 earnings, stock surges 5%")
        self.assertIn("nvidia", words)
        self.assertIn("beats", words)
        self.assertIn("earnings", words)
        self.assertIn("surges", words)
        self.assertNotIn("stock", words)  # stop word

    def test_identical_headlines_high_similarity(self) -> None:
        from cache import _jaccard_similarity, _normalize_headline_words

        a = _normalize_headline_words("NVIDIA reports record Q3 revenue and beats expectations")
        b = _normalize_headline_words("NVIDIA Q3 revenue beats expectations, reaching record levels")
        sim = _jaccard_similarity(a, b)
        self.assertGreater(sim, 0.5)

    def test_different_headlines_low_similarity(self) -> None:
        from cache import _jaccard_similarity, _normalize_headline_words

        a = _normalize_headline_words("NVIDIA announces new AI chip at GTC conference")
        b = _normalize_headline_words("Apple reports iPhone sales decline in China market")
        sim = _jaccard_similarity(a, b)
        self.assertLess(sim, 0.2)


class SignalTimingTests(unittest.TestCase):
    """Tests for signal timing categorization."""

    def test_pre_market(self) -> None:
        from decision_rules import categorize_signal_timing

        result = categorize_signal_timing("2026-05-26T08:00:00Z")
        # 8am UTC = 4am ET in summer = pre_market
        self.assertIn(result, ("pre_market", "after_hours"))

    def test_unknown_timestamp(self) -> None:
        from decision_rules import categorize_signal_timing

        self.assertEqual(categorize_signal_timing(None), "unknown")
        self.assertEqual(categorize_signal_timing(""), "unknown")


if __name__ == "__main__":
    unittest.main()
