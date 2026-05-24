"""
REST backfill for Alpaca news gaps.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any, Callable

import httpx

import config
from models import iso_z, parse_datetime, utc_now

log = logging.getLogger("ingestion.backfill")


class AlpacaNewsBackfiller:
    """
    Uses Alpaca's REST news endpoint to recover articles missed while the
    WebSocket was disconnected or the ingestion service was restarting.
    """

    def __init__(self, api_key: str, secret_key: str) -> None:
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }

    def run(
        self,
        start: datetime,
        end: datetime | None,
        on_article: Callable[[dict[str, Any]], None],
    ) -> int:
        end = end or utc_now()
        page_token: str | None = None
        processed = 0

        with httpx.Client(timeout=config.BACKFILL_HTTP_TIMEOUT_SECONDS) as client:
            for page in range(config.BACKFILL_MAX_PAGES):
                params: dict[str, Any] = {
                    "start": iso_z(start),
                    "end": iso_z(end),
                    "sort": "asc",
                    "limit": min(50, config.BACKFILL_PAGE_LIMIT),
                    "include_content": "true",
                }
                if page_token:
                    params["page_token"] = page_token

                response = client.get(
                    f"{config.ALPACA_NEWS_BASE_URL}/v1beta1/news",
                    headers=self._headers,
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
                articles = payload.get("news") or payload.get("articles") or []
                if not articles:
                    break

                for article in articles:
                    on_article(article)
                    processed += 1

                page_token = (
                    payload.get("next_page_token")
                    or payload.get("next_page")
                    or payload.get("next")
                )
                if not page_token:
                    break
            else:
                log.warning(
                    "Backfill stopped after max page limit (%d); range may need replay",
                    config.BACKFILL_MAX_PAGES,
                )

        log.info(
            "Backfill processed %d articles for %s to %s",
            processed,
            iso_z(start),
            iso_z(end),
        )
        return processed


def start_from_cursor(
    cursor: dict[str, Any] | None,
    lookback_seconds: int,
) -> datetime:
    cursor_value = (cursor or {}).get("last_article_created_at")
    cursor_dt = parse_datetime(cursor_value)
    if cursor_dt is None:
        cursor_dt = utc_now()
    return cursor_dt - timedelta(seconds=lookback_seconds)
