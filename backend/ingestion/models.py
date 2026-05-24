"""
Canonical article model and normalization helpers for ingestion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re

import config


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ref",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def normalize_headline(headline: str) -> str:
    normalized = headline.lower().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[\s\.,;:!?\-]+$", "", normalized)
    return normalized


def hash_text(value: str | None) -> str | None:
    if not value:
        return None
    return sha256(value.encode("utf-8")).hexdigest()


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip() or None
    if not parts.scheme or not parts.netloc:
        return url.strip() or None

    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in TRACKING_QUERY_KEYS:
            continue
        if any(key_lower.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        query.append((key, value))

    path = parts.path.rstrip("/") or parts.path
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urlencode(query, doseq=True),
            "",
        )
    )


def _get(article: Any, name: str, default: Any = None) -> Any:
    if isinstance(article, dict):
        return article.get(name, default)
    return getattr(article, name, default)


def _raw_payload(article: Any) -> dict[str, Any]:
    if isinstance(article, dict):
        return dict(article)
    if hasattr(article, "model_dump"):
        return article.model_dump(mode="json")
    if hasattr(article, "dict"):
        return article.dict()
    payload: dict[str, Any] = {}
    for name in (
        "id",
        "headline",
        "summary",
        "content",
        "author",
        "created_at",
        "updated_at",
        "url",
        "symbols",
        "source",
    ):
        value = getattr(article, name, None)
        if isinstance(value, datetime):
            value = iso_z(value)
        if value is not None:
            payload[name] = value
    return payload


def _symbols(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        candidates = [piece.strip() for piece in value.split(",")]
    else:
        candidates = [str(piece).strip() for piece in value]
    seen: set[str] = set()
    symbols: list[str] = []
    for candidate in candidates:
        symbol = candidate.upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


@dataclass(frozen=True)
class NormalizedArticle:
    provider: str
    source_article_id: str | None
    article_source: str
    headline: str
    normalized_headline: str
    headline_hash: str
    summary: str | None
    content: str | None
    author: str | None
    article_url: str | None
    normalized_url: str | None
    url_hash: str | None
    symbols: list[str]
    source_created_at: datetime
    source_updated_at: datetime | None
    received_at: datetime = field(default_factory=utc_now)
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def redis_message(self, ticker: str, fallback_article_id: str | None = None) -> dict[str, str]:
        message = {
            "ticker": ticker,
            "headline": self.headline,
            "source": self.article_source or self.provider,
            "published_at": iso_z(self.source_created_at) or iso_z(self.received_at) or "",
        }
        if self.summary:
            message["summary"] = self.summary
        if self.article_url:
            message["article_url"] = self.article_url
        article_id = self.source_article_id or fallback_article_id
        if article_id:
            message["article_id"] = str(article_id)
        return message


def normalize_article(article: Any, provider: str = config.PROVIDER) -> NormalizedArticle | None:
    headline = str(_get(article, "headline", "") or "").strip()
    created_at = parse_datetime(_get(article, "created_at"))
    if not headline or created_at is None:
        return None

    article_id = _get(article, "id")
    source_article_id = str(article_id) if article_id not in (None, "") else None
    article_url = str(_get(article, "url", "") or "").strip() or None
    normalized_url = normalize_url(article_url)
    normalized_headline = normalize_headline(headline)

    return NormalizedArticle(
        provider=provider,
        source_article_id=source_article_id,
        article_source=str(_get(article, "source", "") or "unknown"),
        headline=headline,
        normalized_headline=normalized_headline,
        headline_hash=hash_text(normalized_headline) or "",
        summary=str(_get(article, "summary", "") or "").strip() or None,
        content=str(_get(article, "content", "") or "").strip() or None,
        author=str(_get(article, "author", "") or "").strip() or None,
        article_url=article_url,
        normalized_url=normalized_url,
        url_hash=hash_text(normalized_url),
        symbols=_symbols(_get(article, "symbols")),
        source_created_at=created_at,
        source_updated_at=parse_datetime(_get(article, "updated_at")),
        raw_payload=_raw_payload(article),
    )
