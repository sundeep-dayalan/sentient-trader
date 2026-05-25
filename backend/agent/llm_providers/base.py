"""Shared config, error handling, and client contracts for LLM providers."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Literal, Protocol

from groq import RateLimitError

log = logging.getLogger("agent.llm")


SUPPORTED_PROVIDER_TYPES = {"groq-always-free", "openrouter"}
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MAX_WAIT_SECONDS = 600
DEFAULT_OPENROUTER_COOLDOWN_SECONDS = 60
DEFAULT_OPENROUTER_KEY_CHECK_INTERVAL_SECONDS = 300


@dataclass(frozen=True)
class OpenRouterModelConfig:
    priority: int
    id: str
    temperature: float = 0.7
    top_p: float = 0.7


@dataclass(frozen=True)
class OpenRouterRoutingConfig:
    max_wait_seconds: float = DEFAULT_OPENROUTER_MAX_WAIT_SECONDS
    default_cooldown_seconds: float = DEFAULT_OPENROUTER_COOLDOWN_SECONDS
    key_status_check_interval_seconds: float = (
        DEFAULT_OPENROUTER_KEY_CHECK_INTERVAL_SECONDS
    )


@dataclass(frozen=True)
class LLMProviderConfig:
    type: Literal["groq-always-free", "openrouter"]
    base_url: str | None = None
    routing: OpenRouterRoutingConfig = field(default_factory=OpenRouterRoutingConfig)
    models: tuple[OpenRouterModelConfig, ...] = ()


class Provider(Protocol):
    name: str

    def call(
        self,
        response_model: type,
        messages: list[dict],
        *,
        max_retries: int = 1,
    ) -> tuple[Any, str]:
        ...


@dataclass
class LLMClient:
    provider: Provider


def _as_float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _positive_config_float(value: Any, default: float, field_name: str) -> float:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def normalize_llm_provider_config(raw: Any) -> dict[str, Any]:
    """
    Return a JSON-serializable, validated provider config.

    Accepts both final field names (``priority``/``id``) and the early config
    sketch (``preference``/``model``) so existing drafts do not fail noisily.
    """
    if not isinstance(raw, dict) or not raw:
        raw = {"type": "groq-always-free"}

    provider_type = str(raw.get("type") or "groq-always-free").strip().lower()
    if provider_type not in SUPPORTED_PROVIDER_TYPES:
        raise ValueError(
            "llm_provider.type must be one of: groq-always-free, openrouter"
        )

    if provider_type == "groq-always-free":
        return {"type": "groq-always-free"}

    base_url = str(raw.get("base_url") or DEFAULT_OPENROUTER_BASE_URL).strip()
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("llm_provider.base_url must be an HTTP(S) URL")

    routing_raw = raw.get("routing") if isinstance(raw.get("routing"), dict) else {}
    routing = {
        "strategy": "ordered_fallback",
        "max_wait_seconds": _positive_config_float(
            routing_raw.get("max_wait_seconds"),
            DEFAULT_OPENROUTER_MAX_WAIT_SECONDS,
            "llm_provider.routing.max_wait_seconds",
        ),
        "default_cooldown_seconds": _positive_config_float(
            routing_raw.get("default_cooldown_seconds"),
            DEFAULT_OPENROUTER_COOLDOWN_SECONDS,
            "llm_provider.routing.default_cooldown_seconds",
        ),
        "key_status_check_interval_seconds": _positive_config_float(
            routing_raw.get("key_status_check_interval_seconds"),
            DEFAULT_OPENROUTER_KEY_CHECK_INTERVAL_SECONDS,
            "llm_provider.routing.key_status_check_interval_seconds",
        ),
    }

    raw_models = raw.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise ValueError("openrouter provider requires at least one model")

    models: list[dict[str, Any]] = []
    seen_priorities: set[int] = set()
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_models, start=1):
        if not isinstance(item, dict):
            raise ValueError("each openrouter model must be an object")
        priority = item.get("priority", item.get("preference", index))
        try:
            priority_int = int(priority)
        except (TypeError, ValueError) as exc:
            raise ValueError("openrouter model priority must be an integer") from exc
        if priority_int < 1:
            raise ValueError("openrouter model priority must be >= 1")
        if priority_int in seen_priorities:
            raise ValueError("openrouter model priorities must be unique")
        seen_priorities.add(priority_int)

        model_id = str(item.get("id") or item.get("model") or "").strip()
        if not model_id:
            raise ValueError("openrouter model id is required")
        if model_id in seen_ids:
            raise ValueError("openrouter model ids must be unique")
        seen_ids.add(model_id)

        temperature = _as_float(item.get("temperature"), 0.7)
        top_p = _as_float(item.get("top_p"), 0.7)
        if not 0 <= temperature <= 2:
            raise ValueError("openrouter model temperature must be between 0 and 2")
        if not 0 <= top_p <= 1:
            raise ValueError("openrouter model top_p must be between 0 and 1")

        models.append(
            {
                "priority": priority_int,
                "id": model_id,
                "temperature": temperature,
                "top_p": top_p,
            }
        )

    models.sort(key=lambda model: (model["priority"], model["id"]))
    return {
        "type": "openrouter",
        "base_url": base_url.rstrip("/"),
        "routing": routing,
        "models": models,
    }


def parse_llm_provider_config(raw: Any) -> LLMProviderConfig:
    normalized = normalize_llm_provider_config(raw)
    if normalized["type"] == "groq-always-free":
        return LLMProviderConfig(type="groq-always-free")

    routing_raw = normalized["routing"]
    models = tuple(
        OpenRouterModelConfig(
            priority=int(item["priority"]),
            id=str(item["id"]),
            temperature=float(item["temperature"]),
            top_p=float(item["top_p"]),
        )
        for item in normalized["models"]
    )
    return LLMProviderConfig(
        type="openrouter",
        base_url=str(normalized["base_url"]),
        routing=OpenRouterRoutingConfig(
            max_wait_seconds=float(routing_raw["max_wait_seconds"]),
            default_cooldown_seconds=float(routing_raw["default_cooldown_seconds"]),
            key_status_check_interval_seconds=float(
                routing_raw["key_status_check_interval_seconds"]
            ),
        ),
        models=models,
    )


def _langsmith_enabled() -> bool:
    return bool(os.environ.get("LANGSMITH_API_KEY")) or (
        os.environ.get("LANGSMITH_TRACING") == "true"
    )


def _wrap_with_langsmith(client: Any, provider_name: str) -> Any:
    if not _langsmith_enabled():
        return client
    try:
        from langsmith.wrappers import wrap_openai
    except ImportError:
        log.warning(
            "ModelRouter: langsmith package is not installed; skipping LLM tracing."
        )
        return client

    # Older LangSmith wrappers expect a completions namespace on OpenAI-shaped
    # clients. The Groq SDK is close but not identical, so provide a harmless
    # placeholder just like the previous implementation did.
    if provider_name == "groq-always-free" and not hasattr(client, "completions"):

        class DummyCompletions:
            def create(self, *args: Any, **kwargs: Any) -> None:
                return None

        client.completions = DummyCompletions()

    wrapped = wrap_openai(client)
    log.info("ModelRouter: LangSmith tracing enabled for %s client.", provider_name)
    return wrapped


def _exception_chain(exc: Exception) -> Iterable[Exception]:
    seen: set[int] = set()
    stack: list[Any] = [exc]
    while stack:
        current = stack.pop(0)
        if (
            current is None
            or id(current) in seen
            or not isinstance(current, BaseException)
        ):
            continue
        seen.add(id(current))
        yield current

        last_attempt = getattr(current, "last_attempt", None)
        if last_attempt is not None and hasattr(last_attempt, "exception"):
            try:
                stack.append(last_attempt.exception())
            except Exception:
                pass

        stack.append(getattr(current, "__cause__", None))
        stack.append(getattr(current, "__context__", None))


def _status_code(exc: Exception) -> int | None:
    for item in _exception_chain(exc):
        value = getattr(item, "status_code", None)
        if isinstance(value, int):
            return value
        response = getattr(item, "response", None)
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    match = re.search(r"\b(4\d\d|5\d\d)\b", str(exc))
    return int(match.group(1)) if match else None


def _headers_from_exception(exc: Exception) -> dict[str, str]:
    headers: dict[str, str] = {}

    def merge(mapping: Any) -> None:
        if not mapping:
            return
        items = mapping.items() if hasattr(mapping, "items") else []
        for key, value in items:
            headers[str(key).lower()] = str(value)

    for item in _exception_chain(exc):
        response = getattr(item, "response", None)
        merge(getattr(response, "headers", None))

        body = getattr(item, "body", None)
        if isinstance(body, dict):
            metadata = (
                body.get("error", {}).get("metadata")
                if isinstance(body.get("error"), dict)
                else body.get("metadata")
            )
            if isinstance(metadata, dict):
                merge(metadata.get("headers"))

        try:
            if response is not None and hasattr(response, "json"):
                payload = response.json()
                if isinstance(payload, dict):
                    metadata = (
                        payload.get("error", {}).get("metadata")
                        if isinstance(payload.get("error"), dict)
                        else payload.get("metadata")
                    )
                    if isinstance(metadata, dict):
                        merge(metadata.get("headers"))
        except Exception:
            pass

    raw = str(exc)
    for key in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"):
        match = re.search(rf"{key}['\"]?\s*[:=]\s*['\"]?([^,'\"\s}}]+)", raw, re.I)
        if match:
            headers[key] = match.group(1)

    return headers


def _parse_duration_to_seconds(text: str) -> float | None:
    """Parse fragments such as '300ms', '8.5s', or '10m48s'."""
    total = 0.0
    matched = False
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)", text, re.I):
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


def _parse_reset_epoch(value: str, now: float) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError, IndexError, OverflowError):
            return None

    # OpenRouter error metadata has been observed with epoch milliseconds.
    if number > 10_000_000_000:
        return number / 1000
    if number > 1_000_000_000:
        return number
    return now + number


def _seconds_until_next_utc_day(now: float) -> float:
    current = datetime.fromtimestamp(now, tz=timezone.utc)
    tomorrow = (current + timedelta(days=1)).date()
    reset = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)
    return max(reset.timestamp() - now, 1)


def _seconds_until_next_utc_week(now: float) -> float:
    current = datetime.fromtimestamp(now, tz=timezone.utc)
    days_until_monday = (7 - current.weekday()) or 7
    reset_date = (current + timedelta(days=days_until_monday)).date()
    reset = datetime(reset_date.year, reset_date.month, reset_date.day, tzinfo=timezone.utc)
    return max(reset.timestamp() - now, 1)


def _seconds_until_next_utc_month(now: float) -> float:
    current = datetime.fromtimestamp(now, tz=timezone.utc)
    year = current.year + (1 if current.month == 12 else 0)
    month = 1 if current.month == 12 else current.month + 1
    reset = datetime(year, month, 1, tzinfo=timezone.utc)
    return max(reset.timestamp() - now, 1)


def _retry_after_seconds(
    exc: Exception,
    *,
    now: float,
    default_seconds: float,
) -> float:
    headers = _headers_from_exception(exc)
    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            parsed = float(retry_after)
            if parsed > 0:
                return parsed
        except ValueError:
            parsed_epoch = _parse_reset_epoch(retry_after, now)
            if parsed_epoch is not None:
                return max(parsed_epoch - now, 1)

    reset = headers.get("x-ratelimit-reset")
    if reset:
        parsed_epoch = _parse_reset_epoch(reset, now)
        if parsed_epoch is not None:
            return max(parsed_epoch - now, 1)

    match = re.search(
        r"try again in\s+(.+?)(?:\s+Need more tokens|\s*$)",
        str(exc),
        re.I | re.S,
    )
    if match:
        parsed = _parse_duration_to_seconds(match.group(1))
        if parsed is not None:
            return parsed

    return default_seconds


def _quota_reset_seconds(
    exc: Exception,
    *,
    now: float,
    default_seconds: float,
) -> float:
    headers = _headers_from_exception(exc)
    reset = headers.get("x-ratelimit-reset") or headers.get("retry-after")
    if reset:
        parsed_epoch = _parse_reset_epoch(reset, now)
        if parsed_epoch is not None:
            return max(parsed_epoch - now, 1)

    raw = str(exc).lower()
    if "weekly" in raw or "per week" in raw or "per-week" in raw:
        return _seconds_until_next_utc_week(now)
    if "monthly" in raw or "per month" in raw or "per-month" in raw:
        return _seconds_until_next_utc_month(now)
    if (
        "daily" in raw
        or "per day" in raw
        or "per-day" in raw
        or "free-models-per-day" in raw
    ):
        return _seconds_until_next_utc_day(now)
    return default_seconds


def _is_model_not_found_error(exc: Exception) -> bool:
    raw = str(exc).lower()
    status = _status_code(exc)
    return (
        "model_not_found" in raw
        or "does not exist or you do not have access" in raw
        or (status == 404 and "model" in raw and "not found" in raw)
    )


def _is_structured_output_error(exc: Exception) -> bool:
    raw = str(exc).lower()
    return any(
        term in raw
        for term in (
            "validation",
            "failed to parse",
            "instructor",
            "max retries",
            "json",
            "response_format",
            "schema",
        )
    )


def _is_rate_limit_error(exc: Exception) -> bool:
    raw = str(exc).lower()
    return (
        isinstance(exc, RateLimitError)
        or _status_code(exc) == 429
        or "rate limit" in raw
        or "rate_limit" in raw
        or "429" in raw
    )


def _is_quota_exhaustion(exc: Exception) -> bool:
    raw = str(exc).lower()
    if "free-models-per-min" in raw or "per minute" in raw or "per-min" in raw:
        return False
    return any(
        term in raw
        for term in (
            "free-models-per-day",
            "per day",
            "per-day",
            "daily",
            "weekly",
            "monthly",
            "quota",
            "limit_remaining",
            "limit remaining",
        )
    )


def sanitize_llm_error(exc: Exception) -> str:
    """
    Convert provider exceptions into short UI-safe messages.

    Raw API errors may contain org IDs, billing URLs, keys, and JSON blobs that
    should never reach the dashboard.
    """
    raw = str(exc).lower()
    status = _status_code(exc)
    if status == 401 or "invalid api key" in raw or "invalid credentials" in raw:
        return "AI provider credentials are invalid - update the provider API key."
    if status == 402 or "insufficient credits" in raw or "negative credit" in raw:
        return "AI provider credits are exhausted - add credits or switch provider."
    if _is_rate_limit_error(exc):
        if _is_quota_exhaustion(exc):
            return "AI model quota exhausted - the router will use another configured model or resume after reset."
        return "AI model temporarily rate-limited - the router will retry shortly."
    if _is_model_not_found_error(exc):
        return "AI model is unavailable - the router will try another configured model."
    if "all ai model" in raw or "all configured" in raw or "all available" in raw:
        return "All AI model tiers are temporarily unavailable - the system will retry on the next signal."
    if "timeout" in raw or "timed out" in raw:
        return "AI model request timed out - the system will retry on the next signal."
    if "connection" in raw or "network" in raw:
        return "Network error reaching AI model - the system will retry on the next signal."
    if _is_structured_output_error(exc):
        return "AI response failed structured validation - the router will try another configured model."
    return "AI analysis temporarily unavailable - the system will retry on the next signal."


def _maybe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
