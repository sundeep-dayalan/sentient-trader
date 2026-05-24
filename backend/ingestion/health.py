"""
Redis-backed ingestion health state.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from redis import Redis

import config
from models import iso_z, utc_now

log = logging.getLogger("ingestion.health")


class IngestionHealth:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._key = config.WORKER_HEALTH_KEY
        self._worker_name = config.WORKER_NAME
        self._state: dict[str, Any] = {
            "worker": self._worker_name,
            "status": "starting",
            "phase": "starting",
            "detail": "ingestion service booting",
            "stream_connected": False,
            "websocket_reconnects": 0,
            "publish_failures": 0,
            "articles_seen": 0,
            "articles_published": 0,
            "backfill_runs": 0,
            "pending_outbox_count": None,
        }

    def write(self, **updates: Any) -> None:
        self._state.update({key: value for key, value in updates.items() if value is not None})
        now = utc_now()
        self._state["last_heartbeat_at"] = iso_z(now)
        self._state["last_heartbeat_epoch"] = int(time.time())
        self._state["updated_at"] = iso_z(now)
        try:
            self._redis.hset(
                self._key,
                self._worker_name,
                json.dumps(self._state, default=str),
            )
        except Exception as exc:
            log.warning("Could not write ingestion health state: %s", exc)

    def mark_stream_connected(self) -> None:
        self.write(
            status="healthy",
            phase="streaming",
            detail="Alpaca websocket connected",
            stream_connected=True,
            last_stream_connected_at=iso_z(utc_now()),
        )

    def mark_stream_disconnected(self, error: Exception | str) -> None:
        self._state["websocket_reconnects"] = int(self._state.get("websocket_reconnects") or 0) + 1
        self.write(
            status="degraded",
            phase="stream_reconnect_backoff",
            detail=str(error)[:300],
            stream_connected=False,
            last_stream_error_at=iso_z(utc_now()),
        )

    def mark_article_seen(self) -> None:
        self._state["articles_seen"] = int(self._state.get("articles_seen") or 0) + 1
        self.write(
            last_article_seen_at=iso_z(utc_now()),
        )

    def mark_published(self) -> None:
        self._state["articles_published"] = int(self._state.get("articles_published") or 0) + 1
        self.write(
            status="healthy" if self._state.get("stream_connected") else "degraded",
            last_publish_success_at=iso_z(utc_now()),
        )

    def mark_publish_failed(self, error: Exception | str) -> None:
        self._state["publish_failures"] = int(self._state.get("publish_failures") or 0) + 1
        self.write(
            status="degraded",
            phase="outbox_retrying",
            detail=str(error)[:300],
            last_publish_error_at=iso_z(utc_now()),
        )

    def mark_backfill_started(self, reason: str) -> None:
        self.write(
            status="degraded",
            phase="backfilling",
            detail=reason,
            last_backfill_started_at=iso_z(utc_now()),
        )

    def mark_backfill_completed(self, count: int) -> None:
        self._state["backfill_runs"] = int(self._state.get("backfill_runs") or 0) + 1
        self.write(
            status="healthy" if self._state.get("stream_connected") else "degraded",
            phase="streaming" if self._state.get("stream_connected") else "backfill_complete",
            detail=f"Backfill completed with {count} articles",
            last_backfill_success_at=iso_z(utc_now()),
        )

    def mark_unhealthy(self, error: Exception | str) -> None:
        self.write(
            status="unhealthy",
            phase="error",
            detail=str(error)[:300],
            last_error_at=iso_z(utc_now()),
        )
