"""OpenRouter provider with priority fallback, cooldown, and quota handling."""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

import httpx
import instructor

from llm_providers.base import (
    DEFAULT_OPENROUTER_BASE_URL,
    LLMProviderConfig,
    OpenRouterModelConfig,
    _is_model_not_found_error,
    _is_quota_exhaustion,
    _is_rate_limit_error,
    _is_structured_output_error,
    _maybe_float,
    _quota_reset_seconds,
    _retry_after_seconds,
    _status_code,
    _wrap_with_langsmith,
)

log = logging.getLogger("agent.llm")


@dataclass
class _OpenRouterKeyStatus:
    limit: float | None = None
    limit_remaining: float | None = None
    limit_reset: str | None = None
    usage: float | None = None
    usage_daily: float | None = None
    usage_weekly: float | None = None
    usage_monthly: float | None = None
    is_free_tier: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def credit_exhausted(self) -> bool:
        return self.limit_remaining is not None and self.limit_remaining <= 0


class OpenRouterProvider:
    name = "openrouter"

    def __init__(
        self,
        provider_config: LLMProviderConfig,
        *,
        patched_client: Any | None = None,
        key_http_client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not provider_config.models:
            raise ValueError("openrouter requires at least one configured model")
        if not os.environ.get("OPENROUTER_API_KEY") and patched_client is None:
            raise RuntimeError("OPENROUTER_API_KEY is required for openrouter")

        self.config = provider_config
        self.models = list(provider_config.models)
        self.base_url = (provider_config.base_url or DEFAULT_OPENROUTER_BASE_URL).rstrip(
            "/"
        )
        self.routing = provider_config.routing
        self._now = clock
        self._sleep = sleeper
        self._cooldown_until: dict[str, float] = {}
        self._quota_exhausted_until: dict[str, float] = {}
        self._disabled_models: set[str] = set()
        self._provider_block_until: float | None = None
        self._last_key_check_at = 0.0
        self._last_key_status: _OpenRouterKeyStatus | None = None
        self._key_http_client = key_http_client
        self.client = patched_client or self._build_client()

        if patched_client is None:
            self.refresh_key_status(force=True, raise_on_auth=False)

    def _build_client(self) -> Any:
        from openai import OpenAI

        headers = {
            "X-OpenRouter-Title": os.environ.get(
                "OPENROUTER_APP_TITLE", "Sentient Trader"
            ),
            "X-OpenRouter-Experimental-Metadata": "enabled",
        }
        referer = os.environ.get("OPENROUTER_HTTP_REFERER")
        if referer:
            headers["HTTP-Referer"] = referer

        openai_client = OpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url=self.base_url,
            default_headers=headers,
            max_retries=0,
        )
        openai_client = _wrap_with_langsmith(openai_client, self.name)
        return instructor.from_openai(openai_client, mode=instructor.Mode.JSON)

    def refresh_key_status(
        self,
        *,
        force: bool = False,
        raise_on_auth: bool = True,
    ) -> _OpenRouterKeyStatus | None:
        now = self._now()
        if (
            not force
            and self._last_key_check_at
            and now - self._last_key_check_at
            < self.routing.key_status_check_interval_seconds
        ):
            return self._last_key_status

        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return None

        owns_client = self._key_http_client is None
        client = self._key_http_client or httpx.Client(timeout=5)
        try:
            response = client.get(
                f"{self.base_url}/key",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            )
            if response.status_code in {401, 403} and raise_on_auth:
                raise RuntimeError("OpenRouter API key is invalid or unauthorized")
            if response.status_code >= 400:
                log.warning(
                    "ModelRouter: OpenRouter key status check failed with HTTP %s",
                    response.status_code,
                )
                return self._last_key_status

            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else {}
            if not isinstance(data, dict):
                data = {}

            status = _OpenRouterKeyStatus(
                limit=_maybe_float(data.get("limit")),
                limit_remaining=_maybe_float(data.get("limit_remaining")),
                limit_reset=(
                    str(data.get("limit_reset"))
                    if data.get("limit_reset") is not None
                    else None
                ),
                usage=_maybe_float(data.get("usage")),
                usage_daily=_maybe_float(data.get("usage_daily")),
                usage_weekly=_maybe_float(data.get("usage_weekly")),
                usage_monthly=_maybe_float(data.get("usage_monthly")),
                is_free_tier=(
                    bool(data.get("is_free_tier"))
                    if data.get("is_free_tier") is not None
                    else None
                ),
                raw=data,
            )
            self._last_key_status = status
            self._last_key_check_at = now
            if status.credit_exhausted:
                self._provider_block_until = math.inf
                log.error(
                    "ModelRouter: OpenRouter key has no credits remaining; blocking provider"
                )
            else:
                self._provider_block_until = None
                log.info(
                    "ModelRouter: OpenRouter key ok (remaining=%s daily=%s weekly=%s monthly=%s free_tier=%s)",
                    status.limit_remaining,
                    status.usage_daily,
                    status.usage_weekly,
                    status.usage_monthly,
                    status.is_free_tier,
                )
            return status
        except RuntimeError:
            raise
        except Exception as exc:
            log.warning("ModelRouter: OpenRouter key status check failed: %s", exc)
            return self._last_key_status
        finally:
            if owns_client:
                client.close()

    def call(
        self,
        response_model: type,
        messages: list[dict],
        *,
        max_retries: int = 1,
    ) -> tuple[Any, str]:
        self.refresh_key_status(force=False, raise_on_auth=True)
        if self._provider_block_until == math.inf:
            raise RuntimeError("OpenRouter key credits exhausted.")

        last_structured_error: Exception | None = None
        while True:
            now = self._now()
            candidates = self._available_models(now)
            if candidates:
                for model in candidates:
                    try:
                        result = self.client.chat.completions.create(
                            model=model.id,
                            response_model=response_model,
                            messages=messages,
                            temperature=model.temperature,
                            top_p=model.top_p,
                            max_retries=max_retries,
                        )
                        log.debug("ModelRouter: OpenRouter %s succeeded", model.id)
                        return result, model.id
                    except Exception as exc:
                        action = self._handle_model_error(model, exc)
                        if action == "structured":
                            last_structured_error = exc
                        if action == "fatal":
                            raise
                        continue

            if last_structured_error is not None and not self._has_waitable_models():
                raise last_structured_error

            wait = self._seconds_until_next_model(self._now())
            if wait is None:
                if all(model.id in self._disabled_models for model in self.models):
                    raise RuntimeError("All configured OpenRouter models are disabled.")
                if last_structured_error is not None:
                    raise last_structured_error
                raise RuntimeError("All configured OpenRouter models are unavailable.")

            if wait <= self.routing.max_wait_seconds:
                log.info(
                    "ModelRouter: all OpenRouter models cooling down - waiting %.0fs",
                    wait,
                )
                self._sleep(wait + 0.01)
                last_structured_error = None
                continue

            raise RuntimeError(
                f"All OpenRouter models unavailable for at least {wait:.0f}s; exceeding max wait."
            )

    def _available_models(self, now: float) -> list[OpenRouterModelConfig]:
        return [
            model
            for model in self.models
            if model.id not in self._disabled_models
            and now >= self._cooldown_until.get(model.id, 0)
            and now >= self._quota_exhausted_until.get(model.id, 0)
        ]

    def _has_waitable_models(self) -> bool:
        return any(model.id not in self._disabled_models for model in self.models)

    def _seconds_until_next_model(self, now: float) -> float | None:
        resets: list[float] = []
        for model in self.models:
            if model.id in self._disabled_models:
                continue
            resets.append(self._cooldown_until.get(model.id, 0))
            resets.append(self._quota_exhausted_until.get(model.id, 0))
        resets = [
            reset for reset in resets if reset and reset > now and math.isfinite(reset)
        ]
        if not resets:
            return None
        return max(min(resets) - now, 0.01)

    def _handle_model_error(
        self,
        model: OpenRouterModelConfig,
        exc: Exception,
    ) -> Literal["continue", "structured", "fatal"]:
        status = _status_code(exc)
        raw = str(exc).lower()
        now = self._now()

        if status == 402 or "insufficient credits" in raw or "negative credit" in raw:
            self._provider_block_until = math.inf
            self.refresh_key_status(force=True, raise_on_auth=False)
            log.error("ModelRouter: OpenRouter credits exhausted; failing call")
            return "fatal"

        if status in {401, 403} and not _is_model_not_found_error(exc):
            log.error("ModelRouter: OpenRouter auth/permission error; failing call")
            return "fatal"

        if _is_model_not_found_error(exc):
            self._disabled_models.add(model.id)
            log.warning(
                "ModelRouter: OpenRouter model %s unavailable - disabling for this process",
                model.id,
            )
            return "continue"

        if _is_rate_limit_error(exc):
            if _is_quota_exhaustion(exc):
                reset = _quota_reset_seconds(
                    exc,
                    now=now,
                    default_seconds=self.routing.default_cooldown_seconds,
                )
                self._quota_exhausted_until[model.id] = now + reset
                log.warning(
                    "ModelRouter: OpenRouter quota exhausted on %s for %.0fs - trying next configured model",
                    model.id,
                    reset,
                )
            else:
                retry_after = _retry_after_seconds(
                    exc,
                    now=now,
                    default_seconds=self.routing.default_cooldown_seconds,
                )
                self._cooldown_until[model.id] = now + retry_after
                log.warning(
                    "ModelRouter: OpenRouter rate-limited on %s for %.0fs - trying next configured model",
                    model.id,
                    retry_after,
                )
            return "continue"

        if status in {502, 503, 504}:
            retry_after = _retry_after_seconds(
                exc,
                now=now,
                default_seconds=self.routing.default_cooldown_seconds,
            )
            self._cooldown_until[model.id] = now + retry_after
            log.warning(
                "ModelRouter: OpenRouter provider error on %s for %.0fs - trying next configured model",
                model.id,
                retry_after,
            )
            return "continue"

        if _is_structured_output_error(exc):
            log.warning(
                "ModelRouter: structured output failed on OpenRouter %s - trying next configured model",
                model.id,
            )
            return "structured"

        return "fatal"
