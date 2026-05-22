"""
Alpaca News Streamer
===================
Connects to Alpaca's real-time WebSockets to stream news articles and
hands off each new article to the filter → producer pipeline.
"""

import logging
import os
import time
import threading

from alpaca.data.live import NewsDataStream

from filter import is_relevant
from producer import RedisStreamProducer

log = logging.getLogger("ingestion.listener")

class NewsListener:
    """
    Streams Alpaca's news via WebSockets and feeds articles into the Redis stream.
    """

    def __init__(self) -> None:
        api_key = os.environ["ALPACA_API_KEY"]
        secret_key = os.environ["ALPACA_SECRET_KEY"]

        self._stream = NewsDataStream(api_key, secret_key)
        self._producer = RedisStreamProducer()

        log.info("News streamer initialized.")

    async def _news_handler(self, article) -> None:
        """
        Callback for each real-time news article.
        """
        pub_time = getattr(article, "created_at", None)
        if pub_time is None:
            return

        symbols = getattr(article, "symbols", None) or []
        if not symbols:
            return

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
                break  # one message per article even if multiple tickers match

    def _heartbeat_loop(self) -> None:
        """
        Runs in a background thread to update the Redis heartbeat every 30 seconds.
        This ensures health checks pass even while the WebSocket stream is blocking.
        """
        while True:
            try:
                self._producer._redis.set("ingestion:heartbeat", str(int(time.time())))
            except Exception as e:
                log.warning("Could not write ingestion heartbeat: %s", e)
            time.sleep(30)

    def run(self) -> None:
        """Starts the background heartbeat and the blocking WebSocket stream."""
        heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        log.info("Started background heartbeat thread.")

        log.info("Starting real-time news stream...")
        self._stream.subscribe_news(self._news_handler, "*")
        try:
            self._stream.run()
        except KeyboardInterrupt:
            log.info("Shutting down news streamer.")
        except Exception as e:
            log.error("News stream encountered an error: %s", e)
            raise
