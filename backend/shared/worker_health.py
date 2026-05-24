"""
Shared Redis worker health helpers.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import redis


def redis_client() -> redis.Redis:
    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        db=int(os.environ.get("REDIS_DB", 0)),
        username=os.environ.get("REDIS_USERNAME", "") or None,
        password=os.environ.get("REDIS_PASSWORD", "") or None,
        decode_responses=True,
        socket_connect_timeout=float(os.environ.get("REDIS_CONNECT_TIMEOUT", "5")),
        socket_timeout=float(os.environ.get("REDIS_SOCKET_TIMEOUT", "5")),
    )


def health_key() -> str:
    return os.environ.get("WORKER_HEALTH_KEY", "sentient:workers:health")


def worker_name(default: str) -> str:
    service_env_name = f"{default.upper().replace('-', '_')}_WORKER_NAME"
    return (
        os.environ.get(service_env_name)
        or os.environ.get("WORKER_NAME")
        or default
    )


def read_worker_state(redis_conn: redis.Redis, name: str) -> dict[str, Any] | None:
    state_raw = redis_conn.hget(health_key(), name)
    if not state_raw:
        return None
    return json.loads(state_raw)


def write_worker_state(
    redis_conn: redis.Redis,
    default_worker_name: str,
    state: dict[str, Any],
) -> None:
    now = int(time.time())
    name = worker_name(default_worker_name)
    payload = {
        **state,
        "worker": name,
        "last_heartbeat_epoch": now,
        "last_heartbeat_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    redis_conn.hset(health_key(), name, json.dumps(payload, default=str))


def run_worker_healthcheck(default_worker_name: str, stale_after_seconds: int = 120) -> None:
    name = worker_name(default_worker_name)
    try:
        state = read_worker_state(redis_client(), name)
        if not state:
            print(
                f"No health state found for worker '{name}' in Redis key '{health_key()}'.",
                file=sys.stderr,
            )
            sys.exit(1)

        last_updated = int(state.get("last_heartbeat_epoch") or 0)
        diff = int(time.time()) - last_updated
        if diff > stale_after_seconds:
            print(f"{name} heartbeat is stale by {diff} seconds.", file=sys.stderr)
            sys.exit(1)

        status = state.get("status", "unknown")
        phase = state.get("phase", "unknown")
        detail = state.get("detail") or ""
        if status == "unhealthy":
            print(f"{name} is unhealthy: {phase} {detail}", file=sys.stderr)
            sys.exit(1)

        if status == "degraded":
            print(f"{name} is degraded but self-healing: {phase} {detail}")
            sys.exit(0)

        print(f"{name} is healthy: {phase}")
        sys.exit(0)
    except Exception as exc:
        print(f"Healthcheck failed for {name}: {exc}", file=sys.stderr)
        sys.exit(1)
