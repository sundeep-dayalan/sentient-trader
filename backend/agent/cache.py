"""
Semantic Cache (Valkey / Redis)
================================
Prevents duplicate LLM calls for the same article/ticker pair.

The problem it solves:
  News aggregators re-syndicate the same story across multiple sources.
  Without a cache, "NVIDIA beats earnings" from Reuters, Bloomberg, and
  CNBC would trigger three separate LLM provider calls — same information,
  triple the cost and latency.

How it works:
  1. Prefer article_id + ticker when available
  2. Fall back to normalized headline + ticker
  3. SHA-256 hash that scope -> 64-char hex digest
  3. Check Redis with that digest as the key
  4. If found → duplicate, skip this article/ticker decision
  5. If not found → new story, proceed with analysis
  6. After analysis, mark the key as seen for 2 hours

TTL of 2 hours:
  - Long enough to catch duplicate syndication and same-session replays
  - Short enough that a genuine follow-up story on the same topic is analyzed
"""

import hashlib
import logging

from redis_client import create_redis_client

log = logging.getLogger("agent.cache")

# Headlines older than this TTL (in seconds) are treated as fresh again
CACHE_TTL_SECONDS = 2 * 60 * 60  # 2 hours


class HeadlineCache:
    """
    Thin wrapper around Valkey/Redis for article/ticker deduplication.
    """

    def __init__(self) -> None:
        self._redis = create_redis_client()
        log.info("Redis cache connected")

    def _make_key(
        self,
        headline: str,
        ticker: str | None = None,
        article_id: str | None = None,
    ) -> str:
        """
        SHA-256 hash of the article/ticker scope.

        Including ticker is important: a valid multi-ticker headline should let
        NVDA, AMD, and AVGO each receive their own decision instead of making
        the second and third tickers look like duplicate headline cache hits.
        """
        normalized = headline.lower().strip()
        normalized_ticker = (ticker or "").upper().strip()
        normalized_article_id = (article_id or "").strip()
        if normalized_article_id and normalized_ticker:
            scope = f"article:{normalized_article_id}:{normalized_ticker}"
        else:
            scope = f"headline:{normalized}:{normalized_ticker}"
        digest = hashlib.sha256(scope.encode()).hexdigest()
        return f"signal:{digest}"

    def is_duplicate(
        self,
        headline: str,
        ticker: str | None = None,
        article_id: str | None = None,
    ) -> bool:
        """Returns True if we've already processed this article/ticker recently."""
        key = self._make_key(headline, ticker=ticker, article_id=article_id)
        return self._redis.get(key) is not None

    def mark_seen(
        self,
        headline: str,
        ticker: str | None = None,
        article_id: str | None = None,
    ) -> None:
        """Record this article/ticker so future duplicates are caught within the TTL."""
        key = self._make_key(headline, ticker=ticker, article_id=article_id)
        self._redis.setex(key, CACHE_TTL_SECONDS, "1")
        log.debug(
            "Cached signal (TTL=%ds, ticker=%s): %s",
            CACHE_TTL_SECONDS,
            ticker,
            headline[:60],
        )
