"""
API security tests: auth gating, rate-limit bucket identity, metrics token,
and request-ID propagation. No external services — Supabase-auth-dependent
routes are exercised through dependency overrides, and everything that would
touch the network is either overridden or asserted to fail closed.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import time
from types import SimpleNamespace

import pytest

# Must be set before importing main: the limiter salt fails fast, /metrics
# fails fast without a token, and the JWT secret activates verified buckets.
os.environ["API_RATE_LIMIT_KEY_SALT"] = "test-rate-limit-salt"
os.environ["SUPABASE_JWT_SECRET"] = "test-jwt-secret"
os.environ["METRICS_AUTH_TOKEN"] = "test-metrics-token"
os.environ["API_RATE_LIMIT_STORAGE_URI"] = "memory://"

from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

import main


@pytest.fixture()
def client():
    test_client = TestClient(main.app, raise_server_exceptions=False)
    yield test_client
    main.app.dependency_overrides.clear()


# ── JWT helpers ───────────────────────────────────────────────────────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_token(payload: dict, secret: str, alg: str = "HS256") -> str:
    header = _b64url(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = _b64url(json.dumps(payload).encode())
    signature = _b64url(
        hmac.new(secret.encode(), f"{header}.{body}".encode(), "sha256").digest()
    )
    return f"{header}.{body}.{signature}"


def make_request(headers: dict[str, str]) -> StarletteRequest:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("203.0.113.7", 4242),
        "query_string": b"",
    }
    return StarletteRequest(scope)


VALID_CLAIMS = {"sub": "user-123", "exp": time.time() + 3600}


# ── Rate-limit bucket identity (verified JWT) ────────────────────────────────


def test_valid_token_buckets_by_user():
    token = make_token(VALID_CLAIMS, "test-jwt-secret")
    key = main.rate_limit_key(make_request({"authorization": f"Bearer {token}"}))
    assert key.startswith("user:")


def test_forged_signature_falls_back_to_ip_bucket():
    token = make_token(VALID_CLAIMS, "WRONG-secret")
    key = main.rate_limit_key(make_request({"authorization": f"Bearer {token}"}))
    assert key.startswith("ip:")


def test_expired_token_falls_back_to_ip_bucket():
    token = make_token({"sub": "user-123", "exp": time.time() - 60}, "test-jwt-secret")
    key = main.rate_limit_key(make_request({"authorization": f"Bearer {token}"}))
    assert key.startswith("ip:")


def test_alg_none_token_is_rejected():
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(VALID_CLAIMS).encode())
    token = f"{header}.{body}."
    assert main.jwt_payload_for_rate_limit(token) == {}


def test_two_users_get_distinct_buckets():
    token_a = make_token({"sub": "user-a", "exp": time.time() + 3600}, "test-jwt-secret")
    token_b = make_token({"sub": "user-b", "exp": time.time() + 3600}, "test-jwt-secret")
    key_a = main.rate_limit_key(make_request({"authorization": f"Bearer {token_a}"}))
    key_b = main.rate_limit_key(make_request({"authorization": f"Bearer {token_b}"}))
    assert key_a != key_b


# ── Route auth gating ─────────────────────────────────────────────────────────


def test_agent_config_update_requires_auth(client):
    response = client.post("/agent-config", json={"execution": {"order_qty": 5}})
    assert response.status_code == 401


def test_agent_config_update_rejects_non_super_user(client):
    main.app.dependency_overrides[main.require_user] = lambda: main.UserInfo(
        id="user-123", email="visitor@example.com", is_anonymous=False
    )
    response = client.post("/agent-config", json={"execution": {"order_qty": 5}})
    assert response.status_code == 403


def test_cancel_orders_rejects_anonymous_user(client):
    main.app.dependency_overrides[main.require_user] = lambda: main.UserInfo(
        id="anon-1", email=None, is_anonymous=True
    )
    response = client.post("/orders/cancel", json={"orderIds": ["abc"]})
    assert response.status_code == 403


def test_orders_requires_auth(client):
    assert client.get("/orders").status_code == 401


def test_orders_super_only_mode_blocks_regular_users(client, monkeypatch):
    monkeypatch.setattr(main, "ACCOUNT_ENDPOINTS_SUPER_ONLY", True)
    main.app.dependency_overrides[main.require_user] = lambda: main.UserInfo(
        id="user-123", email="visitor@example.com", is_anonymous=False
    )
    assert client.get("/orders").status_code == 403
    assert client.get("/portfolio").status_code == 403


def test_delete_trade_validates_uuid(client):
    main.app.dependency_overrides[main.require_user] = lambda: main.UserInfo(
        id="admin-1", email="admin@example.com", is_anonymous=False
    )
    monkey_super = main.is_super_user
    main.is_super_user = lambda user: True
    try:
        response = client.delete("/trades/not-a-uuid")
    finally:
        main.is_super_user = monkey_super
    assert response.status_code == 400


def test_trades_summary_validates_timestamps(client):
    # Bad range params are rejected with a 400 — and critically, this proves the
    # request routes to /trades/summary rather than being captured by the
    # /trades/{trade_id} path param (which would 400 with "Invalid trade id").
    response = client.get("/trades/summary?from=not-a-timestamp")
    assert response.status_code == 400
    assert "from" in response.json()["detail"].lower()


def test_trades_summary_counts(client, monkeypatch):
    # Happy path: each filter variant resolves to an exact count. Stub the
    # Supabase query builder so .execute() reports a per-call count.
    counts = iter([100, 12, 8, 3])  # all, buy, sell, sim

    class _Q:
        def select(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def gte(self, *a, **k):
            return self

        def lte(self, *a, **k):
            return self

        def or_(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def execute(self):
            return SimpleNamespace(count=next(counts))

    monkeypatch.setattr(main, "get_supabase", lambda: SimpleNamespace(table=lambda _n: _Q()))
    body = client.get("/trades/summary").json()["counts"]
    assert body == {"all": 100, "buy": 12, "sell": 8, "hold": 80, "sim": 3}


# ── Metrics token ─────────────────────────────────────────────────────────────


def test_metrics_requires_token(client):
    assert client.get("/metrics").status_code == 401
    assert (
        client.get(
            "/metrics", headers={"Authorization": "Bearer wrong-token"}
        ).status_code
        == 401
    )


def test_metrics_accepts_valid_token(client):
    response = client.get(
        "/metrics", headers={"Authorization": "Bearer test-metrics-token"}
    )
    assert response.status_code == 200
    # Redis is unreachable in tests → the scrape-health gauge reports 0.
    assert "sentient_metrics_scrape_ok" in response.text


# ── Rate-limited public reads (regression: BUG-2026-06-11-01) ───────────────
# slowapi with headers_enabled=True requires decorated endpoints to declare a
# `response: Response` parameter. Missing it 500s EVERY request to the route,
# so these tests execute the decorated paths end-to-end.


def test_ticker_search_decorated_route_returns_200_with_rate_headers(client):
    response = client.get("/tickers/search?q=NV")
    assert response.status_code == 200
    header_names = {name.lower() for name in response.headers}
    assert "x-ratelimit-limit" in header_names
    assert "x-ratelimit-remaining" in header_names


def test_stats_decorated_route_returns_200(client, monkeypatch):
    class _Result:
        data = {"analyzed": 7, "executed": 2}

    class _RPC:
        def execute(self):
            return _Result()

    class _Stub:
        def rpc(self, name, params=None):
            return _RPC()

    monkeypatch.setattr(main, "get_supabase", lambda: _Stub())
    response = client.get("/stats")
    assert response.status_code == 200
    assert response.json()["stats"]["analyzed"] == 7


def test_trades_decorated_route_returns_200(client, monkeypatch):
    class _Result:
        data = [{"id": "t1", "ticker": "NVDA"}]

    class _Query:
        def __getattr__(self, name):
            # select/order/limit/lt/gt all chain back to the query object.
            return lambda *args, **kwargs: self

        def execute(self):
            return _Result()

    class _Stub:
        def table(self, name):
            return _Query()

    monkeypatch.setattr(main, "get_supabase", lambda: _Stub())
    response = client.get("/trades")
    assert response.status_code == 200
    assert response.json()["trades"][0]["ticker"] == "NVDA"


# ── Request ID propagation ────────────────────────────────────────────────────


def test_request_id_is_generated_and_echoed(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("x-request-id")


def test_inbound_request_id_is_honored_and_sanitized(client):
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers.get("x-request-id") == "abc-123"

    hostile = client.get("/health", headers={"X-Request-ID": "x" * 200 + "<svg>!"})
    echoed = hostile.headers.get("x-request-id")
    assert echoed and len(echoed) <= 64
    assert "<" not in echoed and "!" not in echoed


def test_security_headers_present(client):
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in response.headers["content-security-policy"]
