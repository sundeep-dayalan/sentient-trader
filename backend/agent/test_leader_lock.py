"""Leader-lock semantics for singleton side-loops (position monitor, labeler)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from shared.singleton_lock import RedisLeaderLock


class FakeLockRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail = False

    def set(self, key, value, nx=False, ex=None):
        if self.fail:
            raise ConnectionError("redis down")
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.store.get(key)

    def expire(self, key, ttl):
        if self.fail:
            raise ConnectionError("redis down")
        if key in self.store:
            self.ttls[key] = ttl
            return True
        return False

    def eval(self, script, numkeys, key, owner):
        if self.store.get(key) == owner:
            del self.store[key]
            return 1
        return 0


def test_single_lock_acquires_and_renews():
    redis = FakeLockRedis()
    lock = RedisLeaderLock(redis, "position-monitor", ttl_seconds=60)

    assert lock.acquire_or_renew() is True
    assert lock.is_leader is True
    # Renewal keeps leadership and refreshes the TTL.
    assert lock.acquire_or_renew() is True
    assert redis.ttls["sentient:locks:position-monitor"] == 60


def test_second_replica_does_not_steal_leadership():
    redis = FakeLockRedis()
    leader = RedisLeaderLock(redis, "outcome-labeler", ttl_seconds=60)
    follower = RedisLeaderLock(redis, "outcome-labeler", ttl_seconds=60)

    assert leader.acquire_or_renew() is True
    assert follower.acquire_or_renew() is False
    assert follower.is_leader is False
    # Leader keeps renewing while the follower waits.
    assert leader.acquire_or_renew() is True


def test_follower_takes_over_after_release():
    redis = FakeLockRedis()
    leader = RedisLeaderLock(redis, "outcome-labeler", ttl_seconds=60)
    follower = RedisLeaderLock(redis, "outcome-labeler", ttl_seconds=60)

    assert leader.acquire_or_renew() is True
    leader.release()
    assert follower.acquire_or_renew() is True
    # The old leader is now the follower.
    assert leader.acquire_or_renew() is False


def test_release_never_deletes_a_newer_leaders_lock():
    redis = FakeLockRedis()
    old = RedisLeaderLock(redis, "position-monitor", ttl_seconds=60)
    new = RedisLeaderLock(redis, "position-monitor", ttl_seconds=60)

    assert old.acquire_or_renew() is True
    old.release()
    assert new.acquire_or_renew() is True

    # A slow shutdown of the old process must not delete the new lock.
    old.release()
    assert new.acquire_or_renew() is True


def test_redis_outage_fails_closed():
    redis = FakeLockRedis()
    lock = RedisLeaderLock(redis, "position-monitor", ttl_seconds=60)
    assert lock.acquire_or_renew() is True

    redis.fail = True
    # Without Redis we cannot prove leadership → do not run singleton work.
    assert lock.acquire_or_renew() is False
    assert lock.is_leader is False
