"""
Semantic Cache (Upstash Redis)
===============================
Prevents duplicate LLM calls for the same headline.

The problem it solves:
  News aggregators re-syndicate the same story across multiple sources.
  Without a cache, "NVIDIA beats earnings" from Reuters, Bloomberg, and
  CNBC would trigger three separate Groq API calls — same information,
  triple the cost and latency.

How it works:
  1. Normalize the headline (lowercase + strip whitespace)
  2. SHA-256 hash the normalized text → 64-char hex digest
  3. Check Redis with that digest as the key
  4. If found → duplicate, skip this headline
  5. If not found → new story, proceed with analysis
  6. After analysis, mark the key as seen for 5 minutes

TTL of 5 minutes:
  - Long enough to catch duplicate syndication (usually within 2 minutes)
  - Short enough that a genuine follow-up story on the same topic is analyzed
"""

import hashlib
import logging
import os

from upstash_redis import Redis

log = logging.getLogger("agent.cache")

# Headlines older than this TTL (in seconds) are treated as fresh again
CACHE_TTL_SECONDS = 5 * 60  # 5 minutes


class HeadlineCache:
    """
    Thin wrapper around Upstash Redis for headline deduplication.
    Uses the upstash-redis HTTP client — no persistent TCP connection needed.
    """

    def __init__(self) -> None:
        self._redis = Redis(
            url=os.environ["UPSTASH_REDIS_URL"],
            token=os.environ["UPSTASH_REDIS_TOKEN"],
        )
        log.info("Redis cache connected")

    def _make_key(self, headline: str) -> str:
        """
        SHA-256 hash of the normalized headline text.
        Normalization catches minor formatting differences in the same
        story published by different wire services.
        """
        normalized = headline.lower().strip()
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        return f"headline:{digest}"

    def is_duplicate(self, headline: str) -> bool:
        """Returns True if we've already processed this headline recently."""
        key = self._make_key(headline)
        return self._redis.get(key) is not None

    def mark_seen(self, headline: str) -> None:
        """Record this headline so future duplicates are caught within the TTL window."""
        key = self._make_key(headline)
        self._redis.setex(key, CACHE_TTL_SECONDS, "1")
        log.debug("Cached headline (TTL=%ds): %s", CACHE_TTL_SECONDS, headline[:60])
