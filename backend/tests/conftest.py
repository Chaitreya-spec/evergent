"""Test fixtures.

The suite runs against SQLite so it needs no database server, and every test
gets its own file-backed database that is dropped afterwards.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import uuid
from collections.abc import AsyncIterator

import pytest

# Settings are read at import time, so the environment must be set before any
# application module is imported.
_tmp = tempfile.mkdtemp(prefix="evergent-test-")
os.environ.update(
    {
        "ENVIRONMENT": "test",
        # Never read the developer's own .env.
        "EVERGENT_ENV_FILE": "",
        "DATABASE_URL": f"sqlite+aiosqlite:///{_tmp}/test.db",
        "SECRET_KEY": "test-secret-key-not-used-anywhere-real",
        "REDIS_URL": "redis://localhost:6379/15",
        "RATE_LIMIT_PER_MINUTE": "100000",
        "AUTH_RATE_LIMIT_PER_MINUTE": "100000",
    }
)

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.db.seed import seed_catalog
from app.db.session import get_session
from app.main import app
from app.models import entities  # noqa: F401


@pytest.fixture
async def engine() -> AsyncIterator:
    db_path = f"{_tmp}/{uuid.uuid4().hex}.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()
    with contextlib.suppress(OSError):
        os.remove(db_path)


@pytest.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest.fixture
async def seeded_session(session: AsyncSession) -> AsyncSession:
    await seed_catalog(session)
    return session


@pytest.fixture(autouse=True)
def no_background_execution(monkeypatch) -> None:
    """Stop endpoints from actually running the engine.

    `run_workflow` deliberately opens its own session from the global factory,
    because a background task outlives the request that started it. That also
    means it bypasses the per-test database, so API tests stub it out and
    assert on HTTP semantics instead. Tests that exercise the engine patch
    this fixture off.
    """

    async def _noop(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("app.services.orchestrator.dispatch.run_workflow", _noop)


@pytest.fixture(autouse=True)
def fresh_event_bus() -> None:
    """Each test runs on its own event loop; a Redis client from an earlier
    test's loop cannot be reused."""
    from app.core.ratelimit import limiter
    from app.services.orchestrator import events as events_module

    events_module._redis = None
    # The limiter's Redis client is bound to a loop too.
    limiter._client = None
    limiter._script = None


@pytest.fixture
async def client(engine) -> AsyncIterator[AsyncClient]:
    """API client wired to the per-test database."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    # Events are written from their own session; keep them in this test's database.
    from app.services.orchestrator import events as events_module

    original_factory = events_module.SessionFactory
    events_module.SessionFactory = factory
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()
    events_module.SessionFactory = original_factory


@pytest.fixture
async def auth_client(client: AsyncClient) -> AsyncClient:
    """A client already registered and carrying a bearer token."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"user-{uuid.uuid4().hex[:8]}@example.com",
            "password": "a-sufficiently-long-password",
            "full_name": "Test User",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client
