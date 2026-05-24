"""
Redis Stream Producer
======================
Publishes filtered news articles to a Valkey/Redis Stream.

Redis Streams provide the same semantics we need:
  - Persistent, ordered message log per topic (stream key)
  - Consumer groups: multiple consumers can share the load
  - At-least-once delivery with ACK support
  - Auto-ID generation with XADD (millisecond + sequence counter)

The stream acts as a durable buffer between the ingestion service
and the AI agent — if the agent goes down, messages stay in the stream
and are processed when it comes back up.

Message fields stored in each stream entry:
  ticker, headline, source, published_at, article_url, article_id
"""

import logging
import os
from typing import Mapping

from redis_client import create_redis_client

log = logging.getLogger("ingestion.producer")

# The Redis stream key that the agent consumer reads from.
STREAM_KEY = os.environ.get("REDIS_STREAM_KEY", "market-news")

# Limit the stream to the 1,000 most recent messages so it doesn't
# grow unbounded on the free tier (MAXLEN with ~ for approximate trimming)
STREAM_MAX_LEN = int(os.environ.get("REDIS_STREAM_MAX_LEN", "1000"))


class RedisStreamProducer:
    """
    Publishes news articles to a Redis Stream.
    One producer instance lives for the lifetime of the ingestion process.
    """

    def __init__(self) -> None:
        self._redis = create_redis_client()
        log.info("Redis stream producer connected (stream key: %s)", STREAM_KEY)

    @property
    def redis(self):
        return self._redis

    def publish(
        self,
        ticker: str,
        headline: str,
        source: str,
        published_at: str,
        summary: str | None = None,
        article_url: str | None = None,
        article_id: str | None = None,
    ) -> None:
        """
        Append one news article to the Redis Stream.
        Never raises — errors are logged so the listener keeps running.

        XADD with maxlen=STREAM_MAX_LEN keeps storage bounded.
        The "*" ID lets Redis auto-generate a timestamp-based message ID.
        """
        message = {
            "ticker": ticker,
            "headline": headline,
            "source": source,
            "published_at": published_at,
        }
        if summary:
            message["summary"] = summary
        if article_url:
            message["article_url"] = article_url
        if article_id:
            message["article_id"] = article_id

        try:
            entry_id = self.publish_message(message)
            log.info("Published [%s] → %s: %s", entry_id, ticker, headline[:70])

        except Exception as e:
            # Don't crash the listener — log and continue to the next headline
            log.error("Failed to publish to Redis stream: %s", e)

    def publish_message(self, message: Mapping[str, str]) -> str:
        """
        Append a prebuilt message to Redis and return the stream entry id.

        This method intentionally raises on failure so the durable outbox can
        mark the attempt and retry later.
        """
        entry_id = self._redis.xadd(
            STREAM_KEY,
            dict(message),
            id="*",
            maxlen=STREAM_MAX_LEN,
            approximate=True,
        )
        return str(entry_id)
