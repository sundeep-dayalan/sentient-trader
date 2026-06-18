"""
LLM Provider Router
===================

This module is the stable facade used by the LangGraph agent. Provider-specific
behavior lives under ``llm_providers/``:

  - ``groq_always_free.py`` dynamically discovers Groq's active free models.
  - ``openrouter.py`` handles ordered OpenRouter fallback/cooldowns/quotas.

The public contract stays intentionally small:
    router.call(client, ResponseModel, messages) -> (parsed_response, model_id)
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import config
from llm_providers.base import (
    LLMClient,
    Provider,
    _quota_reset_seconds,
    _retry_after_seconds,
    normalize_llm_provider_config,
    parse_llm_provider_config,
    sanitize_llm_error,
)
from llm_providers.groq_always_free import GroqAlwaysFreeProvider
from llm_providers.openrouter import OpenRouterProvider

log = logging.getLogger("agent.llm")


def _build_provider() -> Provider:
    provider_config = parse_llm_provider_config(config.LLM_PROVIDER_CONFIG)
    if provider_config.type == "openrouter":
        return OpenRouterProvider(provider_config)
    return GroqAlwaysFreeProvider(provider_config)


def _provider_config_fingerprint() -> str:
    normalized = normalize_llm_provider_config(config.LLM_PROVIDER_CONFIG)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)


class ReloadableLLMProvider:
    """
    Stable provider wrapper that hot-swaps the underlying implementation.

    The graph keeps this object for the life of the process. Before every LLM
    call, it checks the current Supabase-backed provider config fingerprint and
    rebuilds only when the provider/model settings changed. A failed rebuild
    keeps the previous working provider alive so an invalid edit does not kill
    the worker mid-run.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._provider: Provider | None = None
        self._fingerprint = ""

    @property
    def name(self) -> str:
        provider = self._provider
        return provider.name if provider is not None else "uninitialized"

    def refresh_if_needed(self, *, force: bool = False) -> Provider:
        with self._lock:
            try:
                fingerprint = _provider_config_fingerprint()
            except Exception:
                if self._provider is not None:
                    log.exception(
                        "ModelRouter: provider config is invalid; keeping active provider=%s",
                        self._provider.name,
                    )
                    return self._provider
                raise

            if (
                not force
                and self._provider is not None
                and fingerprint == self._fingerprint
            ):
                return self._provider

            old_name = self._provider.name if self._provider is not None else None
            try:
                provider = _build_provider()
            except Exception:
                if self._provider is not None:
                    log.exception(
                        "ModelRouter: provider config changed but reload failed; keeping active provider=%s",
                        self._provider.name,
                    )
                    return self._provider
                raise

            self._provider = provider
            self._fingerprint = fingerprint
            if old_name and old_name != provider.name:
                log.info(
                    "ModelRouter: provider hot-swapped from %s to %s",
                    old_name,
                    provider.name,
                )
            else:
                log.info("ModelRouter: active provider=%s", provider.name)
            return provider

    def call(
        self,
        response_model: type,
        messages: list[dict],
        *,
        max_retries: int = 1,
    ) -> tuple[Any, str]:
        provider = self.refresh_if_needed()
        return provider.call(
            response_model,
            messages,
            max_retries=max_retries,
        )


def create_llm_client() -> LLMClient:
    """Return a stable wrapper whose underlying provider can hot-reload."""
    provider = ReloadableLLMProvider()
    provider.refresh_if_needed(force=True)
    return LLMClient(provider=provider)


class ModelRouter:
    """
    Facade kept for compatibility with the LangGraph node code.

    The actual routing state lives inside the active provider owned by
    ``LLMClient``. This keeps model attribution intact while allowing provider
    implementations to maintain different state machines.
    """

    def call(
        self,
        client: LLMClient,
        response_model: type,
        messages: list[dict],
        *,
        max_retries: int = 1,
    ) -> tuple[Any, str]:
        if not isinstance(client, LLMClient):
            raise TypeError(
                "ModelRouter.call expected an LLMClient from create_llm_client()"
            )
        result = client.provider.call(
            response_model,
            messages,
            max_retries=max_retries,
        )
        # Charge the daily budget one unit per *real* successful call. A raised
        # call never reaches here, so failed/retried attempts cost nothing — the
        # counter tracks work actually done, not up-front reservations.
        budget = getattr(client, "budget", None)
        if budget is not None:
            try:
                budget.charge(1)
            except Exception:  # never let accounting break a live debate
                pass
        return result


__all__ = [
    "GroqAlwaysFreeProvider",
    "LLMClient",
    "ModelRouter",
    "OpenRouterProvider",
    "ReloadableLLMProvider",
    "_quota_reset_seconds",
    "_retry_after_seconds",
    "create_llm_client",
    "normalize_llm_provider_config",
    "parse_llm_provider_config",
    "sanitize_llm_error",
]
