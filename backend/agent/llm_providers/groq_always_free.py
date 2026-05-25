"""Groq provider that dynamically uses currently available free text models."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from typing import Any, Callable
from urllib import error as urlerror
from urllib import request as urlrequest

import instructor
from groq import Groq

import config
from llm_providers.base import (
    DEFAULT_OPENROUTER_COOLDOWN_SECONDS,
    DEFAULT_OPENROUTER_MAX_WAIT_SECONDS,
    LLMProviderConfig,
    _is_model_not_found_error,
    _is_rate_limit_error,
    _is_structured_output_error,
    _retry_after_seconds,
    _wrap_with_langsmith,
)

log = logging.getLogger("agent.llm")


_EXCLUDED_MODEL_ID_TERMS = (
    "whisper",
    "prompt-guard",
    "safeguard",
    "guard",
    "orpheus",
    "tts",
    "speech",
    "audio",
    "compound",
)

_FAMILY_SCORE_HINTS = (
    ("gpt-oss", 260),
    ("qwen", 240),
    ("llama", 180),
    ("mixtral", 150),
    ("gemma", 140),
    ("deepseek", 140),
)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _model_size_billions(model_id: str) -> float:
    sizes = [
        float(match)
        for match in re.findall(
            r"(\d+(?:\.\d+)?)\s*b(?:\b|-|_)", model_id, re.I
        )
    ]
    return max(sizes, default=0.0)


def _score_groq_model_for_analysis(model: dict[str, Any]) -> tuple[float, str | None]:
    model_id = model.get("id")
    if not isinstance(model_id, str) or not model_id:
        return 0.0, "missing id"
    if model.get("active") is not True:
        return 0.0, "inactive"

    model_id_l = model_id.lower()
    if any(term in model_id_l for term in _EXCLUDED_MODEL_ID_TERMS):
        return 0.0, "non-analysis model"

    context_window = _as_int(model.get("context_window"))
    if context_window < config.GROQ_MIN_CONTEXT_WINDOW:
        return 0.0, f"context window below {config.GROQ_MIN_CONTEXT_WINDOW}"

    max_completion_tokens = _as_int(model.get("max_completion_tokens"))
    if max_completion_tokens < config.GROQ_MIN_COMPLETION_TOKENS:
        return 0.0, f"completion limit below {config.GROQ_MIN_COMPLETION_TOKENS}"

    score = 0.0
    score += min(_model_size_billions(model_id_l), 160.0) * 4.0
    score += min(context_window, 131_072) / 2048
    score += min(max_completion_tokens, 65_536) / 4096

    for term, bonus in _FAMILY_SCORE_HINTS:
        if term in model_id_l:
            score += bonus
            break

    if "instruct" in model_id_l:
        score += 45
    if "versatile" in model_id_l:
        score += 45
    if "reason" in model_id_l:
        score += 45
    if "instant" in model_id_l:
        score -= 120
    if "preview" in model_id_l:
        score -= 35

    owner = str(model.get("owned_by") or "").lower()
    if owner in {"openai", "meta"}:
        score += 25
    elif "alibaba" in owner:
        score += 20

    created = _as_int(model.get("created"))
    if created:
        score += min(max(created - 1_600_000_000, 0), 250_000_000) / 10_000_000

    return score, None


def _select_policy_ranked_groq_models(payload: dict[str, Any]) -> list[str]:
    scored: list[tuple[float, str]] = []
    rejected: list[str] = []
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue
        score, reason = _score_groq_model_for_analysis(item)
        model_id = item.get("id")
        if score > 0 and isinstance(model_id, str):
            scored.append((score, model_id))
        elif isinstance(model_id, str) and reason:
            rejected.append(f"{model_id} ({reason})")

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    if rejected:
        log.info(
            "ModelRouter: rejected non-candidate Groq models: %s", ", ".join(rejected)
        )
    return [model_id for _, model_id in scored]


def _fetch_groq_models_payload() -> dict[str, Any] | None:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        log.warning(
            "ModelRouter: GROQ_API_KEY missing; using fallback Groq cascade without discovery"
        )
        return None

    req = urlrequest.Request(
        config.GROQ_MODELS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "sentient-trader-agent/1.0",
        },
    )

    try:
        with urlrequest.urlopen(
            req, timeout=config.GROQ_MODEL_DISCOVERY_TIMEOUT
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, TimeoutError, json.JSONDecodeError, urlerror.URLError) as exc:
        log.warning(
            "ModelRouter: could not fetch Groq model list (%s); using fallback cascade",
            exc,
        )
        return None


def _resolve_groq_model_tiers() -> list[str]:
    payload = _fetch_groq_models_payload()
    if payload is not None:
        selected = _select_policy_ranked_groq_models(payload)
        if selected:
            log.info("ModelRouter: active Groq free cascade: %s", " -> ".join(selected))
            return selected
        log.error(
            "ModelRouter: no active Groq text-analysis models found; using fallback cascade"
        )

    return list(config.GROQ_MODEL_DISCOVERY_FALLBACK)


class GroqAlwaysFreeProvider:
    name = "groq-always-free"

    def __init__(
        self,
        provider_config: LLMProviderConfig,
        *,
        patched_client: Any | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if provider_config.models:
            raise ValueError("groq-always-free does not accept configured models")
        if not os.environ.get("GROQ_API_KEY") and patched_client is None:
            raise RuntimeError("GROQ_API_KEY is required for groq-always-free")

        self._now = clock
        self._sleep = sleeper
        self.tiers = _resolve_groq_model_tiers()
        self._cooldown_until: dict[str, float] = {}
        self._disabled_models: set[str] = set()
        self.client = patched_client or self._build_client()

    def _build_client(self) -> Any:
        groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
        groq_client = _wrap_with_langsmith(groq_client, self.name)
        return instructor.from_groq(groq_client, mode=instructor.Mode.JSON)

    def call(
        self,
        response_model: type,
        messages: list[dict],
        *,
        max_retries: int = 1,
    ) -> tuple[Any, str]:
        while True:
            now = self._now()
            available = [
                model
                for model in self.tiers
                if now >= self._cooldown_until.get(model, 0)
                and model not in self._disabled_models
            ]

            if available:
                return self._try_models(
                    available,
                    response_model,
                    messages,
                    max_retries=max_retries,
                )

            live_tiers = [m for m in self.tiers if m not in self._disabled_models]
            if not live_tiers:
                raise RuntimeError("All configured Groq model tiers are unavailable.")

            soonest = min(self._cooldown_until.get(m, math.inf) for m in live_tiers)
            wait = soonest - self._now()
            if 0 < wait <= DEFAULT_OPENROUTER_MAX_WAIT_SECONDS:
                log.info(
                    "ModelRouter: all Groq tiers cooling down - waiting %.0fs",
                    wait,
                )
                self._sleep(wait + 1)
                continue
            raise RuntimeError("All Groq model tiers exhausted.")

    def _try_models(
        self,
        models: list[str],
        response_model: type,
        messages: list[dict],
        *,
        max_retries: int,
    ) -> tuple[Any, str]:
        last_structured_error: Exception | None = None
        for model in models:
            try:
                result = self.client.chat.completions.create(
                    model=model,
                    response_model=response_model,
                    messages=messages,
                    max_retries=max_retries,
                )
                log.debug("ModelRouter: Groq %s succeeded", model)
                return result, model
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    retry_after = _retry_after_seconds(
                        exc,
                        now=self._now(),
                        default_seconds=DEFAULT_OPENROUTER_COOLDOWN_SECONDS,
                    )
                    self._cooldown_until[model] = self._now() + retry_after
                    log.warning(
                        "ModelRouter: Groq rate-limited on %s - cooling down %.0fs, trying next tier",
                        model,
                        retry_after,
                    )
                    continue

                if _is_model_not_found_error(exc):
                    self._disabled_models.add(model)
                    log.warning(
                        "ModelRouter: Groq model %s unavailable - disabling for this process",
                        model,
                    )
                    continue

                if _is_structured_output_error(exc):
                    log.warning(
                        "ModelRouter: structured output failed on Groq %s - trying next tier",
                        model,
                    )
                    last_structured_error = exc
                    continue

                raise

        if last_structured_error is not None:
            raise last_structured_error
        if all(model in self._disabled_models for model in self.tiers):
            raise RuntimeError("All configured Groq model tiers are unavailable.")
        raise RuntimeError("All available Groq tiers rate-limited.")
