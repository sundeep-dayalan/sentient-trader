"""
Redis Stream Consumer
======================
Reads news messages from the Upstash Redis Stream and feeds them to
the AI agent graph one message at a time.

Redis Streams with consumer groups provide the same semantics as Kafka:
  - Messages persist in the stream regardless of consumer uptime
  - Consumer groups track the read position across restarts
  - Each message is delivered to exactly one consumer in the group
  - Un-ACKed messages are redeliverable if the consumer crashes (at-least-once)

How the polling loop works:
  1. xreadgroup(">" = new messages only) pulls up to config.BATCH_SIZE entries
  2. For each entry: parse flat-list fields → NewsMessage → call callback → xack
  3. If the stream is empty: sleep 1 second and poll again
  4. On any error: log, sleep 5 seconds, retry

Field format from upstash-redis 1.7.0:
  xreadgroup returns: [["stream-key", [["entry-id", ["f1","v1","f2","v2",...]], ...]]]
  Fields are a flat alternating list, not a dict — we convert with zip(even, odd).
"""

import logging
import os
import time
from typing import Callable

from upstash_redis import Redis

import config
from schemas import NewsMessage

log = logging.getLogger("agent.consumer")


def _is_upstash_quota_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "max requests limit exceeded" in message or (
        "usage:" in message and "limit:" in message
    )


class RedisStreamConsumer:
    """
    Persistent Redis Stream consumer with auto-group creation and ACK logic.
    Call .start(callback) to begin consuming — this blocks forever.
    """

    def __init__(self) -> None:
        self._redis = Redis(
            url=os.environ["UPSTASH_REDIS_URL"],
            token=os.environ["UPSTASH_REDIS_TOKEN"],
        )
        self._ensure_consumer_group()

    def _ensure_consumer_group(self) -> None:
        """
        Create the consumer group if it doesn't already exist.
        "$" means only deliver messages added AFTER this call — we don't want
        to replay old news on restart. mkstream creates the stream if absent.
        """
        while True:
            try:
                self._redis.xgroup_create(config.STREAM_KEY, config.CONSUMER_GROUP, "$", mkstream=True)
                log.info("Consumer group '%s' created on stream '%s'", config.CONSUMER_GROUP, config.STREAM_KEY)
                return
            except Exception as e:
                if "BUSYGROUP" in str(e):
                    log.info("Consumer group '%s' already exists — resuming", config.CONSUMER_GROUP)
                    return
                if _is_upstash_quota_error(e):
                    log.error(
                        "Upstash Redis request quota exhausted while ensuring consumer group — "
                        "sleeping %.0fs before retry",
                        config.REDIS_QUOTA_RETRY,
                    )
                    time.sleep(config.REDIS_QUOTA_RETRY)
                    continue
                raise

    def start(self, on_message: Callable[[NewsMessage], None]) -> None:
        """
        Poll the Redis stream forever.
        on_message: called with a parsed NewsMessage for each new entry.
        """
        log.info("Redis stream consumer ready (stream=%s, group=%s)", config.STREAM_KEY, config.CONSUMER_GROUP)

        last_heartbeat = 0.0

        while True:
            try:
                now = time.time()
                if now - last_heartbeat > 10:
                    self._redis.set("agent:heartbeat", str(int(now)))
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

                # results format: [["stream-key", [["entry-id", ["f","v",...]], ...]]]
                _stream_name, entries = results[0]
                for entry_id, flat_fields in entries:
                    self._process_entry(entry_id, flat_fields, on_message)

            except KeyboardInterrupt:
                log.info("Consumer shutting down...")
                break
            except Exception as e:
                retry = config.REDIS_QUOTA_RETRY if _is_upstash_quota_error(e) else config.ERROR_RETRY
                log.error("Stream poll error: %s — retrying in %.0fs", e, retry)
                time.sleep(retry)

    def _process_entry(
        self,
        entry_id: str,
        flat_fields: list,
        on_message: Callable[[NewsMessage], None],
    ) -> None:
        """
        Parse one stream entry and call the processing callback.

        upstash-redis returns fields as a flat alternating list:
          ["ticker", "NVDA", "headline", "...", "source", "...", ...]
        We convert to a dict with zip(even_indices, odd_indices).

        We ACK after processing so that a mid-crash leaves the message
        in the Pending Entries List for redelivery.
        """
        try:
            # Convert flat list ["k1","v1","k2","v2",...] → {"k1":"v1","k2":"v2",...}
            fields = dict(zip(flat_fields[::2], flat_fields[1::2]))
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
