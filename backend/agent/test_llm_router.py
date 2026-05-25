import logging
import os
import unittest

from llm_providers.base import (
    _quota_reset_seconds,
    _retry_after_seconds,
    normalize_llm_provider_config,
    parse_llm_provider_config,
)
from llm_providers.openrouter import OpenRouterProvider


class FakeClock:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body or {}

    def json(self) -> dict:
        return self._body


class FakeOpenRouterError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = FakeResponse(status_code, headers=headers, body=body)
        self.body = body


class FakeCompletions:
    def __init__(self, plan: dict[str, list[object]]) -> None:
        self.plan = plan
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        model = kwargs["model"]
        actions = self.plan.setdefault(model, [])
        if not actions:
            raise AssertionError(f"No fake response configured for {model}")
        action = actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


class FakeChat:
    def __init__(self, completions: FakeCompletions) -> None:
        self.completions = completions


class FakeLLMClient:
    def __init__(self, plan: dict[str, list[object]]) -> None:
        self.completions = FakeCompletions(plan)
        self.chat = FakeChat(self.completions)


class FakeKeyHTTPClient:
    def __init__(self, remaining: float | None = 100.0) -> None:
        self.remaining = remaining
        self.calls = 0

    def get(self, *_args, **_kwargs) -> FakeResponse:
        self.calls += 1
        return FakeResponse(
            200,
            body={
                "data": {
                    "limit": None,
                    "limit_remaining": self.remaining,
                    "usage": 0,
                    "usage_daily": 0,
                    "usage_weekly": 0,
                    "usage_monthly": 0,
                    "is_free_tier": True,
                }
            },
        )


def openrouter_config(max_wait_seconds: float = 600):
    return parse_llm_provider_config(
        {
            "type": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "routing": {
                "max_wait_seconds": max_wait_seconds,
                "default_cooldown_seconds": 60,
                "key_status_check_interval_seconds": 300,
            },
            "models": [
                {
                    "priority": 1,
                    "id": "free/primary:free",
                    "temperature": 0.2,
                    "top_p": 0.8,
                },
                {
                    "priority": 2,
                    "id": "paid/fallback",
                    "temperature": 0.7,
                    "top_p": 0.9,
                },
            ],
        }
    )


def provider(
    plan: dict[str, list[object]],
    *,
    clock: FakeClock | None = None,
    max_wait_seconds: float = 600,
) -> OpenRouterProvider:
    clock = clock or FakeClock()
    return OpenRouterProvider(
        openrouter_config(max_wait_seconds=max_wait_seconds),
        patched_client=FakeLLMClient(plan),
        key_http_client=FakeKeyHTTPClient(),
        clock=clock,
        sleeper=clock.sleep,
    )


class OpenRouterRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        logging.getLogger("agent.llm").disabled = True

    def setUp(self) -> None:
        os.environ["OPENROUTER_API_KEY"] = "test-key"

    def test_accepts_preference_model_aliases_and_sorts(self) -> None:
        normalized = normalize_llm_provider_config(
            {
                "type": "openrouter",
                "models": [
                    {"preference": 2, "model": "paid/fallback"},
                    {"preference": 1, "model": "free/primary:free"},
                ],
            }
        )

        self.assertEqual(normalized["models"][0]["id"], "free/primary:free")
        self.assertEqual(normalized["models"][1]["id"], "paid/fallback")

    def test_groq_always_free_discards_model_config(self) -> None:
        normalized = normalize_llm_provider_config(
            {"type": "groq-always-free", "models": [{"id": "not-allowed"}]}
        )

        self.assertEqual(normalized, {"type": "groq-always-free"})

    def test_openrouter_rejects_duplicate_priorities_and_bad_waits(self) -> None:
        with self.assertRaisesRegex(ValueError, "priorities must be unique"):
            normalize_llm_provider_config(
                {
                    "type": "openrouter",
                    "models": [
                        {"priority": 1, "id": "a"},
                        {"priority": 1, "id": "b"},
                    ],
                }
            )

        with self.assertRaisesRegex(ValueError, "max_wait_seconds must be positive"):
            normalize_llm_provider_config(
                {
                    "type": "openrouter",
                    "routing": {"max_wait_seconds": -1},
                    "models": [{"priority": 1, "id": "a"}],
                }
            )

    def test_openrouter_temporary_rate_limit_falls_back_same_call(self) -> None:
        router = provider(
            {
                "free/primary:free": [
                    FakeOpenRouterError(
                        429,
                        "Rate limit exceeded: free-models-per-min.",
                        headers={"Retry-After": "30"},
                    )
                ],
                "paid/fallback": ["ok"],
            }
        )

        result, model = router.call(str, [{"role": "user", "content": "x"}])

        self.assertEqual(result, "ok")
        self.assertEqual(model, "paid/fallback")
        self.assertEqual(
            [call["model"] for call in router.client.completions.calls],
            ["free/primary:free", "paid/fallback"],
        )

    def test_priority_recovers_after_cooldown(self) -> None:
        clock = FakeClock()
        router = provider(
            {
                "free/primary:free": [
                    FakeOpenRouterError(
                        429,
                        "Rate limit exceeded: free-models-per-min.",
                        headers={"Retry-After": "30"},
                    ),
                    "primary-ok",
                ],
                "paid/fallback": ["fallback-ok", "fallback-ok-2"],
            },
            clock=clock,
        )

        self.assertEqual(
            router.call(str, [{"role": "user", "content": "x"}]),
            ("fallback-ok", "paid/fallback"),
        )
        self.assertEqual(
            router.call(str, [{"role": "user", "content": "x"}]),
            ("fallback-ok-2", "paid/fallback"),
        )

        clock.now += 31
        self.assertEqual(
            router.call(str, [{"role": "user", "content": "x"}]),
            ("primary-ok", "free/primary:free"),
        )

    def test_daily_free_quota_exhaustion_skips_model_until_reset(self) -> None:
        clock = FakeClock()
        reset_ms = int((clock.now + 86_400) * 1000)
        router = provider(
            {
                "free/primary:free": [
                    FakeOpenRouterError(
                        429,
                        "Rate limit exceeded: free-models-per-day.",
                        body={
                            "error": {
                                "metadata": {
                                    "headers": {"X-RateLimit-Reset": str(reset_ms)}
                                }
                            }
                        },
                    )
                ],
                "paid/fallback": ["paid-ok", "paid-ok-again"],
            },
            clock=clock,
        )

        self.assertEqual(
            router.call(str, [{"role": "user", "content": "x"}]),
            ("paid-ok", "paid/fallback"),
        )
        self.assertEqual(
            router.call(str, [{"role": "user", "content": "x"}]),
            ("paid-ok-again", "paid/fallback"),
        )
        self.assertEqual(
            [call["model"] for call in router.client.completions.calls],
            ["free/primary:free", "paid/fallback", "paid/fallback"],
        )

    def test_all_models_cooling_waits_for_earliest_within_max_wait(self) -> None:
        clock = FakeClock()
        router = provider(
            {
                "free/primary:free": [
                    FakeOpenRouterError(429, "per minute", headers={"Retry-After": "2"}),
                    "primary-after-wait",
                ],
                "paid/fallback": [
                    FakeOpenRouterError(429, "per minute", headers={"Retry-After": "5"})
                ],
            },
            clock=clock,
            max_wait_seconds=10,
        )

        self.assertEqual(
            router.call(str, [{"role": "user", "content": "x"}]),
            ("primary-after-wait", "free/primary:free"),
        )
        self.assertTrue(clock.sleeps)
        self.assertLess(clock.sleeps[0], 3)

    def test_all_models_cooling_beyond_max_wait_raises(self) -> None:
        router = provider(
            {
                "free/primary:free": [
                    FakeOpenRouterError(
                        429,
                        "Rate limit exceeded: free-models-per-min.",
                        headers={"Retry-After": "100"},
                    )
                ],
                "paid/fallback": [
                    FakeOpenRouterError(
                        429,
                        "Rate limit exceeded.",
                        headers={"Retry-After": "120"},
                    )
                ],
            },
            max_wait_seconds=10,
        )

        with self.assertRaisesRegex(RuntimeError, "exceeding max wait"):
            router.call(str, [{"role": "user", "content": "x"}])

    def test_credit_exhaustion_is_provider_global_and_does_not_try_fallback(self) -> None:
        router = provider(
            {
                "free/primary:free": [
                    FakeOpenRouterError(402, "Your account has insufficient credits.")
                ],
                "paid/fallback": ["should-not-run"],
            }
        )

        with self.assertRaises(FakeOpenRouterError):
            router.call(str, [{"role": "user", "content": "x"}])

        self.assertEqual(
            [call["model"] for call in router.client.completions.calls],
            ["free/primary:free"],
        )

    def test_structured_output_failure_falls_back_to_next_model(self) -> None:
        router = provider(
            {
                "free/primary:free": [ValueError("failed to parse JSON response")],
                "paid/fallback": ["ok"],
            }
        )

        self.assertEqual(
            router.call(str, [{"role": "user", "content": "x"}]),
            ("ok", "paid/fallback"),
        )

    def test_retry_after_and_quota_reset_parse_openrouter_headers(self) -> None:
        now = 1_800_000_000.0
        reset_ms = int((now + 45) * 1000)
        exc = FakeOpenRouterError(
            429,
            "Rate limit exceeded: free-models-per-day.",
            body={
                "error": {
                    "metadata": {"headers": {"X-RateLimit-Reset": str(reset_ms)}}
                }
            },
        )

        self.assertEqual(_quota_reset_seconds(exc, now=now, default_seconds=60), 45)

        retry_exc = FakeOpenRouterError(
            429,
            "Rate limit exceeded.",
            headers={"Retry-After": "17"},
        )
        self.assertEqual(
            _retry_after_seconds(retry_exc, now=now, default_seconds=60), 17
        )


if __name__ == "__main__":
    unittest.main()
