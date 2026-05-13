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
  On a 429: the actual retry-after duration from Groq's response is used
  as the cooldown — no hardcoded values or permanent blacklisting.
"""

from __future__ import annotations

import logging
import os
import re
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
    if any(term in raw for term in ("validation", "failed to parse", "instructor", "max retries", "json")):
        return "AI response failed structured validation — the system will retry on the next signal."
    # Generic fallback — still don't expose raw exception text
    return "AI analysis temporarily unavailable — the system will retry on the next signal."


# ── Retry-After Parsing ─────────────────────────────────────────────────────

def _parse_retry_after(exc: Exception) -> float:
    """
    Extract the retry-after duration (seconds) from a rate-limit error.

    Checks three sources in priority order:
      1. ``retry-after`` HTTP header on the raw Groq response — most
         reliable, set by Groq's API gateway, value is in seconds.
      2. Error message text — Groq embeds "try again in XmY.Zs" in the
         JSON body.  Parsed via regex as a fallback.
      3. Default 60s if neither source is available.
    """
    # ── 1. HTTP header (requires unwrapped groq.RateLimitError) ──────
    raw_exc: Exception | None = exc
    for _ in range(5):  # walk the exception chain, bounded depth
        if isinstance(raw_exc, RateLimitError):
            break
        raw_exc = getattr(raw_exc, "__cause__", None) or getattr(raw_exc, "__context__", None)
        if raw_exc is None:
            break

    if isinstance(raw_exc, RateLimitError) and hasattr(raw_exc, "response") and raw_exc.response:
        header = raw_exc.response.headers.get("retry-after")
        if header:
            try:
                return float(header)
            except ValueError:
                pass

    # ── 2. Parse "try again in XmY.Zs" from the error message ────────
    match = re.search(
        r"try again in\s+(?:(\d+(?:\.\d+)?)m)?\s*(?:(\d+(?:\.\d+)?)s)?",
        str(exc),
        re.IGNORECASE,
    )
    if match:
        minutes = float(match.group(1)) if match.group(1) else 0.0
        seconds = float(match.group(2)) if match.group(2) else 0.0
        parsed = minutes * 60 + seconds
        if parsed > 0:
            return parsed

    # ── 3. Default fallback ──────────────────────────────────────────
    return 60.0


# ── ModelRouter ──────────────────────────────────────────────────────────────

class ModelRouter:
    """
    Tries LLM models in quality-descending order, falling back on rate limits.

    When a model returns 429, the actual ``retry-after`` duration from Groq's
    response is used as the cooldown.  Groq's daily token limit (TPD) is a
    rolling 24-hour window, so a few minutes of cooldown is typically all
    that's needed — no permanent blacklisting required.

    If all tiers are cooling down simultaneously, the router waits for the
    soonest one to expire (up to 10 minutes) instead of failing immediately.

    If OVERRIDE_MODEL env var is set, the cascade is bypassed entirely —
    useful for local testing against a specific model tier.

    Tier order (quality / TPM tradeoff):
      1. openai/gpt-oss-120b     — 1K req/day,  8K TPM  (highest reasoning quality)
      2. llama-3.3-70b-versatile — 1K req/day, 12K TPM  (strong quality, more headroom)
      3. llama-3.1-8b-instant    — 14.4K req/day, 6K TPM (volume fallback)
    """

    TIERS: list[str] = config.MODEL_CASCADE

    def __init__(self) -> None:
        # model → epoch when cooldown expires (dynamic TTL from retry-after)
        self._cooldown_until: dict[str, float] = {}

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
        Raises:  RuntimeError if all tiers are exhausted.
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

        now = time.time()
        available = [
            m for m in self.TIERS
            if now >= self._cooldown_until.get(m, 0)
        ]
        if not available:
            # All models are cooling down — wait for the soonest one
            soonest = min(self._cooldown_until.values())
            wait = soonest - time.time()
            if 0 < wait <= 600:  # don't wait more than 10 minutes
                log.info(
                    "ModelRouter: all tiers cooling down — waiting %.0fs for next available model",
                    wait,
                )
                time.sleep(wait + 1)
                available = [
                    m for m in self.TIERS
                    if time.time() >= self._cooldown_until.get(m, 0)
                ]
            if not available:
                raise RuntimeError(
                    "All Groq model tiers exhausted."
                )

        last_structured_error: Exception | None = None

        for model in available:
            try:
                # Keep one instructor repair attempt for malformed JSON/schema
                # responses, then cascade to another model if validation still
                # fails. Rate-limit errors are still cooled down below.
                result = client.chat.completions.create(
                    model=model,
                    response_model=response_model,
                    messages=messages,
                    max_retries=max_retries,
                )
                log.debug("ModelRouter: %s succeeded", model)
                return result, model

            except Exception as exc:
                # instructor wraps groq.RateLimitError in tenacity.RetryError,
                # so `except RateLimitError` alone misses it.  Detect rate
                # limits by inspecting the full exception string instead —
                # this works regardless of wrapping layer.
                exc_str = str(exc).lower()
                is_rate_limit = (
                    isinstance(exc, RateLimitError)
                    or "rate limit" in exc_str
                    or "rate_limit" in exc_str
                    or "429" in exc_str
                )

                if not is_rate_limit:
                    is_structured_output_error = any(
                        term in exc_str
                        for term in ("validation", "failed to parse", "instructor", "max retries", "json")
                    )
                    if is_structured_output_error:
                        log.warning(
                            "ModelRouter: structured output failed on %s — trying next tier",
                            model,
                        )
                        last_structured_error = exc
                        continue

                    # Network/auth/provider errors are not a model-choice
                    # problem; preserve the original exception.
                    raise

                retry_after = _parse_retry_after(exc)
                self._cooldown_until[model] = time.time() + retry_after
                log.warning(
                    "ModelRouter: rate-limited on %s — cooling down %.0fs (retry-after), trying next tier",
                    model,
                    retry_after,
                )
                continue

        if last_structured_error is not None:
            raise last_structured_error

        raise RuntimeError("All available Groq tiers rate-limited.")
