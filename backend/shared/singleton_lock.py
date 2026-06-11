"""
Redis-backed leader lock for singleton background workers.

The agent process starts side-loops (position monitor, outcome labeler) that
must run on exactly ONE replica: two position monitors would both cancel and
replace the same trailing stops, and two outcome labelers would double-write
scheduler runs. Each loop holds one of these locks and skips its work cycle
whenever it is not the leader, which makes running multiple agent replicas
safe.

Semantics:
  - acquire_or_renew(): SET key owner NX EX ttl. If the key exists and we own
    it, the TTL is refreshed. Returns True only while this process is leader.
  - A crashed leader simply stops renewing; another replica takes over after
    the TTL expires (default 3× the renew interval the callers use).
  - release() deletes the key only if we still own it, so a slow shutdown can
    never delete a newer leader's lock.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid

log = logging.getLogger("shared.singleton_lock")

# Delete-if-owner, executed atomically server-side.
_RELEASE_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
end
return 0
"""


class RedisLeaderLock:
    def __init__(self, redis_conn, name: str, ttl_seconds: int = 180) -> None:
        self._redis = redis_conn
        self._key = f"sentient:locks:{name}"
        self._ttl = max(int(ttl_seconds), 10)
        self._owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._is_leader = False

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    def acquire_or_renew(self) -> bool:
        try:
            if self._redis.set(self._key, self._owner, nx=True, ex=self._ttl):
                if not self._is_leader:
                    log.info("Acquired leader lock %s as %s", self._key, self._owner)
                self._is_leader = True
                return True
            holder = self._redis.get(self._key)
            if holder == self._owner:
                self._redis.expire(self._key, self._ttl)
                self._is_leader = True
                return True
        except Exception as exc:
            # Fail closed: without Redis we cannot prove leadership, so do not
            # run singleton work. The trading consumer itself is unaffected.
            log.warning("Leader lock check failed for %s: %s", self._key, exc)
        if self._is_leader:
            log.warning("Lost leader lock %s (held by another replica)", self._key)
        self._is_leader = False
        return False

    def release(self) -> None:
        try:
            self._redis.eval(_RELEASE_SCRIPT, 1, self._key, self._owner)
        except Exception as exc:
            log.debug("Leader lock release failed for %s: %s", self._key, exc)
        self._is_leader = False
