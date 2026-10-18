"""Liveness and readiness."""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import SessionDep
from app.core.config import settings
from app.schemas.common import Health, Readiness
from app.services.providers.registry import configured_providers

router = APIRouter(tags=["health"])

VERSION = "1.0.0"


@router.get("/health", response_model=Health)
async def health() -> Health:
    """Liveness: the process is up. Deliberately touches no dependency."""
    return Health(status="ok", version=VERSION, environment=settings.environment)


@router.get("/ready", response_model=Readiness)
async def ready(session: SessionDep) -> Readiness:
    """Readiness: dependencies are reachable and a provider is configured."""
    database = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        database = False

    redis_ok = True
    try:
        client = aioredis.from_url(settings.redis_url)
        await client.ping()
        await client.aclose()
    except Exception:
        redis_ok = False

    providers = [p.name for p in configured_providers()]
    # Redis is optional — the event bus falls back to in-process fan-out — so
    # it does not gate readiness.
    status = "ok" if database and providers else "degraded"

    return Readiness(
        status=status, database=database, redis=redis_ok, providers=providers
    )
