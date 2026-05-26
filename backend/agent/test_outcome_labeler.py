import unittest
from datetime import datetime, timezone

import httpx

from outcome_labeler import (
    Bar,
    alpaca_bar_error_message,
    build_no_bars_record,
    build_outcome_record,
    first_close_at_or_after,
    is_supported_alpaca_stock_symbol,
    label_status_for_record,
    market_close_for_signal,
    maybe_single_data,
    next_attempt_count,
    return_pct,
    should_skip_existing,
)


class OutcomeLabelerTests(unittest.TestCase):
    def test_first_close_at_or_after(self) -> None:
        bars = [
            Bar(datetime(2026, 5, 26, 13, 31, tzinfo=timezone.utc), 101.0),
            Bar(datetime(2026, 5, 26, 13, 30, tzinfo=timezone.utc), 100.0),
        ]

        self.assertEqual(
            first_close_at_or_after(
                bars, datetime(2026, 5, 26, 13, 30, 30, tzinfo=timezone.utc)
            ),
            101.0,
        )

    def test_market_close_for_signal(self) -> None:
        close = market_close_for_signal(
            datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
        )

        self.assertEqual(close, datetime(2026, 5, 26, 20, 0, tzinfo=timezone.utc))

    def test_return_pct(self) -> None:
        self.assertEqual(return_pct(100.0, 104.0), 0.04)
        self.assertIsNone(return_pct(0.0, 104.0))

    def test_build_outcome_record(self) -> None:
        signal_at = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
        now = datetime(2026, 5, 26, 21, 0, tzinfo=timezone.utc)
        bars = [
            Bar(signal_at, 100.0),
            Bar(datetime(2026, 5, 26, 14, 15, tzinfo=timezone.utc), 102.0),
            Bar(datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc), 101.0),
            Bar(datetime(2026, 5, 26, 20, 0, tzinfo=timezone.utc), 105.0),
        ]

        record = build_outcome_record(
            trade_id="trade-1",
            ticker="AAPL",
            signal_at=signal_at,
            signal_price=100.0,
            bars=bars,
            now=now,
        )

        self.assertEqual(record["price_15m"], 102.0)
        self.assertEqual(record["return_15m"], 0.02)
        self.assertEqual(record["price_1h"], 101.0)
        self.assertEqual(record["return_eod"], 0.05)
        self.assertEqual(record["label_status"], "LABELED")
        self.assertIsNone(record["label_error"])

    def test_partial_outcome_status_before_eod(self) -> None:
        signal_at = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
        now = datetime(2026, 5, 26, 14, 30, tzinfo=timezone.utc)
        bars = [
            Bar(signal_at, 100.0),
            Bar(datetime(2026, 5, 26, 14, 15, tzinfo=timezone.utc), 102.0),
        ]

        record = build_outcome_record(
            trade_id="trade-1",
            ticker="AAPL",
            signal_at=signal_at,
            signal_price=100.0,
            bars=bars,
            now=now,
        )

        self.assertEqual(record["label_status"], "PARTIAL")
        self.assertEqual(record["return_15m"], 0.02)
        self.assertIsNone(record["return_1h"])

    def test_no_bars_record_is_terminal(self) -> None:
        now = datetime(2026, 5, 25, 22, 0, tzinfo=timezone.utc)
        record = build_no_bars_record(
            trade_id="trade-1",
            ticker="AAPL",
            signal_at=datetime(2026, 5, 25, 14, 0, tzinfo=timezone.utc),
            signal_price=None,
            now=now,
            error="No Alpaca bars available for requested window.",
            label_attempts=2,
        )

        self.assertEqual(record["label_status"], "NO_BARS")
        self.assertEqual(record["label_attempts"], 2)
        self.assertIn("No Alpaca bars", record["label_error"])
        self.assertTrue(should_skip_existing(record, force=False))
        self.assertFalse(should_skip_existing(record, force=True))

    def test_attempt_count_increments_existing_rows(self) -> None:
        self.assertEqual(next_attempt_count(None), 1)
        self.assertEqual(next_attempt_count({"label_attempts": 3}), 4)

    def test_label_status_helper_marks_completed_eod(self) -> None:
        now = datetime(2026, 5, 26, 21, 0, tzinfo=timezone.utc)
        status = label_status_for_record(
            {
                "signal_at": "2026-05-26T14:00:00+00:00",
                "signal_price": 100.0,
                "return_15m": 0.01,
                "return_1h": None,
                "return_eod": None,
            },
            now,
        )

        self.assertEqual(status, "LABELED")

    def test_maybe_single_data_handles_no_row_result(self) -> None:
        class Query:
            def maybe_single(self):
                return self

            def execute(self):
                return None

        self.assertIsNone(maybe_single_data(Query()))

    def test_maybe_single_data_returns_dict_data(self) -> None:
        class Result:
            data = {"trade_id": "trade-1", "label_status": "NO_BARS"}

        class Query:
            def maybe_single(self):
                return self

            def execute(self):
                return Result()

        self.assertEqual(maybe_single_data(Query())["label_status"], "NO_BARS")

    def test_supported_alpaca_symbol_rejects_exchange_prefixes(self) -> None:
        self.assertTrue(is_supported_alpaca_stock_symbol("AAPL"))
        self.assertTrue(is_supported_alpaca_stock_symbol("BRK.B"))
        self.assertFalse(is_supported_alpaca_stock_symbol("TSX:ENB"))

    def test_alpaca_bar_error_message_is_short_and_actionable(self) -> None:
        request = httpx.Request("GET", "https://data.alpaca.markets/v2/stocks/X/bars")
        response = httpx.Response(400, request=request)
        exc = httpx.HTTPStatusError("bad request", request=request, response=response)

        self.assertEqual(
            alpaca_bar_error_message(exc),
            "Alpaca bars request failed with HTTP 400 Bad Request.",
        )


if __name__ == "__main__":
    unittest.main()
