"""
Redis-backed agent health state.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from redis import Redis

from shared.worker_health import write_worker_state

log = logging.getLogger("agent.health")


class AgentHealth:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._state: dict[str, Any] = {
            "worker": "agent",
            "status": "starting",
            "phase": "starting",
            "detail": "agent worker booting",
            "messages_consumed": 0,
            "messages_processed": 0,
            "messages_expired": 0,
            "messages_retried": 0,
            "messages_dead_lettered": 0,
            "processing_errors": 0,
        }
        self._last_phase: str | None = None
        self._last_detail: str | None = None

    def write(self, **updates: Any) -> None:
        self._state.update({key: value for key, value in updates.items() if value is not None})
        if updates.get("detail") is not None:
            self._last_detail = str(updates["detail"])
        if updates.get("phase") is not None:
            self._last_phase = str(updates["phase"])

        now = int(time.time())
        self._state["updated_at"] = now
        try:
            write_worker_state(self._redis, "agent", self._state)
            self._redis.set("agent:heartbeat", str(now))
            self._redis.set(
                "agent:state",
                json.dumps(
                    {
                        "phase": self._state.get("phase"),
                        "detail": self._state.get("detail"),
                        "updated_at": now,
                    }
                ),
            )
        except Exception as exc:
            log.warning("Could not write agent health state: %s", exc)

    def mark_polling(self) -> None:
        self.write(
            status="healthy",
            phase="polling",
            detail="Redis stream consumer is polling for news",
            current_entry_id=None,
        )

    def mark_processing(self, entry_id: str, ticker: str, source: str) -> None:
        self._state["messages_consumed"] = int(self._state.get("messages_consumed") or 0) + 1
        self.write(
            status="healthy",
            phase="processing",
            detail=f"Processing {ticker} from {source}",
            current_entry_id=entry_id,
            current_ticker=ticker,
            current_source=source,
        )

    def mark_processed(self, entry_id: str) -> None:
        self._state["messages_processed"] = int(self._state.get("messages_processed") or 0) + 1
        self.write(
            status="healthy",
            phase="polling",
            detail="Message processed and resolved",
            current_entry_id=entry_id,
        )

    def mark_expired(self, entry_id: str, age_seconds: float) -> None:
        self._state["messages_expired"] = int(self._state.get("messages_expired") or 0) + 1
        self.write(
            status="healthy",
            phase="expired",
            detail=f"Expired stale signal after {int(age_seconds)}s",
            current_entry_id=entry_id,
        )

    def mark_retry_scheduled(self, entry_id: str, attempts: int, delay: int) -> None:
        self._state["messages_retried"] = int(self._state.get("messages_retried") or 0) + 1
        self.write(
            status="degraded",
            phase="retry_scheduled",
            detail=f"Scheduled retry {attempts} in {delay}s",
            current_entry_id=entry_id,
        )

    def mark_dead_lettered(self, entry_id: str, reason: str) -> None:
        self._state["messages_dead_lettered"] = int(self._state.get("messages_dead_lettered") or 0) + 1
        self.write(
            status="degraded",
            phase="dead_lettered",
            detail=reason,
            current_entry_id=entry_id,
        )

    def mark_error(self, entry_id: str, error: Exception | str) -> None:
        self._state["processing_errors"] = int(self._state.get("processing_errors") or 0) + 1
        self.write(
            status="degraded",
            phase="processing_error",
            detail=str(error)[:300],
            current_entry_id=entry_id,
        )
