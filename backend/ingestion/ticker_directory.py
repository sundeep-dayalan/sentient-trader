"""
Redis-backed ticker directory for ingestion-time symbol relevance.

Alpaca news can attach every symbol mentioned anywhere in an article body. The
agent should not debate AMZN, MSFT, and NVDA just because a Marvell article
mentions them in paragraph three. This directory turns Alpaca asset metadata
into aliases and keeps only symbols that are actually named in the headline.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx
from redis import Redis

import config
from filter import BOILERPLATE_PHRASES, is_relevant
from models import NormalizedArticle, iso_z, utc_now

log = logging.getLogger("ingestion.ticker_directory")

TAG_RE = re.compile(r"<[^>]+>")
SPACES_RE = re.compile(r"\s+")

SECURITY_SUFFIXES = (
    "class a common stock",
    "class b common stock",
    "class c common stock",
    "class a capital stock",
    "class b capital stock",
    "class c capital stock",
    "common stock",
    "capital stock",
    "common shares",
    "ordinary shares",
    "american depositary shares",
    "american depository shares",
    "depositary shares",
    "depository shares",
    "sponsored adr",
    "unsponsored adr",
    "preferred stock",
    "preference shares",
    "warrants",
    "warrant",
    "rights",
    "right",
    "units",
    "unit",
    "adr",
    "ads",
)

CORPORATE_SUFFIXES = {
    "ag",
    "corp",
    "corporation",
    "co",
    "company",
    "inc",
    "incorporated",
    "limited",
    "ltd",
    "llc",
    "lp",
    "nv",
    "plc",
    "sa",
    "se",
}

UNSAFE_SINGLE_TOKEN_ALIASES = {
    "advanced",
    "american",
    "bank",
    "capital",
    "china",
    "digital",
    "energy",
    "financial",
    "first",
    "global",
    "group",
    "health",
    "holdings",
    "international",
    "national",
    "new",
    "partners",
    "systems",
    "technology",
    "technologies",
    "trust",
    "united",
}


def _normalize_text(value: str | None) -> str:
    text = html.unescape(value or "").lower()
    text = text.replace("&", " and ")
    text = TAG_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return SPACES_RE.sub(" ", text).strip()


def _contains_phrase(normalized_text: str, normalized_phrase: str) -> bool:
    if not normalized_text or not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {normalized_text} "


def _strip_security_suffix(name: str) -> str:
    cleaned = name
    changed = True
    while changed:
        changed = False
        for suffix in SECURITY_SUFFIXES:
            if cleaned == suffix:
                return ""
            if cleaned.endswith(f" {suffix}"):
                cleaned = cleaned[: -len(suffix)].strip()
                changed = True
                break
    return cleaned


def _strip_corporate_suffixes(name: str) -> str:
    tokens = name.split()
    while tokens and tokens[-1] in CORPORATE_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _coerce_aliases(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            decoded = [piece.strip() for piece in stripped.split(",")]
        return _coerce_aliases(decoded)
    if isinstance(value, (list, tuple, set)):
        aliases: list[str] = []
        for item in value:
            normalized = _normalize_text(str(item))
            if normalized:
                aliases.append(normalized)
        return aliases
    return []


def _dedupe_aliases(values: list[str]) -> list[str]:
    seen: set[str] = set()
    aliases: list[str] = []
    for value in values:
        normalized = _normalize_text(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            aliases.append(normalized)
    return aliases


class TickerDirectory:
    """
    Loads tradable US equity assets from Alpaca and stores alias metadata in Redis.
    """

    def __init__(self, redis: Redis, api_key: str, secret_key: str) -> None:
        self._redis = redis
        self._api_key = api_key
        self._secret_key = secret_key
        self._meta: dict[str, dict[str, Any]] = {}
        self._last_refresh_check_epoch = 0.0

    @property
    def asset_count(self) -> int:
        return len(self._meta)

    def refresh_if_needed(self, force: bool = False) -> None:
        """
        Refresh the in-memory directory from Redis or Alpaca.

        Failures are deliberately non-fatal. If Alpaca or Redis is unavailable,
        ingestion falls back to conservative single-symbol filtering.
        """
        now = time.time()
        if (
            not force
            and now - self._last_refresh_check_epoch
            < config.TICKER_DIRECTORY_REFRESH_CHECK_SECONDS
        ):
            return

        self._last_refresh_check_epoch = now
        if not force and self._load_fresh_from_redis(now):
            return

        try:
            assets = self._fetch_assets()
            overrides = self._load_alias_overrides()
            metadata = self._build_metadata(assets, overrides)
            self._store_metadata(metadata, override_count=sum(len(v) for v in overrides.values()))
            self._meta = metadata
            log.info("Ticker directory refreshed from Alpaca (%d assets)", len(metadata))
        except Exception as exc:
            if self._load_from_redis():
                log.warning("Using stale ticker directory after refresh failure: %s", exc)
                return
            self._meta = {}
            log.warning("Ticker directory unavailable; using conservative fallback: %s", exc)

    def select_relevant_tickers(self, article: NormalizedArticle) -> list[str]:
        """
        Return article symbols that are explicitly relevant to headline/summary.

        Headline matches are required for multi-symbol fanout. Body-only matches
        are intentionally below the default threshold because body mentions are
        often related-company context, not the traded subject.
        """
        self.refresh_if_needed()

        if self._is_boilerplate(article.headline):
            log.debug("Filtered boilerplate article: %s", article.headline[:70])
            return []

        symbols = [symbol.upper() for symbol in article.symbols]
        if not symbols:
            return []

        headline = _normalize_text(article.headline)
        summary = _normalize_text(article.summary)
        content = _normalize_text((article.content or "")[:1500])

        if not self._meta:
            return self._fallback_selection(article, headline, symbols)

        selected: list[str] = []
        scores: dict[str, int] = {}
        for symbol in symbols:
            meta = self._meta.get(symbol) or {"symbol": symbol, "aliases": [symbol]}
            score = self._score_symbol(meta, headline, summary, content)
            scores[symbol] = score
            if score >= config.TICKER_PUBLISH_SCORE_THRESHOLD:
                selected.append(symbol)

        if selected:
            log.debug("Ticker relevance scores: %s", scores)
            return selected

        return self._fallback_selection(article, headline, symbols)

    def _load_fresh_from_redis(self, now: float) -> bool:
        state = self._read_state()
        refreshed_at = int(state.get("last_refresh_epoch") or 0)
        if not refreshed_at:
            return False
        if now - refreshed_at > config.TICKER_DIRECTORY_REFRESH_SECONDS:
            return False
        return self._load_from_redis()

    def _read_state(self) -> dict[str, Any]:
        try:
            raw = self._redis.get(config.TICKER_DIRECTORY_STATE_KEY)
            if not raw:
                return {}
            return json.loads(raw)
        except Exception as exc:
            log.warning("Could not read ticker directory state: %s", exc)
            return {}

    def _load_from_redis(self) -> bool:
        try:
            rows = self._redis.hgetall(config.TICKER_META_HASH_KEY)
        except Exception as exc:
            log.warning("Could not read ticker directory from Redis: %s", exc)
            return False
        metadata: dict[str, dict[str, Any]] = {}
        for symbol, raw in rows.items():
            try:
                parsed = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict) and parsed.get("symbol"):
                metadata[str(parsed["symbol"]).upper()] = parsed
        if not metadata:
            return False
        self._apply_alias_overrides(metadata)
        self._meta = metadata
        log.info("Ticker directory loaded from Redis (%d assets)", len(metadata))
        return True

    def _fetch_assets(self) -> list[dict[str, Any]]:
        url = f"{config.ALPACA_TRADING_BASE_URL}/v2/assets"
        headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
        }
        params = {
            "status": "active",
            "asset_class": "us_equity",
        }
        with httpx.Client(timeout=config.TICKER_DIRECTORY_HTTP_TIMEOUT_SECONDS) as client:
            response = client.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Alpaca assets response was not a list")
        return [asset for asset in payload if isinstance(asset, dict)]

    def _load_alias_overrides(self) -> dict[str, list[str]]:
        overrides: dict[str, list[str]] = {}

        self._merge_override_payload(overrides, config.TICKER_ALIAS_OVERRIDES_JSON)

        if config.TICKER_ALIAS_OVERRIDES_PATH:
            try:
                payload = Path(config.TICKER_ALIAS_OVERRIDES_PATH).read_text()
                self._merge_override_payload(overrides, payload)
            except Exception as exc:
                log.warning("Could not read ticker alias override file: %s", exc)

        try:
            redis_rows = self._redis.hgetall(config.TICKER_ALIAS_OVERRIDES_KEY)
        except Exception as exc:
            log.warning("Could not read Redis ticker alias overrides: %s", exc)
            redis_rows = {}
        for symbol, aliases in redis_rows.items():
            self._merge_override_aliases(overrides, symbol, aliases)

        return overrides

    def _apply_alias_overrides(self, metadata: dict[str, dict[str, Any]]) -> int:
        overrides = self._load_alias_overrides()
        applied = 0
        for symbol, aliases in overrides.items():
            payload = metadata.get(symbol)
            if not payload:
                continue
            payload["aliases"] = _dedupe_aliases([*payload.get("aliases", []), *aliases])
            applied += len(aliases)
        return applied

    def _merge_override_payload(self, target: dict[str, list[str]], payload: str) -> None:
        if not payload:
            return
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            log.warning("Ticker alias override JSON is invalid: %s", exc)
            return
        if not isinstance(decoded, dict):
            log.warning("Ticker alias overrides must be a JSON object")
            return
        for symbol, aliases in decoded.items():
            self._merge_override_aliases(target, symbol, aliases)

    def _merge_override_aliases(
        self,
        target: dict[str, list[str]],
        symbol: Any,
        aliases: Any,
    ) -> None:
        normalized_symbol = str(symbol or "").upper().strip()
        if not normalized_symbol:
            return
        current = target.setdefault(normalized_symbol, [])
        current.extend(_coerce_aliases(aliases))
        target[normalized_symbol] = _dedupe_aliases(current)

    def _build_metadata(
        self,
        assets: list[dict[str, Any]],
        overrides: dict[str, list[str]],
    ) -> dict[str, dict[str, Any]]:
        metadata: dict[str, dict[str, Any]] = {}
        now = iso_z(utc_now())
        for asset in assets:
            if asset.get("class") not in (None, "us_equity"):
                continue
            symbol = str(asset.get("symbol") or "").upper().strip()
            name = str(asset.get("name") or "").strip()
            if not symbol:
                continue

            aliases = self._aliases_for_asset(symbol, name)
            aliases.extend(overrides.get(symbol, []))
            metadata[symbol] = {
                "symbol": symbol,
                "name": name,
                "exchange": asset.get("exchange"),
                "asset_class": asset.get("class"),
                "tradable": bool(asset.get("tradable")),
                "aliases": _dedupe_aliases(aliases),
                "updated_at": now,
            }
        return metadata

    def _aliases_for_asset(self, symbol: str, name: str) -> list[str]:
        aliases = [_normalize_text(symbol)]
        normalized_name = _normalize_text(name)
        if normalized_name:
            stripped_security = _strip_security_suffix(normalized_name)
            stripped_corp = _strip_corporate_suffixes(stripped_security)
            aliases.extend([normalized_name, stripped_security, stripped_corp])

            tokens = stripped_corp.split()
            if tokens:
                first = tokens[0]
                if len(first) >= 4 and first not in UNSAFE_SINGLE_TOKEN_ALIASES:
                    aliases.append(first)
            if len(tokens) >= 2:
                aliases.append(" ".join(tokens[:2]))

        return _dedupe_aliases(aliases)

    def _store_metadata(self, metadata: dict[str, dict[str, Any]], override_count: int) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(config.TICKER_META_HASH_KEY)

        items = list(metadata.items())
        for start in range(0, len(items), 500):
            chunk = {
                symbol: json.dumps(payload, default=str, separators=(",", ":"))
                for symbol, payload in items[start : start + 500]
            }
            if chunk:
                pipe.hset(config.TICKER_META_HASH_KEY, mapping=chunk)

        now = int(time.time())
        state = {
            "source": "alpaca_assets",
            "asset_count": len(metadata),
            "override_count": override_count,
            "last_refresh_epoch": now,
            "last_refresh_at": iso_z(utc_now()),
        }
        pipe.set(config.TICKER_DIRECTORY_STATE_KEY, json.dumps(state, separators=(",", ":")))
        pipe.execute()

    def _score_symbol(
        self,
        meta: dict[str, Any],
        headline: str,
        summary: str,
        content: str,
    ) -> int:
        aliases = [str(alias) for alias in meta.get("aliases") or []]
        if not aliases:
            aliases = [str(meta.get("symbol") or "")]

        score = 0
        if any(_contains_phrase(headline, alias) for alias in aliases):
            score += config.TICKER_HEADLINE_MATCH_SCORE
        if any(_contains_phrase(summary, alias) for alias in aliases):
            score += config.TICKER_SUMMARY_MATCH_SCORE
        if any(_contains_phrase(content, alias) for alias in aliases):
            score += config.TICKER_BODY_MATCH_SCORE
        return score

    def _fallback_selection(
        self,
        article: NormalizedArticle,
        headline: str,
        symbols: list[str],
    ) -> list[str]:
        explicit = [
            symbol
            for symbol in symbols
            if _contains_phrase(headline, _normalize_text(symbol))
        ]
        if explicit:
            return explicit
        if len(symbols) == 1 and is_relevant(article.headline, symbols[0]):
            return symbols
        return []

    def _is_boilerplate(self, headline: str) -> bool:
        normalized = _normalize_text(headline)
        return any(_normalize_text(phrase) in normalized for phrase in BOILERPLATE_PHRASES)
