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

    def incrby(self, key, amount):
        if self.raise_on_eval:
            raise RuntimeError("redis down")
        self.store[key] = self.store.get(key, 0) + int(amount)
        return self.store[key]

    def expireat(self, key, _when):
        return True


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


class ChargePerCallTests(unittest.TestCase):
    """The new model: check() is non-mutating; charge() records real calls."""

    def test_check_does_not_consume(self):
        with mock.patch.dict(os.environ, {"LLM_DAILY_CALL_BUDGET": "8"}):
            budget = LLMBudget(redis_client=FakeRedis())
            # Checking many times must never move the counter.
            for _ in range(5):
                self.assertTrue(budget.check()["allowed"])
            self.assertEqual(budget.used(), 0)

    def test_check_allows_near_cap_and_blocks_only_when_full(self):
        # Hardened semantics: check() needs room for one more call, not a whole
        # debate — so it stays independent of a (possibly misconfigured)
        # per-debate cost and only blocks when the budget is truly used up.
        with mock.patch.dict(os.environ, {"LLM_DAILY_CALL_BUDGET": "8"}):
            budget = LLMBudget(redis_client=FakeRedis())
            budget.charge(7)  # 7/8 — still room for one call
            self.assertTrue(budget.check()["allowed"])
            budget.charge(1)  # 8/8 — full
            self.assertFalse(budget.check()["allowed"])

    def test_check_not_bricked_by_huge_debate_cost(self):
        # The exact prod bug: LLM_DEBATE_CALL_COST set ~= the daily budget must
        # NOT block a brand-new day with plenty of real budget left.
        with mock.patch.dict(os.environ, {"LLM_DAILY_CALL_BUDGET": "4000"}):
            budget = LLMBudget(redis_client=FakeRedis())
            budget.charge(4)  # one real debate's worth of calls
            self.assertTrue(budget.check()["allowed"])  # 4/4000 → obviously fine

    def test_charge_tracks_real_calls(self):
        with mock.patch.dict(os.environ, {"LLM_DAILY_CALL_BUDGET": "100"}):
            budget = LLMBudget(redis_client=FakeRedis())
            for _ in range(4):  # one full debate = 4 real calls
                budget.charge(1)
            self.assertEqual(budget.used(), 4)

    def test_failed_debate_costs_nothing(self):
        # The whole point of the fix: a debate that checks-OK but never makes a
        # call (crash / retry) leaves the budget untouched — no phantom charge.
        with mock.patch.dict(os.environ, {"LLM_DAILY_CALL_BUDGET": "8"}):
            budget = LLMBudget(redis_client=FakeRedis())
            self.assertTrue(budget.check()["allowed"])  # authorized...
            # ...but the debate blew up before any provider.call → no charge.
            self.assertEqual(budget.used(), 0)

    def test_charge_noop_when_disabled(self):
        with mock.patch.dict(os.environ, {"LLM_DAILY_CALL_BUDGET": "0"}):
            budget = LLMBudget(redis_client=FakeRedis())
            self.assertEqual(budget.charge(1), 0)
            self.assertEqual(budget.used(), 0)

    def test_charge_fails_open_on_redis_error(self):
        with mock.patch.dict(os.environ, {"LLM_DAILY_CALL_BUDGET": "8"}):
            budget = LLMBudget(redis_client=FakeRedis(raise_on_eval=True))
            # Must not raise even if Redis is down mid-debate.
            self.assertEqual(budget.charge(1), 0)


if __name__ == "__main__":
    unittest.main()
