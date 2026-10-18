"""Celery application.

Workflows run inside the API process by default, which is enough for
development. EXECUTION_BACKEND=celery sends them here instead, over Redis, so
the API can restart without dropping work. The engine is the same either way.
"""

from __future__ import annotations

import asyncio
import uuid

from celery import Celery

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

celery_app = Celery(
    "evergent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # A workflow is long-running; a hard kill mid-task would leave rows in
    # RUNNING forever, so the soft limit fires first and unwinds cleanly.
    task_soft_time_limit=settings.max_workflow_seconds,
    task_time_limit=settings.max_workflow_seconds + 60,
    worker_prefetch_multiplier=1,
)


@celery_app.task(name="evergent.run_workflow", bind=True)
def run_workflow_task(self, workflow_id: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Bridge from Celery's sync worker into the async engine."""
    log.info("celery_workflow_started", workflow_id=workflow_id, task_id=self.request.id)
    asyncio.run(_run_and_release(uuid.UUID(workflow_id)))
    return {"workflow_id": workflow_id, "status": "finished"}


async def _run_and_release(workflow_id: uuid.UUID) -> None:
    """Run one workflow, then release everything bound to this event loop.

    Each job gets a new loop from asyncio.run. Pooled database connections and
    the Redis client belong to the loop that opened them, so reusing them in
    the next job fails with "attached to a different loop". Releasing them at
    the end of each job keeps every job self-contained.
    """
    from app.db.session import engine
    from app.services.orchestrator import events
    from app.services.orchestrator.engine import run_workflow

    try:
        await run_workflow(workflow_id)
    finally:
        await events.close()
        await engine.dispose()
