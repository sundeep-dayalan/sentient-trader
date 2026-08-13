"""Deterministic provider that answers the committee from replay fixtures.

Selected only when ``REPLAY_MODE=true`` and the configured provider's own API
key is absent, so a contributor can run the full graph with no LLM account.
When a real key is present the normal provider is used and this file is not
reached, which keeps the demo honest: the committee you see is either the real
one or an explicitly canned one, never a blend.

The implementation makes no network call and holds no mutable state. Every
answer is a deep copy of a code-owned fixture payload validated through the
same response model the live providers must satisfy, so a schema change breaks
here first rather than in production. An unknown headline or an unresolvable
stage raises; the persona nodes turn that into a neutral, low-conviction
fallback and the risk gate ends at HOLD.
"""

from __future__ import annotations

import logging
from typing import Any

from replay import (
    REPLAY_IDENTITY_FIELD,
    ReplayFixtureError,
    fixture_for_case,
    resolve_stage,
)

log = logging.getLogger("agent.llm")

DETERMINISTIC_REPLAY_PROVIDER_NAME = "deterministic-replay"
DETERMINISTIC_REPLAY_MODEL_ID = "replay-deterministic-v1"


def _user_prompt(messages: list[dict]) -> str:
    """Concatenate the user turns only.

    System prompts are Supabase-editable, so they must never influence which
    fixture or stage is selected.
    """
    return "\n".join(
        str(message.get("content") or "")
        for message in messages
        if str(message.get("role") or "") == "user"
    )


class DeterministicReplayProvider:
    name = DETERMINISTIC_REPLAY_PROVIDER_NAME

    def call(
        self,
        response_model: type,
        messages: list[dict],
        *,
        max_retries: int = 1,
    ) -> tuple[Any, str]:
        prompt = _user_prompt(messages)
        replay_case = next(
            (
                message.get(REPLAY_IDENTITY_FIELD)
                for message in messages
                if message.get(REPLAY_IDENTITY_FIELD) is not None
            ),
            None,
        )
        fixture = fixture_for_case(replay_case)
        if fixture is None:
            raise ReplayFixtureError(
                "REPLAY_MODE is on with no LLM key, and this headline is not a "
                "replay fixture. Seed the fixtures with "
                "`python inject_dummy.py --replay`, or set a provider API key "
                "to analyze your own headline."
            )
        stage = resolve_stage(response_model, prompt)
        result = response_model(**fixture.payload(stage))
        log.info(
            "Replay committee [%s]: canned %s output from fixture %s",
            fixture.ticker,
            stage,
            fixture.case,
        )
        return result, DETERMINISTIC_REPLAY_MODEL_ID
