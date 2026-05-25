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

import logging
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


def create_llm_client() -> LLMClient:
    """Build the active provider from Supabase config and return a stable wrapper."""
    provider_config = parse_llm_provider_config(config.LLM_PROVIDER_CONFIG)
    if provider_config.type == "openrouter":
        provider: Provider = OpenRouterProvider(provider_config)
    else:
        provider = GroqAlwaysFreeProvider(provider_config)
    log.info("ModelRouter: active provider=%s", provider.name)
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
        return client.provider.call(
            response_model,
            messages,
            max_retries=max_retries,
        )


__all__ = [
    "GroqAlwaysFreeProvider",
    "LLMClient",
    "ModelRouter",
    "OpenRouterProvider",
    "_quota_reset_seconds",
    "_retry_after_seconds",
    "create_llm_client",
    "normalize_llm_provider_config",
    "parse_llm_provider_config",
    "sanitize_llm_error",
]
