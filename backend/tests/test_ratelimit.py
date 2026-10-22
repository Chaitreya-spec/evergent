"""Rate limits: two tiers, shared through Redis, with a memory fallback."""

from __future__ import annotations

import uuid

import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.ratelimit import RateLimiter, tier_for
from app.main import app

REDIS_URL = "redis://localhost:6379/15"


async def _redis_available() -> bool:
    try:
        client = aioredis.from_url(REDIS_URL, socket_connect_timeout=0.5)
        await client.ping()
        await client.aclose()
        return True
    except Exception:
        return False


@pytest.fixture
def small_limits(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 5)
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 2)


def test_sign_in_routes_get_the_strict_tier(small_limits) -> None:
    assert tier_for("/api/v1/auth/login") == ("auth", 2)
    assert tier_for("/api/v1/auth/password-reset/confirm") == ("auth", 2)
    assert tier_for("/api/v1/auth/me") == ("api", 5)
    assert tier_for("/api/v1/workflows") == ("api", 5)


async def test_memory_fallback_limits_per_client(small_limits) -> None:
    limiter = RateLimiter("redis://127.0.0.1:6390/0")  # nothing listens here
    results = [(await limiter.hit("1.2.3.4", "/api/v1/auth/login")).allowed for _ in range(3)]
    assert results == [True, True, False]
    # Another client, and the other tier, are counted separately.
    assert (await limiter.hit("5.6.7.8", "/api/v1/auth/login")).allowed
    assert (await limiter.hit("1.2.3.4", "/api/v1/workflows")).allowed


async def test_replicas_share_one_limit_through_redis(small_limits) -> None:
    if not await _redis_available():
        pytest.skip("needs Redis on localhost:6379")
    client = f"test-{uuid.uuid4().hex}"
    first, second = RateLimiter(REDIS_URL), RateLimiter(REDIS_URL)
    try:
        # Two separate limiters stand in for two API processes.
        outcomes = []
        for limiter in (first, second, first, second, first, second):
            outcomes.append((await limiter.hit(client, "/api/v1/workflows")).allowed)
        assert outcomes == [True, True, True, True, True, False]
        refused = await second.hit(client, "/api/v1/workflows")
        assert not refused.allowed and 1 <= refused.retry_after <= 60
    finally:
        await first.close()
        await second.close()


async def test_the_api_answers_429_with_retry_after(
    small_limits, monkeypatch, client: AsyncClient
) -> None:
    monkeypatch.setattr("app.main.limiter", RateLimiter("redis://127.0.0.1:6390/0"))
    codes = []
    for _ in range(3):
        response = await client.post(
            "/api/v1/auth/login", json={"email": "x@example.com", "password": "wrong-password-x"}
        )
        codes.append(response.status_code)
    assert codes == [401, 401, 429]
    assert response.headers["Retry-After"].isdigit()
    assert response.json()["code"] == "rate_limited"


PROXY_SECRET = "s" * 44


async def _sign_in_via_remote_frontend(secret: str, browser: str) -> int:
    """A sign-in as a frontend on Vercel relays it: from an address that no
    TRUSTED_PROXIES entry covers, naming the browser it serves."""
    transport = ASGITransport(app=app, client=("198.51.100.20", 1234))
    async with AsyncClient(transport=transport, base_url="http://test") as frontend:
        response = await frontend.post(
            "/api/v1/auth/login",
            json={"email": "x@example.com", "password": "wrong-password-x"},
            headers={"x-evergent-proxy-secret": secret, "x-evergent-client-ip": browser},
        )
    return response.status_code


async def test_behind_a_remote_frontend_each_browser_has_its_own_limit(
    small_limits, monkeypatch, client: AsyncClient
) -> None:
    """Otherwise one person guessing passwords locks everyone out of signing in."""
    monkeypatch.setattr("app.main.limiter", RateLimiter("redis://127.0.0.1:6390/0"))
    monkeypatch.setattr(settings, "frontend_proxy_secret", PROXY_SECRET)
    codes = [await _sign_in_via_remote_frontend(PROXY_SECRET, "203.0.113.9") for _ in range(3)]
    assert codes == [401, 401, 429]
    assert await _sign_in_via_remote_frontend(PROXY_SECRET, "203.0.113.77") == 401


async def test_without_the_secret_a_client_cannot_pick_its_own_address(
    small_limits, monkeypatch, client: AsyncClient
) -> None:
    """The client-address header is only believed with the secret, so it is no
    way around the limit for someone calling the API directly."""
    monkeypatch.setattr("app.main.limiter", RateLimiter("redis://127.0.0.1:6390/0"))
    monkeypatch.setattr(settings, "frontend_proxy_secret", PROXY_SECRET)
    codes = [await _sign_in_via_remote_frontend("forged", f"203.0.113.{n}") for n in range(3)]
    assert codes == [401, 401, 429]
