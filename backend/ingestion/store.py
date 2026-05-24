"""
Supabase-backed durable article store, dedupe gate, and Redis outbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import os
from typing import Any

from postgrest.exceptions import APIError
from supabase import Client, create_client
from supabase.client import ClientOptions

import config
from models import NormalizedArticle, iso_z, utc_now

log = logging.getLogger("ingestion.store")


@dataclass
class IngestResult:
    article_id: str | None
    stored: bool
    duplicate: bool
    dedupe_reason: str | None
    outbox_rows: list[dict[str, Any]]


class IngestionStore:
    """
    Durable ingestion state.

    Articles are written here before they are published to Redis. Dedupe only
    suppresses outbox creation; audit rows/events remain available in Postgres.
    """

    def __init__(self) -> None:
        self._client: Client = create_client(
            supabase_url=os.environ["SUPABASE_URL"],
            supabase_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
            options=ClientOptions(
                schema=os.environ.get("SUPABASE_DB_SCHEMA", "public"),
            ),
        )
        log.info("Supabase ingestion store connected")

    def _table(self, name: str):
        return self._client.table(name)

    def get_cursor(self, provider: str = config.PROVIDER) -> dict[str, Any] | None:
        result = (
            self._table("ingestion_cursors")
            .select("*")
            .eq("provider", provider)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    def mark_backfill_completed(self, provider: str = config.PROVIDER) -> None:
        self._table("ingestion_cursors").upsert(
            {
                "provider": provider,
                "updated_at": iso_z(utc_now()),
                "last_backfill_completed_at": iso_z(utc_now()),
            },
            on_conflict="provider",
        ).execute()

    def update_cursor(self, article: NormalizedArticle, origin: str) -> None:
        payload = {
            "provider": article.provider,
            "updated_at": iso_z(utc_now()),
            "last_article_created_at": iso_z(article.source_created_at),
            "last_source_article_id": article.source_article_id,
        }
        if origin == "websocket":
            payload["last_websocket_article_at"] = iso_z(article.source_created_at)
        self._table("ingestion_cursors").upsert(
            payload,
            on_conflict="provider",
        ).execute()

    def record_event(
        self,
        event_type: str,
        article_id: str | None = None,
        outbox_id: str | None = None,
        ticker: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        try:
            self._table("ingestion_events").insert(
                {
                    "event_type": event_type,
                    "article_id": article_id,
                    "outbox_id": outbox_id,
                    "ticker": ticker,
                    "detail": detail or {},
                },
                returning="minimal",
            ).execute()
        except Exception as exc:
            log.warning("Could not write ingestion event %s: %s", event_type, exc)

    def ingest_article(
        self,
        article: NormalizedArticle,
        relevant_tickers: list[str],
        origin: str,
    ) -> IngestResult:
        self.update_cursor(article, origin)

        canonical_id, dedupe_reason = self._find_canonical(article)
        if dedupe_reason == "exact_source_article_id" and canonical_id:
            self.record_event(
                "ARTICLE_DEDUPED",
                article_id=canonical_id,
                detail={
                    "reason": dedupe_reason,
                    "source_article_id": article.source_article_id,
                    "origin": origin,
                },
            )
            return IngestResult(
                article_id=canonical_id,
                stored=False,
                duplicate=True,
                dedupe_reason=dedupe_reason,
                outbox_rows=[],
            )

        row = self._insert_article(article, canonical_id, dedupe_reason)
        article_id = row["id"]
        self.record_event(
            "ARTICLE_STORED",
            article_id=article_id,
            detail={
                "origin": origin,
                "duplicate": bool(dedupe_reason),
                "dedupe_reason": dedupe_reason,
            },
        )

        self._insert_article_symbols(article_id, article.symbols)

        if dedupe_reason:
            self.record_event(
                "ARTICLE_DEDUPED",
                article_id=article_id,
                detail={
                    "reason": dedupe_reason,
                    "canonical_article_id": canonical_id,
                    "origin": origin,
                },
            )
            return IngestResult(
                article_id=article_id,
                stored=True,
                duplicate=True,
                dedupe_reason=dedupe_reason,
                outbox_rows=[],
            )

        outbox_rows = [
            self._ensure_outbox(article, article_id, ticker)
            for ticker in self._unique_tickers(relevant_tickers)
        ]
        return IngestResult(
            article_id=article_id,
            stored=True,
            duplicate=False,
            dedupe_reason=None,
            outbox_rows=[row for row in outbox_rows if row is not None],
        )

    def _find_canonical(self, article: NormalizedArticle) -> tuple[str | None, str | None]:
        if article.source_article_id:
            existing = self._select_one(
                "raw_news_articles",
                "id, canonical_article_id",
                {
                    "provider": article.provider,
                    "source_article_id": article.source_article_id,
                },
            )
            if existing:
                return existing.get("canonical_article_id") or existing["id"], "exact_source_article_id"

        if article.url_hash:
            existing = self._select_one(
                "raw_news_articles",
                "id, canonical_article_id",
                {
                    "url_hash": article.url_hash,
                    "is_duplicate": False,
                },
            )
            if existing:
                return existing.get("canonical_article_id") or existing["id"], "same_url"

        if article.symbols:
            window_start = article.source_created_at - timedelta(
                seconds=config.DEDUPE_HEADLINE_WINDOW_SECONDS,
            )
            window_end = article.source_created_at + timedelta(
                seconds=config.DEDUPE_HEADLINE_WINDOW_SECONDS,
            )
            result = (
                self._table("raw_news_articles")
                .select("id, canonical_article_id, symbols")
                .eq("headline_hash", article.headline_hash)
                .eq("is_duplicate", False)
                .gte("source_created_at", iso_z(window_start))
                .lte("source_created_at", iso_z(window_end))
                .order("source_created_at", desc=True)
                .limit(20)
                .execute()
            )
            article_symbols = set(article.symbols)
            for candidate in result.data or []:
                candidate_symbols = set(candidate.get("symbols") or [])
                if article_symbols.intersection(candidate_symbols):
                    return (
                        candidate.get("canonical_article_id") or candidate["id"],
                        "same_headline_ticker_window",
                    )

        return None, None

    def _insert_article(
        self,
        article: NormalizedArticle,
        canonical_id: str | None,
        dedupe_reason: str | None,
    ) -> dict[str, Any]:
        payload = {
            "provider": article.provider,
            "source_article_id": article.source_article_id,
            "article_source": article.article_source,
            "headline": article.headline,
            "normalized_headline": article.normalized_headline,
            "headline_hash": article.headline_hash,
            "summary": article.summary,
            "content": article.content,
            "author": article.author,
            "article_url": article.article_url,
            "url_hash": article.url_hash,
            "symbols": article.symbols,
            "source_created_at": iso_z(article.source_created_at),
            "source_updated_at": iso_z(article.source_updated_at),
            "received_at": iso_z(article.received_at),
            "raw_payload": article.raw_payload,
            "canonical_article_id": canonical_id,
            "dedupe_reason": dedupe_reason,
            "is_duplicate": dedupe_reason is not None,
        }
        try:
            result = (
                self._table("raw_news_articles")
                .insert(payload)
                .execute()
            )
        except APIError as exc:
            if "duplicate key" not in str(exc).lower() or not article.source_article_id:
                raise
            existing = self._select_one(
                "raw_news_articles",
                "id, canonical_article_id",
                {
                    "provider": article.provider,
                    "source_article_id": article.source_article_id,
                },
            )
            if existing:
                return existing
            raise
        return result.data[0]

    def _insert_article_symbols(self, article_id: str, symbols: list[str]) -> None:
        rows = [
            {"article_id": article_id, "ticker": ticker}
            for ticker in self._unique_tickers(symbols)
        ]
        if not rows:
            return
        try:
            self._table("news_article_symbols").upsert(
                rows,
                on_conflict="article_id,ticker",
                returning="minimal",
            ).execute()
        except Exception as exc:
            log.warning("Could not write article symbols for %s: %s", article_id, exc)

    def _ensure_outbox(
        self,
        article: NormalizedArticle,
        article_id: str,
        ticker: str,
    ) -> dict[str, Any] | None:
        existing = self._select_one(
            "news_outbox",
            "id, article_id, ticker, status, attempts, message_payload",
            {
                "article_id": article_id,
                "ticker": ticker,
            },
        )
        if existing:
            self.record_event(
                "ARTICLE_DEDUPED",
                article_id=article_id,
                outbox_id=existing["id"],
                ticker=ticker,
                detail={"reason": "same_article_ticker"},
            )
            return None

        payload = {
            "article_id": article_id,
            "ticker": ticker,
            "message_payload": article.redis_message(ticker, fallback_article_id=article_id),
        }
        result = self._table("news_outbox").insert(payload).execute()
        row = result.data[0]
        self.record_event(
            "OUTBOX_CREATED",
            article_id=article_id,
            outbox_id=row["id"],
            ticker=ticker,
        )
        return row

    def pending_outbox(self, limit: int = config.OUTBOX_BATCH_SIZE) -> list[dict[str, Any]]:
        result = (
            self._table("news_outbox")
            .select("id, article_id, ticker, status, attempts, message_payload")
            .in_("status", ["PENDING", "RETRYING", "FAILED"])
            .lte("next_attempt_at", iso_z(utc_now()))
            .order("created_at")
            .limit(limit)
            .execute()
        )
        return result.data or []

    def pending_outbox_count(self) -> int:
        result = (
            self._table("news_outbox")
            .select("id", count="exact")
            .in_("status", ["PENDING", "RETRYING", "FAILED"])
            .execute()
        )
        return int(result.count or 0)

    def mark_outbox_published(self, row: dict[str, Any], redis_entry_id: str) -> None:
        now = iso_z(utc_now())
        self._table("news_outbox").update(
            {
                "status": "PUBLISHED",
                "redis_entry_id": redis_entry_id,
                "published_at": now,
                "updated_at": now,
                "last_error": None,
            },
            returning="minimal",
        ).eq("id", row["id"]).execute()
        self.record_event(
            "ARTICLE_PUBLISHED",
            article_id=row.get("article_id"),
            outbox_id=row["id"],
            ticker=row.get("ticker"),
            detail={"redis_entry_id": redis_entry_id},
        )

    def mark_outbox_failed(self, row: dict[str, Any], error: Exception) -> None:
        attempts = int(row.get("attempts") or 0) + 1
        delay = min(
            config.OUTBOX_MAX_RETRY_DELAY_SECONDS,
            max(5, 2 ** min(attempts, 8)),
        )
        now = utc_now()
        self._table("news_outbox").update(
            {
                "status": "RETRYING",
                "attempts": attempts,
                "last_error": str(error)[:1000],
                "updated_at": iso_z(now),
                "next_attempt_at": iso_z(now + timedelta(seconds=delay)),
            },
            returning="minimal",
        ).eq("id", row["id"]).execute()
        self.record_event(
            "ARTICLE_PUBLISH_FAILED",
            article_id=row.get("article_id"),
            outbox_id=row["id"],
            ticker=row.get("ticker"),
            detail={"attempts": attempts, "error": str(error)[:500]},
        )

    def _select_one(
        self,
        table: str,
        columns: str,
        filters: dict[str, Any],
    ) -> dict[str, Any] | None:
        query = self._table(table).select(columns)
        for key, value in filters.items():
            query = query.eq(key, value)
        result = query.limit(1).execute()
        return result.data[0] if result.data else None

    def _unique_tickers(self, tickers: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for ticker in tickers:
            normalized = ticker.strip().upper()
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique.append(normalized)
        return unique
