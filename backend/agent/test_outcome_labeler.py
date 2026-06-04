import unittest
from datetime import datetime, timezone

import httpx

from outcome_labeler import (
    Bar,
    alpaca_bar_error_message,
    build_no_bars_record,
    build_outcome_record,
    build_pending_record,
    first_bar_at_or_after,
    first_close_at_or_after,
    get_bars,
    is_extended_hours,
    is_supported_alpaca_stock_symbol,
    label_status_for_record,
    market_close_for_signal,
    maybe_single_data,
    next_attempt_count,
    next_regular_session_close,
    outcome_label_status,
    return_pct,
    should_skip_existing,
)


class _FakeProvider:
    def __init__(self, name, bars=None, error=None):
        self.name = name
        self._bars = bars or []
        self._error = error
        self.calls = 0

    def fetch_bars(self, ticker, start, end):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return list(self._bars)


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

    def test_first_bar_at_or_after_returns_bar(self) -> None:
        bars = [
            Bar(datetime(2026, 5, 26, 13, 31, tzinfo=timezone.utc), 101.0),
            Bar(datetime(2026, 5, 26, 13, 30, tzinfo=timezone.utc), 100.0),
        ]

        bar = first_bar_at_or_after(
            bars, datetime(2026, 5, 26, 13, 30, 30, tzinfo=timezone.utc)
        )

        self.assertIsNotNone(bar)
        self.assertEqual(bar.close, 101.0)

    def test_after_close_signal_anchors_on_next_session(self) -> None:
        # Signal logged at 17:00 ET (after the close): forward windows must
        # anchor on the next session's opening bar, not freeze as NO_BARS.
        signal_at = datetime(2026, 5, 26, 21, 0, tzinfo=timezone.utc)
        now = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)
        bars = [
            Bar(datetime(2026, 5, 27, 13, 30, tzinfo=timezone.utc), 101.0),  # open
            Bar(datetime(2026, 5, 27, 13, 45, tzinfo=timezone.utc), 102.0),  # +15m
            Bar(datetime(2026, 5, 27, 14, 30, tzinfo=timezone.utc), 103.0),  # +1h
            Bar(datetime(2026, 5, 27, 20, 0, tzinfo=timezone.utc), 105.0),  # close
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
        self.assertEqual(record["return_1h"], 0.03)
        self.assertEqual(record["return_eod"], 0.05)
        self.assertEqual(record["label_status"], "LABELED")

    def test_pending_record_is_retryable(self) -> None:
        now = datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc)  # pre-market
        record = build_pending_record(
            trade_id="trade-1",
            ticker="AAPL",
            signal_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
            signal_price=None,
            now=now,
            note="Awaiting market-data bars for the signal's trading session.",
        )

        self.assertEqual(record["label_status"], "PARTIAL")
        self.assertIsNone(record["return_15m"])
        self.assertIn("Awaiting", record["label_error"])
        # PARTIAL is non-terminal, so the row is retried on later runs.
        self.assertFalse(should_skip_existing(record, force=False))

    def test_outcome_label_status_pending_before_eod(self) -> None:
        now = datetime(2026, 5, 26, 14, 30, tzinfo=timezone.utc)
        status = outcome_label_status(
            target_eod=datetime(2026, 5, 26, 20, 0, tzinfo=timezone.utc),
            now=now,
            return_eod=None,
            return_15m=None,
            return_1h=None,
            signal_price=None,
        )

        self.assertEqual(status, "PARTIAL")

    def test_next_regular_session_close(self) -> None:
        # Pre-market Tuesday -> same-day 16:00 ET (20:00 UTC).
        self.assertEqual(
            next_regular_session_close(
                datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc)
            ),
            datetime(2026, 5, 26, 20, 0, tzinfo=timezone.utc),
        )
        # After-close Tuesday (21:00 UTC == 17:00 ET) -> Wednesday's close.
        self.assertEqual(
            next_regular_session_close(
                datetime(2026, 5, 26, 21, 0, tzinfo=timezone.utc)
            ),
            datetime(2026, 5, 27, 20, 0, tzinfo=timezone.utc),
        )
        # Saturday -> Monday's close.
        self.assertEqual(
            next_regular_session_close(
                datetime(2026, 5, 30, 14, 0, tzinfo=timezone.utc)
            ),
            datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc),
        )

    def test_is_extended_hours(self) -> None:
        # 14:00 UTC == 10:00 ET (regular session) on a Tuesday.
        self.assertFalse(
            is_extended_hours(datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc))
        )
        # 11:00 UTC == 07:00 ET (pre-market).
        self.assertTrue(
            is_extended_hours(datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc))
        )
        # 21:00 UTC == 17:00 ET (after close).
        self.assertTrue(
            is_extended_hours(datetime(2026, 5, 26, 21, 0, tzinfo=timezone.utc))
        )
        # Saturday is always extended hours.
        self.assertTrue(
            is_extended_hours(datetime(2026, 5, 30, 14, 0, tzinfo=timezone.utc))
        )

    def test_get_bars_uses_primary_when_covered(self) -> None:
        start = datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc)
        primary = _FakeProvider("alpaca", [Bar(start, 100.0)])
        fallback = _FakeProvider("yfinance", [Bar(start, 999.0)])

        bars = get_bars(
            [primary, fallback], "AAPL", start, start, prefer_fallback=False
        )

        self.assertEqual([b.close for b in bars], [100.0])
        self.assertEqual(fallback.calls, 0)  # fallback not queried

    def test_get_bars_prefers_earlier_fallback_offhours(self) -> None:
        signal = datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc)  # pre-market
        # Primary (IEX) only has the regular-session open, missing pre-market.
        primary = _FakeProvider(
            "alpaca", [Bar(datetime(2026, 5, 26, 13, 30, tzinfo=timezone.utc), 105.0)]
        )
        # Fallback covers the pre-market window from the signal time.
        fallback = _FakeProvider(
            "yfinance",
            [
                Bar(signal, 100.0),
                Bar(datetime(2026, 5, 26, 13, 30, tzinfo=timezone.utc), 105.0),
            ],
        )

        bars = get_bars(
            [primary, fallback], "AAPL", signal, signal, prefer_fallback=True
        )

        self.assertEqual(bars[0].close, 100.0)
        self.assertEqual(fallback.calls, 1)

    def test_get_bars_falls_back_when_primary_empty(self) -> None:
        start = datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc)
        primary = _FakeProvider("alpaca", [])
        fallback = _FakeProvider("yfinance", [Bar(start, 100.0)])

        bars = get_bars(
            [primary, fallback], "AAPL", start, start, prefer_fallback=False
        )

        self.assertEqual([b.close for b in bars], [100.0])

    def test_get_bars_reraises_primary_error_when_all_empty(self) -> None:
        start = datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc)
        request = httpx.Request("GET", "https://data.alpaca.markets/v2/stocks/X/bars")
        response = httpx.Response(429, request=request)
        primary = _FakeProvider(
            "alpaca",
            error=httpx.HTTPStatusError("rate", request=request, response=response),
        )
        fallback = _FakeProvider("yfinance", [])

        with self.assertRaises(httpx.HTTPStatusError):
            get_bars([primary, fallback], "AAPL", start, start, prefer_fallback=True)

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
