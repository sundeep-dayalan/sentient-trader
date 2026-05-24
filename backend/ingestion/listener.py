"""
Alpaca News Streamer
===================
Connects to Alpaca's real-time WebSocket stream, backfills missed REST news,
stores raw articles durably, dedupes, and publishes Redis outbox entries for
the downstream agent.
"""

from __future__ import annotations

import logging
import os
import random
import threading
from typing import Any

from alpaca.data.live import NewsDataStream

import config
from backfill import AlpacaNewsBackfiller, start_from_cursor
from health import IngestionHealth
from models import normalize_article, utc_now
from producer import RedisStreamProducer
from store import IngestionStore
from ticker_directory import TickerDirectory

log = logging.getLogger("ingestion.listener")


class NewsListener:
    """
    Streams Alpaca news and feeds a durable store -> outbox -> Redis pipeline.
    """

    def __init__(self) -> None:
        self._api_key = os.environ["ALPACA_API_KEY"]
        self._secret_key = os.environ["ALPACA_SECRET_KEY"]

        self._producer = RedisStreamProducer()
        self._store = IngestionStore()
        self._backfiller = AlpacaNewsBackfiller(self._api_key, self._secret_key)
        self._health = IngestionHealth(self._producer.redis)
        self._ticker_directory = TickerDirectory(
            self._producer.redis,
            self._api_key,
            self._secret_key,
        )
        self._stop = threading.Event()

        self._ticker_directory.refresh_if_needed()
        self._health.write(ticker_directory_assets=self._ticker_directory.asset_count)
        log.info("News streamer initialized.")

    async def _news_handler(self, article) -> None:
        """Callback for each real-time news article."""
        self._handle_article(article, origin="websocket")

    def _handle_article(self, article: Any, origin: str) -> None:
        normalized = normalize_article(article)
        if normalized is None:
            log.warning("Skipping malformed article from %s", origin)
            return

        if not normalized.symbols:
            log.debug("Skipping article with no symbols: %s", normalized.headline[:70])
            return

        self._health.mark_article_seen()
        relevant_tickers = self._ticker_directory.select_relevant_tickers(normalized)

        try:
            result = self._store.ingest_article(
                normalized,
                relevant_tickers=relevant_tickers,
                origin=origin,
            )
        except Exception as exc:
            self._health.mark_unhealthy(exc)
            log.exception("Failed to durably store article from %s: %s", origin, exc)
            return

        if result.duplicate:
            log.info(
                "Deduped article (%s): %s",
                result.dedupe_reason,
                normalized.headline[:70],
            )
            return

        if not relevant_tickers:
            log.debug("Stored low-signal article without outbox: %s", normalized.headline[:70])
            return

        for row in result.outbox_rows:
            self._publish_outbox_row(row)

    def _publish_outbox_row(self, row: dict[str, Any]) -> None:
        try:
            payload = {
                str(key): str(value)
                for key, value in (row.get("message_payload") or {}).items()
                if value is not None
            }
            entry_id = self._producer.publish_message(payload)
            self._store.mark_outbox_published(row, entry_id)
            self._health.mark_published()
            log.info("Published outbox %s -> Redis %s", row["id"], entry_id)
        except Exception as exc:
            try:
                self._store.mark_outbox_failed(row, exc)
            except Exception as store_exc:
                log.error("Could not mark outbox failure for %s: %s", row.get("id"), store_exc)
            self._health.mark_publish_failed(exc)
            log.error("Failed to publish outbox %s: %s", row.get("id"), exc)

    def _heartbeat_loop(self) -> None:
        """Updates Redis health state while the WebSocket stream is blocking."""
        while not self._stop.is_set():
            try:
                pending = self._store.pending_outbox_count()
            except Exception as exc:
                pending = None
                log.warning("Could not read pending outbox count: %s", exc)
            self._health.write(pending_outbox_count=pending)
            self._stop.wait(config.HEARTBEAT_INTERVAL_SECONDS)

    def _outbox_retry_loop(self) -> None:
        """Retries Redis publishes that failed after durable storage."""
        while not self._stop.is_set():
            try:
                rows = self._store.pending_outbox(config.OUTBOX_BATCH_SIZE)
                for row in rows:
                    if self._stop.is_set():
                        break
                    self._publish_outbox_row(row)
            except Exception as exc:
                self._health.mark_publish_failed(exc)
                log.error("Outbox retry loop failed: %s", exc)
            self._stop.wait(config.OUTBOX_RETRY_INTERVAL_SECONDS)

    def _run_backfill(self, reason: str, lookback_seconds: int) -> None:
        """Fetch missed REST news using the durable cursor plus overlap."""
        try:
            cursor = self._store.get_cursor(config.PROVIDER)
            start = start_from_cursor(cursor, lookback_seconds)
            end = utc_now()
            self._health.mark_backfill_started(reason)
            count = self._backfiller.run(
                start=start,
                end=end,
                on_article=lambda raw: self._handle_article(raw, origin="backfill"),
            )
            self._store.mark_backfill_completed(config.PROVIDER)
            self._health.mark_backfill_completed(count)
        except Exception as exc:
            self._health.mark_unhealthy(exc)
            log.error("Backfill failed (%s): %s", reason, exc)

    def _new_stream(self) -> NewsDataStream:
        stream = NewsDataStream(self._api_key, self._secret_key)
        stream.subscribe_news(self._news_handler, "*")
        return stream

    def run(self) -> None:
        """Starts heartbeat, outbox retry, startup backfill, and WebSocket loop."""
        heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        log.info("Started background heartbeat thread.")

        outbox_thread = threading.Thread(target=self._outbox_retry_loop, daemon=True)
        outbox_thread.start()
        log.info("Started outbox retry thread.")

        self._run_backfill(
            reason="startup",
            lookback_seconds=config.BACKFILL_STARTUP_LOOKBACK_SECONDS,
        )

        reconnect_delay = 1.0
        while not self._stop.is_set():
            try:
                self._health.write(
                    status="degraded",
                    phase="stream_connecting",
                    detail="Connecting to Alpaca websocket",
                    stream_connected=False,
                )
                log.info("Starting real-time news stream...")
                stream = self._new_stream()
                self._health.mark_stream_connected()
                reconnect_delay = 1.0
                stream.run()
                raise RuntimeError("Alpaca news stream stopped unexpectedly")
            except KeyboardInterrupt:
                log.info("Shutting down news streamer.")
                self._stop.set()
                break
            except Exception as exc:
                self._health.mark_stream_disconnected(exc)
                log.error("News stream error: %s", exc)
                self._run_backfill(
                    reason="websocket_reconnect",
                    lookback_seconds=config.BACKFILL_RECONNECT_LOOKBACK_SECONDS,
                )
                sleep_for = min(
                    config.STREAM_RECONNECT_MAX_DELAY_SECONDS,
                    reconnect_delay + random.uniform(0, reconnect_delay),
                )
                log.info("Reconnecting news stream in %.1fs", sleep_for)
                self._stop.wait(sleep_for)
                reconnect_delay = min(
                    config.STREAM_RECONNECT_MAX_DELAY_SECONDS,
                    reconnect_delay * 2,
                )
