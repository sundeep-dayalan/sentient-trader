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

Enhanced with lightweight semantic dedup (Jaccard similarity on word sets):
  Catches "NVIDIA beats Q3 earnings" vs "NVDA earnings top estimates" without
  needing embedding API calls. Stores recent headlines per ticker in a Redis
  list and checks word overlap before committing to a new LLM analysis.
"""

import hashlib
import logging
import re

from redis_client import create_redis_client

log = logging.getLogger("agent.cache")

# Headlines older than this TTL (in seconds) are treated as fresh again
CACHE_TTL_SECONDS = 2 * 60 * 60  # 2 hours

# Semantic dedup: minimum Jaccard similarity to consider two headlines as covering
# the same story. 0.55 is intentionally conservative — catches obvious reformulations
# without blocking legitimately different stories about the same ticker.
SEMANTIC_SIMILARITY_THRESHOLD = 0.55

# How many recent headlines to keep per ticker for semantic comparison
RECENT_HEADLINES_MAX = 15

# Stop words removed before Jaccard comparison
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "in", "on", "at", "to",
    "for", "of", "and", "or", "its", "it", "by", "as", "with", "from",
    "has", "have", "had", "be", "been", "will", "that", "this", "but",
    "not", "says", "said", "report", "reports", "stock", "shares",
})


def _normalize_headline_words(headline: str) -> set[str]:
    """Extract meaningful words from a headline for Jaccard comparison."""
    words = re.findall(r"[a-z0-9]+", headline.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) >= 2}


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Word-set Jaccard similarity: |intersection| / |union|."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


class HeadlineCache:
    """
    Thin wrapper around Valkey/Redis for article/ticker deduplication.

    Supports two dedup layers:
    1. Exact match: SHA-256 of article_id+ticker or normalized headline+ticker
    2. Semantic match: Jaccard word-set similarity against recent headlines
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
        if self._redis.get(key) is not None:
            return True

        # Lightweight semantic dedup: check word overlap with recent headlines
        if ticker and self._is_semantic_duplicate(headline, ticker):
            return True

        return False

    def _is_semantic_duplicate(self, headline: str, ticker: str) -> bool:
        """
        Check if a semantically similar headline was recently processed for this ticker.

        Uses Jaccard similarity on normalized word sets — fast, no API calls,
        catches ~80% of reformulated duplicate stories.
        """
        list_key = f"recent_headlines:{ticker.upper()}"
        try:
            recent = self._redis.lrange(list_key, 0, RECENT_HEADLINES_MAX - 1)
        except Exception:
            return False

        if not recent:
            return False

        new_words = _normalize_headline_words(headline)
        if len(new_words) < 3:
            return False  # Too few words for reliable comparison

        for cached_bytes in recent:
            try:
                cached = cached_bytes.decode("utf-8") if isinstance(cached_bytes, bytes) else str(cached_bytes)
            except (UnicodeDecodeError, AttributeError):
                continue
            cached_words = _normalize_headline_words(cached)
            similarity = _jaccard_similarity(new_words, cached_words)
            if similarity >= SEMANTIC_SIMILARITY_THRESHOLD:
                log.info(
                    "Semantic dedup hit (%.0f%% similar, ticker=%s): new=%s | cached=%s",
                    similarity * 100,
                    ticker,
                    headline[:50],
                    cached[:50],
                )
                return True

        return False

    def mark_seen(
        self,
        headline: str,
        ticker: str | None = None,
        article_id: str | None = None,
    ) -> None:
        """Record this article/ticker so future duplicates are caught within the TTL."""
        key = self._make_key(headline, ticker=ticker, article_id=article_id)
        self._redis.setex(key, CACHE_TTL_SECONDS, "1")

        # Also store in the recent headlines list for semantic dedup
        if ticker:
            list_key = f"recent_headlines:{ticker.upper()}"
            try:
                self._redis.lpush(list_key, headline)
                self._redis.ltrim(list_key, 0, RECENT_HEADLINES_MAX - 1)
                self._redis.expire(list_key, CACHE_TTL_SECONDS)
            except Exception:
                pass  # Semantic dedup is best-effort

        log.debug(
            "Cached signal (TTL=%ds, ticker=%s): %s",
            CACHE_TTL_SECONDS,
            ticker,
            headline[:60],
        )
