"""
Alpaca News Poller
===================
Polls Alpaca's REST news API every 30 seconds and hands off each new
article to the filter → producer pipeline.

Why polling instead of WebSocket?
  alpaca-py 0.26.0 ships StockDataStream and CryptoDataStream but does
  NOT include a NewsDataStream. The REST approach is equally real-time
  for our purposes — news articles rarely need sub-second delivery, and
  30-second polling keeps us within Alpaca's free-tier rate limits.

Each poll fetches articles published since the last seen timestamp so
we never process the same article twice.
"""

import logging
import os
import time
from datetime import datetime, timezone, timedelta

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

from filter import is_relevant
from producer import RedisStreamProducer

log = logging.getLogger("ingestion.listener")

POLL_INTERVAL_SECONDS = 30
# Fetch up to 50 articles per poll — more than enough for 30-second windows
FETCH_LIMIT = 50


class NewsListener:
    """
    Polls Alpaca's news REST API and feeds articles into the Redis stream.

    On each poll:
      - Articles older than the last-seen timestamp are skipped
      - Articles with no ticker symbols are dropped (nothing to trade on)
      - Remaining articles pass through the relevance filter
      - Articles that pass are published to the Redis stream
    """

    def __init__(self) -> None:
        api_key = os.environ["ALPACA_API_KEY"]
        secret_key = os.environ["ALPACA_SECRET_KEY"]

        self._client = NewsClient(api_key=api_key, secret_key=secret_key)
        self._producer = RedisStreamProducer()

        # Start looking from "now" so we only process articles published
        # after the service starts — not old backfill data
        self._last_seen = datetime.now(timezone.utc) - timedelta(minutes=1)

        log.info("News poller initialized (interval: %ds)", POLL_INTERVAL_SECONDS)

    def _poll_once(self) -> None:
        """Fetch and process all articles published since last poll."""
        try:
            news_set = self._client.get_news(
                NewsRequest(
                    start=self._last_seen,
                    sort="asc",      # oldest first so we advance the cursor in order
                    limit=FETCH_LIMIT,
                )
            )
        except Exception as e:
            log.error("News API request failed: %s", e)
            return

        articles = getattr(news_set, "news", []) or []
        if not articles:
            return

        new_articles = 0
        for article in articles:
            pub_time = article.created_at
            if pub_time is None:
                continue

            # Advance cursor to 1 second PAST the latest article.
            # Alpaca's `start` param is inclusive, so without this +1s the
            # boundary article would be re-fetched on every subsequent poll.
            if pub_time >= self._last_seen:
                self._last_seen = pub_time + timedelta(seconds=1)

            symbols = getattr(article, "symbols", None) or []
            if not symbols:
                continue

            for ticker in symbols:
                if is_relevant(article.headline, ticker):
                    self._producer.publish(
                        ticker=ticker,
                        headline=article.headline,
                        source=getattr(article, "source", None) or "unknown",
                        published_at=pub_time.isoformat(),
                        summary=getattr(article, "summary", None) or None,
                        article_url=getattr(article, "url", None),
                        article_id=str(getattr(article, "id", "")) or None,
                    )
                    new_articles += 1
                    break  # one message per article even if multiple tickers match

        if new_articles:
            log.info("Poll complete — published %d articles", new_articles)

    def run(self) -> None:
        """Polls forever, sleeping POLL_INTERVAL_SECONDS between each fetch."""
        log.info("Starting news poll loop (every %ds)...", POLL_INTERVAL_SECONDS)
        while True:
            self._poll_once()
            time.sleep(POLL_INTERVAL_SECONDS)
