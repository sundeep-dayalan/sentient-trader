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
  2. For each entry: parse fields → NewsMessage → freshness gate → callback → xack
  3. If processing fails: schedule an independent retry or dead-letter, then xack
  4. If the stream is empty: reclaim idle pending entries and process due retries
  5. On any infrastructure error: log, heartbeat, sleep, retry

Field format from redis-py:
  xreadgroup returns: [["stream-key", [["entry-id", {"f1":"v1", ...}], ...]]]
"""

import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError, TimeoutError

sys.path.append(str(Path(__file__).resolve().parents[1]))

import config
from health import AgentHealth
from redis_client import create_redis_client
from schemas import NewsMessage
from shared.logging_setup import signal_id_var, signal_ticker_var

log = logging.getLogger("agent.consumer")

# US-style ticker symbols only. Alpaca's stock APIs reject exchange-prefixed
# symbols (TSX:BMO, LSE:BARC, etc.); processing them wastes LLM quota and
# trips the outcome labeler. Match the regex used by the outcome labeler so
# whatever passes here is guaranteed labelable downstream.
_US_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,9}$")
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
        self._health = AgentHealth(self._redis)
        self._last_state_phase: str | None = None
        self._last_state_detail: str | None = None
        self._pending_claim_cursor = "0-0"
        self._stop = threading.Event()
        self._write_agent_state("starting", "initializing Redis stream consumer")
        self._ensure_consumer_group()

    def request_stop(self) -> None:
        """Ask the polling loop to exit after the in-flight entry resolves.

        Called from a SIGTERM/SIGINT handler. The current entry finishes its
        full process-and-ACK cycle, so a rolling deploy never abandons a
        half-processed message to redelivery.
        """
        self._stop.set()

    def _write_agent_state(self, phase: str, detail: str | None = None) -> None:
        if detail is not None:
            self._last_state_detail = detail
        status = "healthy" if phase == "polling" else "degraded"
        self._health.write(
            status=status,
            phase=phase,
            detail=detail if detail is not None else self._last_state_detail,
        )
        self._last_state_phase = phase

    def _sleep_with_heartbeat(
        self, seconds: float, phase: str, detail: str | None = None
    ) -> None:
        deadline = time.time() + seconds
        while not self._stop.is_set():
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            self._write_agent_state(phase, detail)
            # Event.wait instead of time.sleep so a shutdown request wakes us.
            self._stop.wait(min(30, remaining))

    def _ensure_consumer_group(self) -> None:
        """
        Create the consumer group if it doesn't already exist.
        REDIS_CONSUMER_START_ID controls first boot behavior. "0" catches up
        from retained stream history; "$" starts from only new messages. The
        freshness gate prevents stale retained messages from becoming trades.
        mkstream creates the stream if absent.
        """
        while True:
            try:
                self._redis.xgroup_create(
                    config.STREAM_KEY,
                    config.CONSUMER_GROUP,
                    config.CONSUMER_START_ID,
                    mkstream=True,
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

    def start(
        self,
        on_message: Callable[[NewsMessage], None],
        on_expired: Callable[[NewsMessage, dict[str, Any]], None],
    ) -> None:
        """
        Poll the Redis stream forever.
        on_message: called with a parsed NewsMessage for each new entry.
        """
        log.info(
            "Redis stream consumer ready (stream=%s, group=%s)",
            config.STREAM_KEY,
            config.CONSUMER_GROUP,
        )
        self._health.mark_polling()

        last_heartbeat = 0.0

        while not self._stop.is_set():
            try:
                now = time.time()
                if now - last_heartbeat > 10:
                    self._health.mark_polling()
                    last_heartbeat = now

                # ">" = give me messages not yet delivered to any consumer in this group
                results = self._redis.xreadgroup(
                    config.CONSUMER_GROUP,
                    config.CONSUMER_NAME,
                    {config.STREAM_KEY: ">"},
                    count=config.BATCH_SIZE,
                )

                if not results:
                    self._process_pending_claims(on_message, on_expired)
                    self._process_due_retries(on_message, on_expired)
                    self._stop.wait(config.POLL_INTERVAL)
                    continue

                # results format: [["stream-key", [["entry-id", {"f":"v", ...}], ...]]]
                _stream_name, entries = results[0]
                for entry_id, fields in entries:
                    resolved = self._process_entry(
                        entry_id,
                        fields,
                        on_message,
                        on_expired,
                        source="stream",
                    )
                    if resolved:
                        self._ack(entry_id)
                    if self._stop.is_set():
                        break

            except KeyboardInterrupt:
                log.info("Consumer shutting down...")
                break
            except ResponseError as e:
                if "NOGROUP" in str(e):
                    log.warning(
                        "Consumer group missing while polling stream — recreating: %s",
                        e,
                    )
                    self._write_agent_state(
                        "stream_backoff",
                        f"Consumer group missing; recreating: {str(e)[:160]}",
                    )
                    self._ensure_consumer_group()
                    continue
                raise
            except Exception as e:
                retry = config.ERROR_RETRY
                log.error("Stream poll error: %s — retrying in %.0fs", e, retry)
                self._sleep_with_heartbeat(
                    retry,
                    "stream_backoff",
                    str(e)[:200],
                )

        log.info("Consumer stopped cleanly after draining the in-flight entry")
        self._write_agent_state("stopped", "graceful shutdown complete")

    def _process_entry(
        self,
        entry_id: str,
        raw_fields: Mapping[str, str] | list[str],
        on_message: Callable[[NewsMessage], None],
        on_expired: Callable[[NewsMessage, dict[str, Any]], None],
        *,
        source: str,
        attempts: int = 0,
    ) -> bool:
        """
        Parse one stream entry and call the processing callback.

        redis-py returns fields as a dict when decode_responses=True.
        The flat-list branch keeps compatibility with older stream fixtures.

        We ACK after processing so that a mid-crash leaves the message
        in the Pending Entries List for redelivery.
        """
        fields = (
            dict(raw_fields)
            if isinstance(raw_fields, Mapping)
            else dict(zip(raw_fields[::2], raw_fields[1::2]))
        )
        fields = {str(key): str(value) for key, value in fields.items()}

        # Correlation: every log line emitted while this entry is processed —
        # debate, gates, order, Supabase write — carries the entry id + ticker.
        signal_token = signal_id_var.set(entry_id)
        ticker_token = signal_ticker_var.set(fields.get("ticker") or None)
        try:
            return self._process_entry_inner(
                entry_id, fields, on_message, on_expired, source=source, attempts=attempts
            )
        finally:
            signal_id_var.reset(signal_token)
            signal_ticker_var.reset(ticker_token)

    def _process_entry_inner(
        self,
        entry_id: str,
        fields: dict[str, str],
        on_message: Callable[[NewsMessage], None],
        on_expired: Callable[[NewsMessage, dict[str, Any]], None],
        *,
        source: str,
        attempts: int = 0,
    ) -> bool:
        try:
            news = NewsMessage(**fields)
            log.info("Consumed [%s]: %s", news.ticker, news.headline[:70])
            self._health.mark_processing(entry_id, news.ticker, source)

            # ── Ticker support gate ──────────────────────────────────────────
            # Reject exchange-prefixed/non-US tickers (TSX:BMO, LSE:BARC, etc.)
            # before they reach the LLM pipeline. Alpaca can't trade them and
            # the outcome labeler can't price them — every cent spent on LLM
            # debate is wasted. Dead-letter the entry with an explicit reason.
            ticker_raw = (news.ticker or "").strip().upper()
            if not ticker_raw or not _US_TICKER_PATTERN.fullmatch(ticker_raw):
                log.info(
                    "Rejecting unsupported ticker format: %r (entry=%s)",
                    news.ticker, entry_id,
                )
                self._write_dead_letter(
                    entry_id=entry_id,
                    fields=fields,
                    reason="unsupported_ticker_format",
                    attempts=attempts,
                    error=f"ticker {news.ticker!r} is not a tradable US symbol",
                )
                self._health.mark_dead_lettered(entry_id, "unsupported ticker format")
                return True

            age = self._signal_age_seconds(news)
            if age is not None and age > config.AGENT_MAX_AUDIT_SIGNAL_AGE_SECONDS:
                self._write_dead_letter(
                    entry_id=entry_id,
                    fields=fields,
                    reason="expired_beyond_audit_window",
                    attempts=attempts,
                    error=f"signal age {int(age)}s exceeded audit window",
                )
                self._health.mark_dead_lettered(entry_id, "expired beyond audit window")
                return True

            if age is not None and age > config.AGENT_MAX_TRADE_SIGNAL_AGE_SECONDS:
                on_expired(
                    news,
                    {
                        "entry_id": entry_id,
                        "source": source,
                        "attempts": attempts,
                        "age_seconds": age,
                        "max_trade_age_seconds": config.AGENT_MAX_TRADE_SIGNAL_AGE_SECONDS,
                    },
                )
                self._health.mark_expired(entry_id, age)
                return True

            on_message(news)
            self._health.mark_processed(entry_id)
            return True

        except (TypeError, ValueError) as exc:
            log.warning("Malformed entry %s: %s", entry_id, exc)
            self._write_dead_letter(
                entry_id=entry_id,
                fields=fields,
                reason="malformed_message",
                attempts=attempts,
                error=str(exc),
            )
            self._health.mark_dead_lettered(entry_id, "malformed message")
            return True

        except Exception as exc:
            log.error("Processing failed for entry %s: %s", entry_id, exc)
            self._health.mark_error(entry_id, exc)
            return self._route_failed_entry(entry_id, fields, attempts, exc)

    def _process_due_retries(
        self,
        on_message: Callable[[NewsMessage], None],
        on_expired: Callable[[NewsMessage, dict[str, Any]], None],
    ) -> None:
        retry_ids = self._redis.zrangebyscore(
            config.AGENT_RETRY_ZSET_KEY,
            min=0,
            max=time.time(),
            start=0,
            num=config.AGENT_RETRY_BATCH_SIZE,
        )
        for retry_id in retry_ids:
            payload_raw = self._redis.hget(config.AGENT_RETRY_HASH_KEY, retry_id)
            if not payload_raw:
                self._redis.zrem(config.AGENT_RETRY_ZSET_KEY, retry_id)
                continue
            payload = json.loads(payload_raw)
            resolved = self._process_entry(
                payload["entry_id"],
                payload["fields"],
                on_message,
                on_expired,
                source="retry",
                attempts=int(payload.get("attempts") or 0),
            )
            if resolved:
                self._redis.zrem(config.AGENT_RETRY_ZSET_KEY, retry_id)
                self._redis.hdel(config.AGENT_RETRY_HASH_KEY, retry_id)

    def _process_pending_claims(
        self,
        on_message: Callable[[NewsMessage], None],
        on_expired: Callable[[NewsMessage, dict[str, Any]], None],
    ) -> None:
        """
        Reclaim messages left pending by a crashed/stalled consumer.

        Redis does not automatically re-run entries sitting in the Pending
        Entries List. XAUTOCLAIM lets this worker take over entries that have
        been idle long enough and resolve them with the same freshness, retry,
        DLQ, and ACK rules as fresh stream entries.
        """
        result = self._redis.xautoclaim(
            config.STREAM_KEY,
            config.CONSUMER_GROUP,
            config.CONSUMER_NAME,
            min_idle_time=config.AGENT_PENDING_IDLE_SECONDS * 1000,
            start_id=self._pending_claim_cursor,
            count=config.AGENT_PENDING_BATCH_SIZE,
        )
        if not result:
            return

        next_cursor = str(result[0] or "0-0")
        entries = result[1] if len(result) > 1 else []
        self._pending_claim_cursor = next_cursor if next_cursor != "0-0" else "0-0"

        for entry_id, fields in entries:
            resolved = self._process_entry(
                entry_id,
                fields,
                on_message,
                on_expired,
                source="pending",
            )
            if resolved:
                self._ack(entry_id)

    def _route_failed_entry(
        self,
        entry_id: str,
        fields: dict[str, str],
        attempts: int,
        error: Exception,
    ) -> bool:
        next_attempt = attempts + 1
        if next_attempt >= config.AGENT_MAX_PROCESSING_ATTEMPTS:
            self._write_dead_letter(
                entry_id=entry_id,
                fields=fields,
                reason="max_attempts_exceeded",
                attempts=next_attempt,
                error=str(error),
            )
            self._health.mark_dead_lettered(entry_id, "max attempts exceeded")
            return True

        delay = min(
            config.AGENT_RETRY_MAX_DELAY_SECONDS,
            config.AGENT_RETRY_BASE_DELAY_SECONDS * (2 ** max(0, attempts)),
        )
        retry_payload = {
            "entry_id": entry_id,
            "fields": fields,
            "attempts": next_attempt,
            "last_error": str(error)[:1000],
            "scheduled_at": self._now_iso(),
        }
        self._redis.hset(
            config.AGENT_RETRY_HASH_KEY,
            entry_id,
            json.dumps(retry_payload),
        )
        self._redis.zadd(
            config.AGENT_RETRY_ZSET_KEY,
            {entry_id: time.time() + delay},
        )
        self._health.mark_retry_scheduled(entry_id, next_attempt, delay)
        return True

    def _write_dead_letter(
        self,
        *,
        entry_id: str,
        fields: dict[str, str],
        reason: str,
        attempts: int,
        error: str,
    ) -> None:
        self._redis.xadd(
            config.AGENT_DLQ_STREAM_KEY,
            {
                "original_entry_id": entry_id,
                "reason": reason,
                "attempts": str(attempts),
                "error": error[:1000],
                "payload": json.dumps(fields),
                "dead_lettered_at": self._now_iso(),
            },
            id="*",
        )

    def _ack(self, entry_id: str) -> None:
        try:
            self._redis.xack(config.STREAM_KEY, config.CONSUMER_GROUP, entry_id)
        except Exception as exc:
            log.error("Failed to ACK entry %s: %s", entry_id, exc)

    def _signal_age_seconds(self, news: NewsMessage) -> float | None:
        published = self._parse_datetime(news.published_at)
        if published is None:
            return None
        return max(0.0, (datetime.now(timezone.utc) - published).total_seconds())

    def _parse_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
