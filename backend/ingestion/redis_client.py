"""
Valkey / Redis connection helper for the ingestion service.
"""

import os

from redis import Redis


def create_redis_client() -> Redis:
    """Create a Valkey-compatible Redis client from environment variables."""
    return Redis(
        host=os.environ.get("REDIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        db=int(os.environ.get("REDIS_DB", "0")),
        username=os.environ.get("REDIS_USERNAME") or None,
        password=os.environ.get("REDIS_PASSWORD") or None,
        decode_responses=True,
        socket_connect_timeout=float(os.environ.get("REDIS_CONNECT_TIMEOUT", "5")),
        socket_timeout=float(os.environ.get("REDIS_SOCKET_TIMEOUT", "5")),
        health_check_interval=30,
    )
