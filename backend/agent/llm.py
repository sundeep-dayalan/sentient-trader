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
  Active models are discovered from Groq's live /models endpoint at startup,
  filtered for structured text analysis, and ranked by policy. Optional pinned
  models from GROQ_MODEL_PINNED_ORDER are tried first when active.
  On a 429: the actual retry-after duration from Groq's response is used
  as the cooldown — no hardcoded values or permanent blacklisting.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

import instructor
from groq import Groq, RateLimitError

import config

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
    """Extract the largest parameter count from IDs such as gpt-oss-120b or qwen3-32b."""
    sizes = [
        float(match)
        for match in re.findall(r"(\d+(?:\.\d+)?)\s*b(?:\b|-|_)", model_id, re.IGNORECASE)
    ]
    return max(sizes, default=0.0)


def _score_model_for_analysis(model: dict[str, Any]) -> tuple[float, str | None]:
    """
    Score one Groq model for this app's structured financial-analysis workload.

    This intentionally avoids requiring a hardcoded list of model IDs. The local
    policy encodes workload needs: active text model, large enough context, not a
    safety/audio/TTS/compound system, with a preference for larger instruction or
    reasoning families.
    """
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
    size_b = _model_size_billions(model_id_l)
    score += min(size_b, 160.0) * 4.0
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


def _select_policy_ranked_models(payload: dict[str, Any]) -> list[str]:
    """Rank active Groq models using the analysis policy above."""
    scored: list[tuple[float, str]] = []
    rejected: list[str] = []
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue
        score, reason = _score_model_for_analysis(item)
        model_id = item.get("id")
        if score > 0 and isinstance(model_id, str):
            scored.append((score, model_id))
        elif isinstance(model_id, str) and reason:
            rejected.append(f"{model_id} ({reason})")

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    if rejected:
        log.info("ModelRouter: rejected non-candidate Groq models: %s", ", ".join(rejected))
    return [model_id for _, model_id in scored]


def _select_ranked_active_models(
    payload: dict[str, Any],
    ranked_models: list[str],
) -> tuple[list[str], list[str]]:
    """
    Return configured models that Groq currently reports as active.

    Used for optional operator-pinned model preferences.
    """
    active_ids = {
        item.get("id")
        for item in payload.get("data", [])
        if isinstance(item, dict) and item.get("active") is True
    }
    active_ids = {model_id for model_id in active_ids if isinstance(model_id, str)}

    ranked_unique = list(dict.fromkeys(ranked_models))
    selected = [model for model in ranked_unique if model in active_ids]
    missing = [model for model in ranked_unique if model not in active_ids]
    return selected, missing


