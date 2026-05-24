"""
Redis Stream Consumer
======================
Reads news messages from the Valkey/Redis Stream and feeds them to
the AI agent graph one message at a time.

Redis Streams with consumer groups provide durable queue semantics:
  - Messages persist in the stream regardless of consumer uptime
  - Consumer groups track the read position across restarts
  - Each message is delivered to exactly one consumer in the group
  - Un-ACKed messages are redeliverable if the consumer crashes (at-least-once)

How the polling loop works:
  1. xreadgroup(">" = new messages only) pulls up to config.BATCH_SIZE entries
  2. For each entry: parse flat-list fields → NewsMessage → call callback → xack
  3. If the stream is empty: sleep 1 second and poll again
  4. On any error: log, sleep 5 seconds, retry

Field format from redis-py:
  xreadgroup returns: [["stream-key", [["entry-id", {"f1":"v1", ...}], ...]]]
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Callable, Mapping

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError, TimeoutError

sys.path.append(str(Path(__file__).resolve().parents[1]))

import config
from redis_client import create_redis_client
from schemas import NewsMessage
from shared.worker_health import write_worker_state

log = logging.getLogger("agent.consumer")
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
REDIS_DB = os.environ.get("REDIS_DB", "0")


class RedisStreamConsumer:
    """
    Persistent Redis Stream consumer with auto-group creation and ACK logic.
    Call .start(callback) to begin consuming — this blocks forever.
    """

    def __init__(self) -> None:
        self._redis = create_redis_client()
        self._last_state_phase: str | None = None
        self._last_state_detail: str | None = None
        self._write_agent_state("starting", "initializing Redis stream consumer")
        self._ensure_consumer_group()

    def _write_agent_state(self, phase: str, detail: str | None = None) -> None:
        now = int(time.time())
        if detail is not None:
            self._last_state_detail = detail
        try:
            self._redis.set("agent:heartbeat", str(now))
            if phase != self._last_state_phase or detail:
                self._redis.set(
                    "agent:state",
                    json.dumps(
                        {
                            "phase": phase,
                            "detail": detail,
                            "updated_at": now,
                        }
                    ),
                )
                self._last_state_phase = phase
            write_worker_state(
                self._redis,
                "agent",
                {
                    "status": "healthy" if phase == "polling" else "degraded",
                    "phase": phase,
                    "detail": detail if detail is not None else self._last_state_detail,
                    "updated_at": now,
                },
            )
        except Exception as exc:
            log.warning("Could not write agent heartbeat/state: %s", exc)

    def _sleep_with_heartbeat(
        self, seconds: float, phase: str, detail: str | None = None
    ) -> None:
        deadline = time.time() + seconds
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            self._write_agent_state(phase, detail)
            time.sleep(min(30, remaining))

    def _ensure_consumer_group(self) -> None:
        """
        Create the consumer group if it doesn't already exist.
        "$" means only deliver messages added AFTER this call — we don't want
        to replay old news on restart. mkstream creates the stream if absent.
        """
        while True:
            try:
                self._redis.xgroup_create(
                    config.STREAM_KEY, config.CONSUMER_GROUP, "$", mkstream=True
                )
                log.info(
                    "Consumer group '%s' created on stream '%s'",
                    config.CONSUMER_GROUP,
                    config.STREAM_KEY,
                )
                return
            except ResponseError as e:
                if "BUSYGROUP" in str(e):
                    log.info(
                        "Consumer group '%s' already exists — resuming",
                        config.CONSUMER_GROUP,
                    )
                    return
                raise
            except (RedisConnectionError, TimeoutError) as e:
                log.error(
                    "Redis unavailable at %s:%s DB %s: %s — retrying in %.0fs",
                    REDIS_HOST,
                    REDIS_PORT,
                    REDIS_DB,
                    e,
                    config.ERROR_RETRY,
                )
                self._sleep_with_heartbeat(
                    config.ERROR_RETRY,
                    "redis_backoff",
                    f"Redis unavailable: {str(e)[:160]}",
                )

    def start(self, on_message: Callable[[NewsMessage], None]) -> None:
        """
        Poll the Redis stream forever.
        on_message: called with a parsed NewsMessage for each new entry.
        """
        log.info(
            "Redis stream consumer ready (stream=%s, group=%s)",
            config.STREAM_KEY,
            config.CONSUMER_GROUP,
        )
        self._write_agent_state("polling", "Redis stream consumer is polling for news")

        last_heartbeat = 0.0

        while True:
            try:
                now = time.time()
                if now - last_heartbeat > 10:
                    self._write_agent_state("polling")
                    last_heartbeat = now

                # ">" = give me messages not yet delivered to any consumer in this group
                results = self._redis.xreadgroup(
                    config.CONSUMER_GROUP,
                    config.CONSUMER_NAME,
                    {config.STREAM_KEY: ">"},
                    count=config.BATCH_SIZE,
                )

                if not results:
                    time.sleep(config.POLL_INTERVAL)
                    continue

                # results format: [["stream-key", [["entry-id", {"f":"v", ...}], ...]]]
                _stream_name, entries = results[0]
                for entry_id, fields in entries:
                    self._process_entry(entry_id, fields, on_message)

            except KeyboardInterrupt:
                log.info("Consumer shutting down...")
                break
            except Exception as e:
                retry = config.ERROR_RETRY
                log.error("Stream poll error: %s — retrying in %.0fs", e, retry)
                self._sleep_with_heartbeat(
                    retry,
                    "stream_backoff",
                    str(e)[:200],
                )

    def _process_entry(
        self,
        entry_id: str,
        raw_fields: Mapping[str, str] | list[str],
        on_message: Callable[[NewsMessage], None],
    ) -> None:
        """
        Parse one stream entry and call the processing callback.

        redis-py returns fields as a dict when decode_responses=True.
        The flat-list branch keeps compatibility with older stream fixtures.

        We ACK after processing so that a mid-crash leaves the message
        in the Pending Entries List for redelivery.
        """
        try:
            fields = (
                dict(raw_fields)
                if isinstance(raw_fields, Mapping)
                else dict(zip(raw_fields[::2], raw_fields[1::2]))
            )
            news = NewsMessage(**fields)
            log.info("Consumed [%s]: %s", news.ticker, news.headline[:70])
            on_message(news)
        except (TypeError, ValueError) as e:
            log.warning("Skipping malformed entry %s: %s", entry_id, e)
        finally:
            # ACK even on parse failures so bad messages don't block the group
            try:
                self._redis.xack(config.STREAM_KEY, config.CONSUMER_GROUP, entry_id)
            except Exception as e:
                log.error("Failed to ACK entry %s: %s", entry_id, e)
