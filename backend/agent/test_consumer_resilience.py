"""
Consumer resilience tests — the retry / dead-letter / shutdown state machine.

These run against an in-memory fake Redis so the exact production code paths
(_process_entry → _route_failed_entry → _process_due_retries / DLQ) execute
without a broker. The fake implements only the primitives the consumer uses.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

import config
import consumer as consumer_module
from consumer import RedisStreamConsumer
from schemas import NewsMessage


class FakeRedis:
    """Minimal in-memory stand-in for the redis-py client."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._stream_seq = 0
        self.acked: list[str] = []
        self.groups_created: list[str] = []

    # hashes ------------------------------------------------------------
    def hset(self, key, field=None, value=None, mapping=None):
        bucket = self.hashes.setdefault(key, {})
        if mapping:
            bucket.update({str(k): str(v) for k, v in mapping.items()})
        if field is not None:
            bucket[str(field)] = str(value)
        return 1

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(str(field))

    def hdel(self, key, *fields):
        bucket = self.hashes.get(key, {})
        removed = 0
        for field in fields:
            if bucket.pop(str(field), None) is not None:
                removed += 1
        return removed

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    # sorted sets ---------------------------------------------------------
    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(
            {str(member): float(score) for member, score in mapping.items()}
        )
        return len(mapping)

    def zrem(self, key, *members):
        bucket = self.zsets.get(key, {})
        removed = 0
        for member in members:
            if bucket.pop(str(member), None) is not None:
                removed += 1
        return removed

    def zrangebyscore(self, key, min, max, start=0, num=None):
        items = sorted(
            (
                (member, score)
                for member, score in self.zsets.get(key, {}).items()
                if float(min) <= score <= float(max)
            ),
            key=lambda item: item[1],
        )
        members = [member for member, _ in items]
        return members[start : start + num] if num is not None else members[start:]

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    # streams -------------------------------------------------------------
    def xadd(self, key, fields, id="*", maxlen=None, approximate=None):
        self._stream_seq += 1
        entry_id = f"{int(time.time() * 1000)}-{self._stream_seq}"
        self.streams.setdefault(key, []).append(
            (entry_id, {str(k): str(v) for k, v in fields.items()})
        )
        return entry_id

    def xack(self, key, group, entry_id):
        self.acked.append(entry_id)
        return 1

    def xgroup_create(self, key, group, start_id, mkstream=False):
        self.groups_created.append(group)
        return True

    def xautoclaim(self, key, group, consumer, min_idle_time, start_id, count):
        return ["0-0", []]

    def xreadgroup(self, group, consumer, streams, count=None):
        return []


