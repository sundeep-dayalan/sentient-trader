"""
Daily LLM-call budget kill-switch.

A runaway loop or a misconfigured retry can quietly burn a paid OpenRouter
balance. This caps the number of LLM calls the agent makes per UTC day. When the
cap is reached the agent drops to *pre-screen-only* mode: every new signal gets a
deterministic HOLD without spending another LLM call, until the counter resets at
00:00 UTC.

Disabled by default (``LLM_DAILY_CALL_BUDGET=0`` ⇒ unlimited). Set a positive
integer to enable the cap. The cost of one full committee debate is four calls
(momentum, value, risk, synthesis).

Accounting model: the pre-screen node only *checks* (``check``) that there is
room for a debate — it does not reserve anything. The budget is then spent one
unit per *real* successful LLM call (``charge``), from the router. This keeps the
counter equal to work actually done: a debate that errors out, or a message that
is retried, never leaves a phantom charge behind. (The older ``try_consume``
reserve-up-front path is retained for compatibility but is no longer on the hot
path — it over-counted whenever a debate failed or a message was retried.)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from redis_client import create_redis_client

log = logging.getLogger("agent.llm_budget")

# One full debate = four persona LLM calls (momentum, value, risk, synthesis).
DEBATE_CALL_COST = max(int(os.environ.get("LLM_DEBATE_CALL_COST", "4")), 1)
BUDGET_KEY_PREFIX = os.environ.get("LLM_BUDGET_KEY_PREFIX", "sentient:llm:budget")

# Atomically consume `cost` calls iff it keeps the day's total within `budget`.
# Returns the post-increment total when consumed, or -1 when it would exceed.
_CONSUME_SCRIPT = """
local used = tonumber(redis.call("GET", KEYS[1]) or "0")
local cost = tonumber(ARGV[1])
local budget = tonumber(ARGV[2])
if used + cost > budget then
  return -1
end
local newval = redis.call("INCRBY", KEYS[1], cost)
redis.call("EXPIREAT", KEYS[1], tonumber(ARGV[3]))
return newval
"""


def daily_budget() -> int:
    """Configured daily LLM-call cap (0 = disabled / unlimited)."""
    try:
        return max(int(os.environ.get("LLM_DAILY_CALL_BUDGET", "0")), 0)
    except ValueError:
        return 0


def _utc_day_key(now: float) -> str:
    day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%d")
    return f"{BUDGET_KEY_PREFIX}:{day}"


def _end_of_utc_day_epoch(now: float) -> int:
    current = datetime.fromtimestamp(now, tz=timezone.utc)
    tomorrow = (current + timedelta(days=1)).date()
    reset = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)
    return int(reset.timestamp())


class LLMBudget:
    """Redis-backed daily LLM-call budget."""

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client or create_redis_client()
        if redis_client is None and self.enabled:
            log.warning(
                "LLMBudget initialized without an explicit Redis client; "
                "falling back to a new connection. The budget kill-switch may "
                "not share state with the cache."
            )

    @property
    def enabled(self) -> bool:
        return daily_budget() > 0

    def try_consume(self, cost: int = DEBATE_CALL_COST) -> dict[str, Any]:
        """
        Reserve `cost` calls for an upcoming debate.

        Returns a dict with ``allowed`` plus ``used``/``budget`` for logging and
        the decision trace. Fails *open* — a Redis error never blocks analysis,
        because dropping every signal would be worse than briefly overspending.
        """
        budget = daily_budget()
        if budget <= 0:
            return {"allowed": True, "enabled": False, "used": 0, "budget": 0}

        now = time.time()
        key = _utc_day_key(now)
        try:
            result = int(
                self._redis.eval(
                    _CONSUME_SCRIPT,
                    1,
                    key,
                    str(cost),
                    str(budget),
                    str(_end_of_utc_day_epoch(now)),
                )
            )
        except Exception:
            log.warning("LLM budget check failed; allowing call (fail-open)", exc_info=True)
            return {
                "allowed": True,
                "enabled": True,
                "used": 0,
                "budget": budget,
                "degraded": True,
            }

        if result < 0:
            used = self.used()
            log.warning(
                "LLM daily budget exhausted: used=%s budget=%s — pre-screen-only "
                "mode until UTC reset.",
                used,
                budget,
            )
            return {"allowed": False, "enabled": True, "used": used, "budget": budget}

        return {"allowed": True, "enabled": True, "used": result, "budget": budget}

    def check(self, cost: int = 1) -> dict[str, Any]:
        """
        Non-mutating authorization peek before starting a debate.

        Reports only whether there is room for at least one more call
        (``used < budget``). It deliberately does NOT require headroom for a
        whole debate: the budget is spent one unit per *real* LLM call via
        ``charge``, so a debate that starts near the cap simply overshoots by a
        few calls — negligible — rather than being blocked. Crucially, this keeps
        ``check`` independent of ``DEBATE_CALL_COST``: a misconfigured per-debate
        cost (e.g. accidentally set equal to the daily budget) can no longer brick
        the gate. Fails *open* on any Redis error.
        """
        budget = daily_budget()
        if budget <= 0:
            return {"allowed": True, "enabled": False, "used": 0, "budget": 0}
        used = self.used()
        allowed = used + max(1, cost) <= budget
        if not allowed:
            log.warning(
                "LLM daily budget exhausted: used=%s budget=%s — pre-screen-only "
                "mode until UTC reset.",
                used,
                budget,
            )
        return {"allowed": allowed, "enabled": True, "used": used, "budget": budget}

    def charge(self, cost: int = 1) -> int:
        """
        Record ``cost`` real LLM calls that actually happened.

        Called once per successful provider call, so the daily counter tracks
        true usage rather than up-front reservations. Fails *open* (a Redis error
        never breaks an in-flight debate) and is a no-op when the cap is disabled.
        Returns the post-increment day total (0 when disabled/degraded).
        """
        if daily_budget() <= 0 or cost <= 0:
            return 0
        now = time.time()
        key = _utc_day_key(now)
        try:
            total = int(self._redis.incrby(key, cost))
            self._redis.expireat(key, _end_of_utc_day_epoch(now))
            return total
        except Exception:
            log.warning("LLM budget charge failed; continuing (fail-open)", exc_info=True)
            return 0

    def used(self) -> int:
        try:
            raw = self._redis.get(_utc_day_key(time.time()))
            return int(raw) if raw is not None else 0
        except Exception:
            return 0

    def status(self) -> dict[str, Any]:
        budget = daily_budget()
        used = self.used() if budget > 0 else 0
        return {
            "enabled": budget > 0,
            "used": used,
            "budget": budget,
            "remaining": max(budget - used, 0),
            "exhausted": budget > 0 and used >= budget,
        }
