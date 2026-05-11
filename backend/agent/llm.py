"""
LLM Provider — Groq + instructor
==================================
All provider-specific code lives here. The rest of the agent pipeline
imports only from this module, never directly from ``groq`` or ``instructor``.

To swap providers (e.g. Groq → OpenAI → Anthropic):
  1. Replace this single file.
  2. Ensure create_llm_client() returns an instructor-patched client.
  3. Ensure ModelRouter.call() still returns (parsed_response, model_name).

No other file in the agent package needs to change.

ModelRouter: quota-aware cascade
  Each Groq model has its own independent rate-limit bucket.
  Order: openai/gpt-oss-120b → llama-3.3-70b-versatile → llama-3.1-8b-instant
  On a 429: per-minute limits skip to next tier; daily-exhausted models are
  blacklisted in-memory for the session.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import instructor
from groq import Groq, RateLimitError

import config

log = logging.getLogger("agent.llm")


# ── Client Factory ───────────────────────────────────────────────────────────

def create_llm_client() -> Any:
    """
    Build and return an instructor-patched Groq client.

    The returned object exposes ``client.chat.completions.create(...)``
    with automatic Pydantic validation via instructor's JSON mode.

    Swap this function's body to change providers — the rest of the
    agent pipeline only calls ``client.chat.completions.create()``.
    """
    return instructor.from_groq(
        Groq(api_key=os.environ["GROQ_API_KEY"]),
        mode=instructor.Mode.JSON,
    )


# ── Error Sanitisation ──────────────────────────────────────────────────────

def sanitize_llm_error(exc: Exception) -> str:
    """
    Convert any LLM-related exception into a clean, user-facing message.

    Raw API errors (429s, 500s, etc.) contain org IDs, billing URLs, and
    JSON blobs that should never reach the UI. This strips all of that
    and returns a short human-readable explanation.
    """
    raw = str(exc).lower()
    if "rate_limit" in raw or "rate limit" in raw or "429" in raw:
        if any(m in raw for m in ("per day", "daily", "tpd", "quota")):
            return "AI model daily quota exhausted — the system will resume automatically at midnight UTC."
        return "AI model temporarily rate-limited — the system will retry shortly."
    if "all groq model tiers" in raw or "all available groq" in raw:
        return "All AI model tiers are temporarily unavailable — the system will retry on the next signal."
    if "timeout" in raw or "timed out" in raw:
        return "AI model request timed out — the system will retry on the next signal."
    if "connection" in raw or "network" in raw:
        return "Network error reaching AI model — the system will retry on the next signal."
    # Generic fallback — still don't expose raw exception text
    return "AI analysis temporarily unavailable — the system will retry on the next signal."


# ── ModelRouter ──────────────────────────────────────────────────────────────

class ModelRouter:
    """
    Tries LLM models in quality-descending order, falling back on rate limits.

    Groq enforces two types of limits:
      - Per-minute (RPM / TPM): transient — skip to next tier, don't blacklist.
      - Daily (req/day / TPD):  persistent — blacklist the model for the session.

    The in-memory blacklist means we don't waste RTT on known-exhausted models.
    It resets on process restart, which is acceptable since daily limits also
    reset at midnight UTC.

    If OVERRIDE_MODEL env var is set, the cascade is bypassed entirely —
    useful for local testing against a specific model tier.

    Tier order (quality / TPM tradeoff):
      1. openai/gpt-oss-120b     — 1K req/day,  8K TPM  (highest reasoning quality)
      2. llama-3.3-70b-versatile — 1K req/day, 12K TPM  (strong quality, more headroom)
      3. llama-3.1-8b-instant    — 14.4K req/day, 6K TPM (volume fallback)
    """

    TIERS: list[str] = config.MODEL_CASCADE

    # Groq error messages that signal a daily (persistent) quota exhaustion.
    _DAILY_EXHAUSTION_MARKERS = ("per day", "daily", "quota exceeded", "day limit")

    def __init__(self) -> None:
        self._blacklisted: set[str] = set()
        self._rate_limited_until: dict[str, float] = {}  # model → epoch when 60s cooldown expires

    def call(
        self,
        client: Any,
        response_model: type,
        messages: list[dict],
        *,
        max_retries: int = 1,
    ) -> tuple[Any, str]:
        """
        Invoke the LLM with automatic model-tier fallback.

        Returns: (parsed_response, model_name_that_succeeded)
        Raises:  RuntimeError if all tiers are exhausted or blacklisted.
                 Any non-rate-limit exception propagates immediately.
        """
        # Hard override: bypass cascade entirely
        if config.MODEL_OVERRIDE:
            result = client.chat.completions.create(
                model=config.MODEL_OVERRIDE,
                response_model=response_model,
                messages=messages,
                max_retries=max_retries,
            )
            return result, config.MODEL_OVERRIDE

        available = [
            m for m in self.TIERS
            if m not in self._blacklisted
            and time.time() >= self._rate_limited_until.get(m, 0)
        ]
        if not available:
            # Check if any non-blacklisted model is just on a transient cooldown
            cooldown_models = {
                m: self._rate_limited_until[m]
                for m in self.TIERS
                if m not in self._blacklisted and m in self._rate_limited_until
            }
            if cooldown_models:
                # Wait for the soonest cooldown to expire, then retry once
                soonest = min(cooldown_models.values())
                wait = soonest - time.time()
                if 0 < wait <= 90:  # don't wait more than 90s
                    log.info("ModelRouter: all tiers cooling down — waiting %.0fs for next available model", wait)
                    time.sleep(wait + 1)
                    # Re-check availability after waiting
                    available = [
                        m for m in self.TIERS
                        if m not in self._blacklisted
                        and time.time() >= self._rate_limited_until.get(m, 0)
                    ]
            if not available:
                raise RuntimeError(
                    "All Groq model tiers exhausted for this session."
                )

        for model in available:
            try:
                result = client.chat.completions.create(
                    model=model,
                    response_model=response_model,
                    messages=messages,
                    max_retries=max_retries,
                )
                log.debug("ModelRouter: %s succeeded", model)
                return result, model

            except RateLimitError as exc:
                msg = str(exc).lower()
                is_daily = any(marker in msg for marker in self._DAILY_EXHAUSTION_MARKERS)
                if is_daily:
                    log.warning(
                        "ModelRouter: daily quota exhausted for %s — blacklisting for session",
                        model,
                    )
                    self._blacklisted.add(model)
                else:
                    # Per-minute limit — cool down this model for 60s, fall back now
                    self._rate_limited_until[model] = time.time() + 60
                    log.warning(
                        "ModelRouter: per-minute rate limit on %s — cooling down 60s, trying next tier",
                        model,
                    )
                continue

            except Exception:
                # Schema validation failures, network errors, etc. — don't try next tier,
                # the problem isn't the model choice.
                raise

        raise RuntimeError(
            "All available Groq tiers rate-limited."
        )