@pytest.fixture()
def fake_consumer(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(consumer_module, "create_redis_client", lambda: fake)
    instance = RedisStreamConsumer()
    return instance, fake


def make_fields(**overrides) -> dict[str, str]:
    fields = {
        "ticker": "NVDA",
        "headline": "NVDA beats earnings expectations by a wide margin",
        "source": "test-suite",
        "published_at": "2099-01-01T00:00:00Z",
    }
    fields.update({key: str(value) for key, value in overrides.items()})
    return fields


def dlq_entries(fake: FakeRedis) -> list[dict[str, str]]:
    return [fields for _id, fields in fake.streams.get(config.AGENT_DLQ_STREAM_KEY, [])]


def test_valid_entry_invokes_callback_and_resolves(fake_consumer):
    consumer, fake = fake_consumer
    seen: list[NewsMessage] = []

    resolved = consumer._process_entry(
        "1-1", make_fields(), seen.append, lambda news, meta: None, source="stream"
    )

    assert resolved is True
    assert len(seen) == 1 and seen[0].ticker == "NVDA"
    assert not dlq_entries(fake)


def test_malformed_entry_dead_letters_and_resolves(fake_consumer):
    consumer, fake = fake_consumer

    resolved = consumer._process_entry(
        "1-2",
        {"headline": "missing required fields"},
        lambda news: None,
        lambda news, meta: None,
        source="stream",
    )

    assert resolved is True
    entries = dlq_entries(fake)
    assert len(entries) == 1
    assert entries[0]["reason"] == "malformed_message"


def test_unsupported_ticker_dead_letters_without_llm_spend(fake_consumer):
    consumer, fake = fake_consumer
    calls: list[NewsMessage] = []

    resolved = consumer._process_entry(
        "1-3",
        make_fields(ticker="TSX:BMO"),
        calls.append,
        lambda news, meta: None,
        source="stream",
    )

    assert resolved is True
    assert not calls
    assert dlq_entries(fake)[0]["reason"] == "unsupported_ticker_format"


def test_expired_signal_routes_to_on_expired(fake_consumer):
    consumer, fake = fake_consumer
    expired: list[tuple[NewsMessage, dict]] = []

    resolved = consumer._process_entry(
        "1-4",
        make_fields(published_at="2020-01-01T00:00:00Z"),
        lambda news: pytest.fail("expired signal must not reach on_message"),
        lambda news, meta: expired.append((news, meta)),
        source="stream",
    )

    # Default audit window is 24h, so a 2020 signal is past it → dead letter.
    assert resolved is True
    assert dlq_entries(fake)[0]["reason"] == "expired_beyond_audit_window"
    assert not expired


def test_recently_expired_signal_calls_on_expired(fake_consumer, monkeypatch):
    consumer, fake = fake_consumer
    monkeypatch.setattr(config, "AGENT_MAX_TRADE_SIGNAL_AGE_SECONDS", 0)
    expired: list[dict] = []

    # One hour old: past the (patched) trade window, inside the audit window.
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    resolved = consumer._process_entry(
        "1-5",
        make_fields(published_at=one_hour_ago.isoformat().replace("+00:00", "Z")),
        lambda news: pytest.fail("expired signal must not reach on_message"),
        lambda news, meta: expired.append(meta),
        source="stream",
    )

    assert resolved is True
    assert len(expired) == 1
    assert expired[0]["age_seconds"] > 0
    assert not dlq_entries(fake)


def test_processing_failure_schedules_retry_then_succeeds(fake_consumer, monkeypatch):
    consumer, fake = fake_consumer
    monkeypatch.setattr(config, "AGENT_RETRY_BASE_DELAY_SECONDS", 0)
    attempts = {"n": 0}

    def flaky(news: NewsMessage) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient supabase outage")

    resolved = consumer._process_entry(
        "2-1", make_fields(), flaky, lambda news, meta: None, source="stream"
    )

    # Failure still resolves the stream entry (ACK) — the retry is independent.
    assert resolved is True
    assert fake.zcard(config.AGENT_RETRY_ZSET_KEY) == 1
    payload = json.loads(fake.hget(config.AGENT_RETRY_HASH_KEY, "2-1"))
    assert payload["attempts"] == 1
    assert "transient supabase outage" in payload["last_error"]

    # Retry is due immediately (base delay 0) and succeeds on attempt 2.
    consumer._process_due_retries(flaky, lambda news, meta: None)
    assert attempts["n"] == 2
    assert fake.zcard(config.AGENT_RETRY_ZSET_KEY) == 0
    assert fake.hget(config.AGENT_RETRY_HASH_KEY, "2-1") is None
    assert not dlq_entries(fake)


def test_max_attempts_exhausted_goes_to_dlq(fake_consumer):
    consumer, fake = fake_consumer

    def always_fails(news: NewsMessage) -> None:
        raise RuntimeError("permanent failure")

    resolved = consumer._process_entry(
        "3-1",
        make_fields(),
        always_fails,
        lambda news, meta: None,
        source="retry",
        attempts=config.AGENT_MAX_PROCESSING_ATTEMPTS - 1,
    )

    assert resolved is True
    entries = dlq_entries(fake)
    assert len(entries) == 1
    assert entries[0]["reason"] == "max_attempts_exceeded"
    assert entries[0]["original_entry_id"] == "3-1"
    # Payload preserved for replay.
    assert json.loads(entries[0]["payload"])["ticker"] == "NVDA"
    assert fake.zcard(config.AGENT_RETRY_ZSET_KEY) == 0


def test_request_stop_exits_polling_loop(fake_consumer):
    consumer, fake = fake_consumer
    consumer.request_stop()

    # Must return promptly instead of blocking forever.
    start = time.monotonic()
    consumer.start(
        on_message=lambda news: None,
        on_expired=lambda news, meta: None,
    )
    assert time.monotonic() - start < 2.0

    # Final health state records the graceful shutdown.
    states = fake.hgetall("sentient:workers:health")
    final = json.loads(next(iter(states.values())))
    assert final["phase"] == "stopped"
