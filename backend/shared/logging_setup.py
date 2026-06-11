"""
Shared logging setup for all backend services.

Two output modes, selected with LOG_FORMAT:
  - "text" (default): the existing human-readable single-line format, unchanged
    for local development.
  - "json": one JSON object per line for log aggregators (Coolify/Loki/Grafana).

Every record carries the correlation IDs stored in the contextvars below when
they are set:
  - request_id: set per HTTP request by the API middleware.
  - signal_id:  set per consumed stream entry by the agent consumer, so one
    news signal can be traced ingestion → debate → order across log lines.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from typing import Any, Optional

request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)
signal_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "signal_id", default=None
)
signal_ticker_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "signal_ticker", default=None
)

TEXT_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
TEXT_DATEFMT = "%Y-%m-%dT%H:%M:%S"


class CorrelationFilter(logging.Filter):
    """Stamp correlation IDs onto every record so any formatter can use them."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.signal_id = signal_id_var.get()
        record.signal_ticker = signal_ticker_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__(datefmt=TEXT_DATEFMT)
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("request_id", "signal_id", "signal_ticker"):
            value = getattr(record, field, None)
            if value:
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """The existing text format plus correlation IDs when present."""

    def __init__(self) -> None:
        super().__init__(fmt=TEXT_FORMAT, datefmt=TEXT_DATEFMT)

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        tags = []
        if getattr(record, "request_id", None):
            tags.append(f"req={record.request_id}")
        if getattr(record, "signal_id", None):
            tags.append(f"signal={record.signal_id}")
        return f"{base}  [{' '.join(tags)}]" if tags else base


def setup_logging(service: str) -> None:
    """
    Configure root logging for a service. Replaces logging.basicConfig.

    LOG_FORMAT=json switches to structured output; LOG_LEVEL overrides INFO.
    Safe to call once at process start, before any other imports log.
    """
    log_format = os.environ.get("LOG_FORMAT", "text").strip().lower()
    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.addFilter(CorrelationFilter())
    if log_format == "json":
        handler.setFormatter(JsonFormatter(service))
    else:
        handler.setFormatter(TextFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
