"""Workflow event bus.

Every event is written to the database first, then fanned out: through Redis
so any worker can emit and any API process can stream, or in-process when
Redis is unavailable. The database row is what lets a client that reconnects
catch up on what it missed — live delivery alone would lose it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionFactory
from app.models.entities import WorkflowEvent
from app.models.enums import ExecutionEvent

log = get_logger(__name__)

_CHANNEL = "evergent:workflow:{workflow_id}"

# workflow_id -> subscriber queues, used by the in-process fallback.
_local_subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis | None:
    global _redis
    if _redis is not None:
        return _redis
    try:
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        _redis = client
        return _redis
    except Exception as exc:
        log.warning("redis_unavailable", error=str(exc), fallback="in-process")
        return None


async def close() -> None:
    """Drop the Redis client.

    It is bound to the event loop that created it. A Celery worker runs each
    job in a fresh loop, so the client must not outlive the job.
    """
    global _redis
    if _redis is not None:
        client, _redis = _redis, None
        try:
            await client.aclose()
        except Exception as exc:
            log.warning("redis_close_failed", error=str(exc))


def _envelope(
    workflow_id: str, event: ExecutionEvent, data: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        # Lets a subscriber that receives one event by two routes drop the copy.
        "id": uuid.uuid4().hex,
        "event": event.value,
        "workflow_id": workflow_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "data": data or {},
    }


async def _persist(payload: dict[str, Any]) -> int | None:
    try:
        async with SessionFactory() as session:
            row = WorkflowEvent(
                event_id=payload["id"],
                workflow_id=uuid.UUID(payload["workflow_id"]),
                event=payload["event"],
                data=payload["data"],
                created_at=datetime.fromisoformat(payload["timestamp"]),
            )
            session.add(row)
            await session.commit()
            return row.seq
    except Exception as exc:
        log.warning("event_persist_failed", event_name=payload["event"], error=str(exc)[:200])
        return None


def as_envelope(row: WorkflowEvent) -> dict[str, Any]:
    # Stored as UTC; SQLite hands it back without a zone, so say it explicitly
    # rather than let a client read it as its local time.
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
    return {
        "id": row.event_id,
        "seq": row.seq,
        "event": row.event,
        "workflow_id": str(row.workflow_id),
        "timestamp": created.isoformat(),
        "data": row.data or {},
    }


async def history(
    session: AsyncSession, workflow_id: uuid.UUID, *, after: int = 0, limit: int = 1000
) -> list[dict[str, Any]]:
    """Persisted events after sequence number `after`, oldest first."""
    rows = await session.scalars(
        select(WorkflowEvent)
        .where(WorkflowEvent.workflow_id == workflow_id, WorkflowEvent.seq > after)
        .order_by(WorkflowEvent.seq)
        .limit(limit)
    )
    return [as_envelope(row) for row in rows]


async def publish(
    workflow_id: str,
    event: ExecutionEvent,
    data: dict[str, Any] | None = None,
) -> None:
    """Record and emit an event. Never raises — telemetry must not break a workflow."""
    payload = _envelope(workflow_id, event, data)
    seq = await _persist(payload)
    if seq is not None:
        payload["seq"] = seq

    for queue in list(_local_subscribers.get(workflow_id, ())):
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(payload)

    client = await _get_redis()
    if client is None:
        return
    try:
        await client.publish(_CHANNEL.format(workflow_id=workflow_id), json.dumps(payload))
    except Exception as exc:
        log.warning("event_publish_failed", error=str(exc))


async def subscribe(workflow_id: str) -> AsyncIterator[dict[str, Any]]:
    """Yield events for one workflow until the consumer stops iterating."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
    _local_subscribers.setdefault(workflow_id, set()).add(queue)

    client = await _get_redis()
    pubsub = None
    if client is not None:
        pubsub = client.pubsub()
        await pubsub.subscribe(_CHANNEL.format(workflow_id=workflow_id))

    async def pump_redis() -> None:
        assert pubsub is not None
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            with contextlib.suppress(json.JSONDecodeError, asyncio.QueueFull):
                queue.put_nowait(json.loads(message["data"]))

    pump: asyncio.Task[None] | None = (
        asyncio.create_task(pump_redis()) if pubsub is not None else None
    )

    # A publisher in this process reaches the subscriber twice: through the
    # local queue and back through Redis. Remembering recent ids drops the copy.
    seen: deque[str] = deque(maxlen=256)

    try:
        # Everything published from here on reaches this subscriber. Saying so
        # lets a client load current state now, leaving no gap in which an
        # event could fall between that load and the subscription.
        yield {"event": "subscribed", "workflow_id": workflow_id, "data": {}}
        while True:
            try:
                # The timeout doubles as an SSE keep-alive tick.
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except TimeoutError:
                yield {"event": "ping", "workflow_id": workflow_id, "data": {}}
                continue
            event_id = event.get("id")
            if event_id is not None:
                if event_id in seen:
                    continue
                seen.append(event_id)
            yield event
    finally:
        _local_subscribers.get(workflow_id, set()).discard(queue)
        if not _local_subscribers.get(workflow_id):
            _local_subscribers.pop(workflow_id, None)
        if pump is not None:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
                await pubsub.aclose()


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        with contextlib.suppress(Exception):
            await _redis.aclose()
        _redis = None
