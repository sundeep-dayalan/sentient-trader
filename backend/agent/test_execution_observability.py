import unittest
from types import SimpleNamespace

from logger import trade_observability_fields
from trader import AlpacaTrader


class ExecutionObservabilityTests(unittest.TestCase):
    def test_submitted_without_order_id_is_not_counted_as_executed(self) -> None:
        fields = trade_observability_fields(
            decision_trace={
                "portfolio_manager_decision": {
                    "model": "qwen",
                    "action": "BUY",
                    "confidence": 0.9,
                },
                "llm_operations": [{"step": "portfolio_manager_synthesis"}],
                "risk_gate": {
                    "should_trade": True,
                    "inputs": {"calibrated_confidence": 0.82},
                    "committee_metrics": {"confidence_cap": 0.82},
                },
                "execution": {
                    "submitted": True,
                    "action": "BUY",
                    "client_order_id": "proof-client-id",
                    "order_id": None,
                    "status": "accepted",
                    "error": None,
                },
                "processing_started_at": "2026-05-25T10:00:00+00:00",
                "processing_finished_at": "2026-05-25T10:00:02+00:00",
            },
            trade_action="BUY",
            order_id=None,
        )

        self.assertNotIn("executed_action", fields)
        self.assertEqual(fields["order_status"], "accepted")
        self.assertIn("no Alpaca order_id", fields["execution_error"])
        self.assertEqual(fields["decision_path"], "full_debate")
        self.assertEqual(fields["calibrated_confidence"], 0.82)

    def test_submitted_with_order_id_but_not_filled_has_no_executed_action(self) -> None:
        """Fix #3: submitted=True + order_id but fill_status='new' should NOT set executed_action."""
        fields = trade_observability_fields(
            decision_trace={
                "llm_operations": [{"step": "portfolio_manager_synthesis"}],
                "risk_gate": {"should_trade": True},
                "execution": {
                    "submitted": True,
                    "action": "BUY",
                    "order_id": "alpaca-123",
                    "status": "new",
                    "fill_status": "new",
                    "error": None,
                },
            },
            trade_action="BUY",
            order_id="alpaca-123",
        )

        self.assertNotIn("executed_action", fields)
        self.assertEqual(fields["order_status"], "new")

    def test_submitted_and_filled_sets_executed_action(self) -> None:
        """Fix #3: submitted=True + order_id + fill_status='filled' SHOULD set executed_action."""
        fields = trade_observability_fields(
            decision_trace={
                "llm_operations": [{"step": "portfolio_manager_synthesis"}],
                "risk_gate": {"should_trade": True},
                "execution": {
                    "submitted": True,
                    "action": "BUY",
                    "order_id": "alpaca-456",
                    "status": "filled",
                    "fill_status": "filled",
                    "filled_avg_price": 123.45,
                    "error": None,
                },
            },
            trade_action="BUY",
            order_id="alpaca-456",
        )

        self.assertEqual(fields["executed_action"], "BUY")
        self.assertEqual(fields["order_status"], "filled")

    def test_partially_filled_sets_executed_action(self) -> None:
        """Fix #3: partially_filled also counts as a real execution."""
        fields = trade_observability_fields(
            decision_trace={
                "llm_operations": [{"step": "portfolio_manager_synthesis"}],
                "risk_gate": {"should_trade": True},
                "execution": {
                    "submitted": True,
                    "action": "BUY",
                    "order_id": "alpaca-789",
                    "status": "partially_filled",
                    "fill_status": "partially_filled",
                    "error": None,
                },
            },
            trade_action="BUY",
            order_id="alpaca-789",
        )

        self.assertEqual(fields["executed_action"], "BUY")

    def test_price_move_blocked_has_no_executed_action(self) -> None:
        """Fix #4: price-move gate blocks should record the error but no executed_action."""
        fields = trade_observability_fields(
            decision_trace={
                "llm_operations": [{"step": "portfolio_manager_synthesis"}],
                "risk_gate": {"should_trade": True},
                "execution": {
                    "submitted": False,
                    "action": "BUY",
                    "order_id": None,
                    "status": "price_move_blocked",
                    "error": "Price-move gate: AAPL moved up 4.2% since analysis",
                    "price_move_gate": {
                        "enabled": True,
                        "blocked": True,
                        "move_pct": 0.042,
                    },
                },
            },
            trade_action="BUY",
            order_id=None,
        )

        self.assertNotIn("executed_action", fields)
        self.assertEqual(fields["order_status"], "price_move_blocked")
        self.assertIn("Price-move gate", fields.get("execution_error", ""))

    def test_deterministic_pre_screen_path_is_queryable(self) -> None:
        fields = trade_observability_fields(
            decision_trace={
                "portfolio_manager_decision": {
                    "model": "deterministic-pre-screen",
                    "action": "HOLD",
                },
                "llm_operations": [],
                "risk_gate": {"should_trade": False},
                "execution": {"submitted": False, "action": "HOLD"},
            },
            trade_action="HOLD",
            order_id=None,
        )

        self.assertEqual(fields["decision_path"], "pre_screen")
        self.assertEqual(fields["pm_recommendation"], "HOLD")
        self.assertIs(fields["risk_should_trade"], False)


class FakeOrderClient:
    def __init__(self, submitted_order, lookup_order=None, lookup_error=None) -> None:
        self.submitted_order = submitted_order
        self.lookup_order = lookup_order
        self.lookup_error = lookup_error

    def submit_order(self, order_data):
        self.order_data = order_data
        return self.submitted_order

    def get_order_by_client_id(self, client_order_id):
        self.lookup_client_order_id = client_order_id
        if self.lookup_error:
            raise self.lookup_error
        return self.lookup_order


class AlpacaTraderOrderResultTests(unittest.TestCase):
    def trader_with_client(self, client: FakeOrderClient) -> AlpacaTrader:
        trader = AlpacaTrader.__new__(AlpacaTrader)
        trader._dry_run = False
        trader._client = client
        return trader

    def test_missing_order_id_returns_failed_result(self) -> None:
        trader = self.trader_with_client(
            FakeOrderClient(
                submitted_order=SimpleNamespace(id=None, status="accepted"),
                lookup_error=RuntimeError("not found"),
            )
        )

        result = trader.place_order("AAPL", "BUY", quantity=1, client_order_id="cid")

        self.assertFalse(result.submitted)
        self.assertIsNone(result.order_id)
        self.assertEqual(result.client_order_id, "cid")
        self.assertIn("returned no order_id", result.error or "")

    def test_missing_order_id_recovers_by_client_order_id_lookup(self) -> None:
        trader = self.trader_with_client(
            FakeOrderClient(
                submitted_order=SimpleNamespace(id=None, status="accepted"),
                lookup_order=SimpleNamespace(id="alpaca-order-1", status="accepted"),
            )
        )

        result = trader.place_order("AAPL", "BUY", quantity=1, client_order_id="cid")

        self.assertTrue(result.submitted)
        self.assertEqual(result.order_id, "alpaca-order-1")
        self.assertEqual(result.client_order_id, "cid")


if __name__ == "__main__":
    unittest.main()
