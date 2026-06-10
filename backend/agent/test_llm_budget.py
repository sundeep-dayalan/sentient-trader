"""Unit tests for the daily LLM-call budget kill-switch (llm_budget.py)."""

import os
import unittest
from unittest import mock

import llm_budget
from llm_budget import LLMBudget


class FakeRedis:
    """Minimal stand-in that emulates the INCRBY/GET semantics of the consume
    Lua script in Python so we can test LLMBudget without a real Redis."""

    def __init__(self, raise_on_eval: bool = False) -> None:
        self.store: dict[str, int] = {}
        self.raise_on_eval = raise_on_eval

    def eval(self, _script, _numkeys, key, cost, budget, _expireat):
        if self.raise_on_eval:
            raise RuntimeError("redis down")
        cost, budget = int(cost), int(budget)
        used = self.store.get(key, 0)
        if used + cost > budget:
            return -1
        self.store[key] = used + cost
        return self.store[key]

    def get(self, key):
        value = self.store.get(key)
        return str(value) if value is not None else None


class LLMBudgetTests(unittest.TestCase):
    def test_disabled_when_budget_zero(self):
        with mock.patch.dict(os.environ, {"LLM_DAILY_CALL_BUDGET": "0"}):
            budget = LLMBudget(redis_client=FakeRedis())
            self.assertFalse(budget.enabled)
            result = budget.try_consume(cost=4)
            self.assertTrue(result["allowed"])
            self.assertFalse(result["enabled"])

    def test_allows_until_budget_then_blocks(self):
        with mock.patch.dict(os.environ, {"LLM_DAILY_CALL_BUDGET": "8"}):
            budget = LLMBudget(redis_client=FakeRedis())
            self.assertTrue(budget.try_consume(cost=4)["allowed"])  # used 4/8
            self.assertTrue(budget.try_consume(cost=4)["allowed"])  # used 8/8
            blocked = budget.try_consume(cost=4)  # would be 12/8
            self.assertFalse(blocked["allowed"])
            self.assertEqual(blocked["used"], 8)
            self.assertEqual(blocked["budget"], 8)

    def test_fails_open_on_redis_error(self):
        with mock.patch.dict(os.environ, {"LLM_DAILY_CALL_BUDGET": "8"}):
            budget = LLMBudget(redis_client=FakeRedis(raise_on_eval=True))
            result = budget.try_consume(cost=4)
            self.assertTrue(result["allowed"])
            self.assertTrue(result.get("degraded"))

    def test_status_reports_remaining(self):
        with mock.patch.dict(os.environ, {"LLM_DAILY_CALL_BUDGET": "10"}):
            budget = LLMBudget(redis_client=FakeRedis())
            budget.try_consume(cost=4)
            status = budget.status()
            self.assertEqual(status["budget"], 10)
            self.assertEqual(status["used"], 4)
            self.assertEqual(status["remaining"], 6)
            self.assertFalse(status["exhausted"])

    def test_invalid_budget_env_disables(self):
        with mock.patch.dict(os.environ, {"LLM_DAILY_CALL_BUDGET": "not-a-number"}):
            self.assertEqual(llm_budget.daily_budget(), 0)


if __name__ == "__main__":
    unittest.main()
