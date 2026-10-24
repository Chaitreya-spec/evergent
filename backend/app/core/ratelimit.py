"""Request rate limits, shared by every API process through Redis.

Two tiers per client address:

- the sign-in tier (login, register, password reset, verification) is strict,
  because that is where password guessing happens;
- everything else gets a general allowance sized for a busy workspace tab.

Each is a sliding one-minute window kept in a Redis sorted set, updated
atomically by a Lua script, so several API replicas enforce one shared limit.
If Redis is unreachable the limiter falls back to per-process memory — less
exact behind several replicas, but never fails open entirely.
"""

from __future__ import annotations

import math
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

WINDOW_MS = 60_000

# Paths (after the API prefix) in the strict tier.
AUTH_PATHS = (
    "/auth/login",
    "/auth/register",
    "/auth/google",
    "/auth/password-reset/request",
    "/auth/password-reset/confirm",
    "/auth/verify-email/request",
    "/auth/verify-email/confirm",
)

_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
if redis.call('ZCARD', key) >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return {0, tonumber(oldest[2])}
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('PEXPIRE', key, window)
return {1, 0}
"""


@dataclass(slots=True)
class Decision:
    allowed: bool
    limit: int
    retry_after: int = 0


def tier_for(path: str) -> tuple[str, int]:
    """Which allowance a request draws on."""
    if path.startswith(settings.api_v1_prefix) and path[len(settings.api_v1_prefix) :] in AUTH_PATHS:
        return "auth", settings.auth_rate_limit_per_minute
    return "api", settings.rate_limit_per_minute


class RateLimiter:
    def __init__(self, url: str | None = None) -> None:
        self._url = url or settings.redis_url
        self._client: aioredis.Redis | None = None
        self._script = None
        # After a failed connection, memory is used for a while before Redis
        # is tried again, so an outage does not add a timeout to every request.
        self._redis_down_until = 0.0
        self._memory: dict[str, deque[float]] = defaultdict(deque)

    async def _redis(self) -> aioredis.Redis | None:
        if self._client is not None:
            return self._client
        if time.monotonic() < self._redis_down_until:
            return None
        try:
            client = aioredis.from_url(self._url, socket_connect_timeout=0.5, socket_timeout=0.5)
            await client.ping()
        except Exception as exc:
            self._redis_down_until = time.monotonic() + 30
            log.warning("rate_limit_redis_unavailable", error=str(exc)[:200], fallback="memory")
            return None
        self._client = client
        self._script = client.register_script(_SLIDING_WINDOW)
        return client

    def _hit_memory(self, key: str, limit: int) -> Decision:
        now = time.monotonic()
        bucket = self._memory[key]
        while bucket and now - bucket[0] > WINDOW_MS / 1000:
            bucket.popleft()
        if len(bucket) >= limit:
            return Decision(False, limit, max(1, math.ceil(WINDOW_MS / 1000 - (now - bucket[0]))))
        bucket.append(now)
        return Decision(True, limit)

    async def hit(self, client: str, path: str) -> Decision:
        tier, limit = tier_for(path)
        key = f"ratelimit:{tier}:{client}"
        redis = await self._redis()
        if redis is not None and self._script is not None:
            now = int(time.time() * 1000)
            try:
                allowed, oldest = await self._script(keys=[key], args=[now, WINDOW_MS, limit, f"{now}-{uuid.uuid4().hex[:8]}"])
            except Exception as exc:
                # Lost the connection mid-flight: drop the client and fall back.
                log.warning("rate_limit_redis_error", error=str(exc)[:200])
                self._client = None
                self._redis_down_until = time.monotonic() + 30
            else:
                if allowed:
                    return Decision(True, limit)
                return Decision(False, limit, max(1, math.ceil((int(oldest) + WINDOW_MS - now) / 1000)))
        return self._hit_memory(key, limit)

    async def close(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.aclose()


limiter = RateLimiter()
