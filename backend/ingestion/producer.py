"""
Redis Stream Producer
======================
Publishes filtered news articles to an Upstash Redis Stream.

We replaced Kafka with Redis Streams because Upstash no longer offers
a Kafka product. Redis Streams provide the same semantics we need:
  - Persistent, ordered message log per topic (stream key)
  - Consumer groups: multiple consumers can share the load
  - At-least-once delivery with ACK support
  - Auto-ID generation with XADD (millisecond + sequence counter)

The stream acts as a durable buffer between the ingestion service
and the AI agent — if the agent goes down, messages stay in the stream
and are processed when it comes back up.

Message fields stored in each stream entry:
  ticker, headline, source, published_at
"""

import json
import logging
import os

from upstash_redis import Redis

log = logging.getLogger("ingestion.producer")

# The Redis stream key that the agent consumer reads from.
# This replaces the Kafka topic name.
STREAM_KEY = os.environ.get("REDIS_STREAM_KEY", "market-news")

# Limit the stream to the 1,000 most recent messages so it doesn't
# grow unbounded on the free tier (MAXLEN with ~ for approximate trimming)
STREAM_MAX_LEN = 1000


class RedisStreamProducer:
    """
    Publishes news articles to a Redis Stream via the Upstash REST API.
    One producer instance lives for the lifetime of the ingestion process.
    """

    def __init__(self) -> None:
        self._redis = Redis(
            url=os.environ["UPSTASH_REDIS_URL"],
            token=os.environ["UPSTASH_REDIS_TOKEN"],
        )
        log.info("Redis stream producer connected (stream key: %s)", STREAM_KEY)

    def publish(
        self,
        ticker: str,
        headline: str,
        source: str,
        published_at: str,
    ) -> None:
        """
        Append one news article to the Redis Stream.
        Never raises — errors are logged so the listener keeps running.

        XADD with maxlen=STREAM_MAX_LEN keeps storage bounded on the free tier.
        The "*" ID lets Redis auto-generate a timestamp-based message ID.
        """
        message = {
            "ticker":       ticker,
            "headline":     headline,
            "source":       source,
            "published_at": published_at,
        }

        try:
            entry_id = self._redis.xadd(
                STREAM_KEY,
                "*",        # auto-generate a timestamp-based ID
                message,
                maxlen=STREAM_MAX_LEN,
            )
            log.info("Published [%s] → %s: %s", entry_id, ticker, headline[:70])

        except Exception as e:
            # Don't crash the listener — log and continue to the next headline
            log.error("Failed to publish to Redis stream: %s", e)