def _fetch_groq_models_payload() -> dict[str, Any] | None:
    """Fetch Groq's active model list. Failure is non-fatal; static config remains usable."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        log.warning("ModelRouter: GROQ_API_KEY missing; using fallback model cascade without discovery")
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
        with urlrequest.urlopen(req, timeout=config.GROQ_MODEL_DISCOVERY_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, TimeoutError, json.JSONDecodeError, urlerror.URLError) as exc:
        log.warning("ModelRouter: could not fetch Groq model list (%s); using fallback cascade", exc)
        return None


def _resolve_model_tiers(pinned_models: list[str]) -> list[str]:
    """Resolve optional pinned preferences plus auto-ranked active Groq models."""
    payload = _fetch_groq_models_payload()
    pinned_unique = list(dict.fromkeys(pinned_models))
    if payload is None:
        fallback = [model for model in config.GROQ_MODEL_DISCOVERY_FALLBACK if model not in pinned_unique]
        return [*pinned_unique, *fallback]

    auto_ranked = _select_policy_ranked_models(payload)
    if pinned_unique:
        pinned_active, missing = _select_ranked_active_models(payload, pinned_unique)
        if missing:
            log.warning("ModelRouter: skipping inactive/unavailable pinned Groq models: %s", ", ".join(missing))
        selected = [*pinned_active, *(model for model in auto_ranked if model not in pinned_active)]
    else:
        selected = auto_ranked

    if selected:
        log.info("ModelRouter: active Groq cascade: %s", " → ".join(selected))
        return selected

    log.error("ModelRouter: no active Groq text-analysis models found; using fallback cascade")
    fallback = [model for model in config.GROQ_MODEL_DISCOVERY_FALLBACK if model not in pinned_unique]
    return [*pinned_unique, *fallback]


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
    if "model_not_found" in raw or "does not exist or you do not have access" in raw:
        return "AI model is unavailable — the system will try another configured model."
    if "all groq model tiers" in raw or "all configured groq" in raw or "all available groq" in raw:
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

def _parse_duration_to_seconds(text: str) -> float | None:
    """Parse Groq duration fragments such as '300ms', '8.5s', or '10m48s'."""
    total = 0.0
    matched = False
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)", text, re.IGNORECASE):
        matched = True
        amount = float(value)
        unit = unit.lower()
        if unit == "ms":
            total += amount / 1000
        elif unit == "s":
            total += amount
        elif unit == "m":
            total += amount * 60
        elif unit == "h":
            total += amount * 3600
    return total if matched and total > 0 else None


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

    # ── 2. Parse "try again in 300ms / 8.5s / 10m48s" from the body ───
    match = re.search(
        r"try again in\s+(.+?)(?:\s+Need more tokens|\s*$)",
        str(exc),
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        parsed = _parse_duration_to_seconds(match.group(1))
        if parsed is not None:
            return parsed

    # ── 3. Default fallback ──────────────────────────────────────────
    return 60.0


def _is_model_not_found_error(exc: Exception) -> bool:
    """Return True for stale/unavailable model IDs that should not stop the cascade."""
    raw = str(exc).lower()
    return (
        "model_not_found" in raw
        or "does not exist or you do not have access" in raw
        or ("404" in raw and "model" in raw and "not found" in raw)
    )


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

    If MODEL_OVERRIDE is set in Supabase config, the cascade is bypassed
    entirely — useful for testing against a specific model tier.

    The default order is auto-ranked from Groq metadata. Operators can pin a
    small preferred prefix with GROQ_MODEL_PINNED_ORDER when needed.
    """

    TIERS: list[str] = config.GROQ_MODEL_PINNED_ORDER

    def __init__(self) -> None:
        self.tiers = _resolve_model_tiers(self.TIERS)
        # model → epoch when cooldown expires (dynamic TTL from retry-after)
        self._cooldown_until: dict[str, float] = {}
        # stale model IDs discovered at call time (for example revoked access)
        self._disabled_models: set[str] = set()

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
            m for m in self.tiers
            if now >= self._cooldown_until.get(m, 0)
            and m not in self._disabled_models
        ]
        if not available:
            live_tiers = [m for m in self.tiers if m not in self._disabled_models]
            if not live_tiers:
                raise RuntimeError("All configured Groq model tiers are unavailable.")

            # All models are cooling down — wait for the soonest one
            soonest = min(self._cooldown_until.get(m, 0) for m in live_tiers)
            wait = soonest - time.time()
            if 0 < wait <= 600:  # don't wait more than 10 minutes
                log.info(
                    "ModelRouter: all tiers cooling down — waiting %.0fs for next available model",
                    wait,
                )
                time.sleep(wait + 1)
                available = [
                    m for m in self.tiers
                    if time.time() >= self._cooldown_until.get(m, 0)
                    and m not in self._disabled_models
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
                    if _is_model_not_found_error(exc):
                        self._disabled_models.add(model)
                        log.warning(
                            "ModelRouter: %s is unavailable according to Groq — disabling for this process",
                            model,
                        )
                        continue

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

        if all(model in self._disabled_models for model in self.tiers):
            raise RuntimeError("All configured Groq model tiers are unavailable.")

        raise RuntimeError("All available Groq tiers rate-limited.")
