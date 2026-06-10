"""
Valkey / Redis connection helper for the agent service.
"""

import os

from redis import Redis


def _redis_ssl_enabled() -> bool:
    """TLS is opt-in via REDIS_SSL so a private-subnet Valkey stays plaintext,
    while a managed endpoint (e.g. Upstash / rediss://) can flip it on without
    a code change."""
    return (os.environ.get("REDIS_SSL", "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def create_redis_client() -> Redis:
    """Create a Valkey-compatible Redis client from environment variables."""
    kwargs: dict = dict(
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
    if _redis_ssl_enabled():
        kwargs["ssl"] = True
        # Default to verifying certs; set REDIS_SSL_CERT_REQS=none for providers
        # that terminate TLS with a self-signed/internal cert.
        kwargs["ssl_cert_reqs"] = os.environ.get("REDIS_SSL_CERT_REQS", "required")
    return Redis(**kwargs)
