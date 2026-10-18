"""Workflows orphaned by a restart.

The engine touches `heartbeat_at` while it works. A workflow still marked as
in progress whose heartbeat has gone quiet lost its engine — the API process
or worker running it stopped. It is paused, with its interrupted tasks put
back in the queue, so a person can resume it; it is never left looking busy
forever, and never silently re-run (which could repeat paid work twice).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.entities import Task, Workflow
from app.models.enums import ExecutionEvent, TaskStatus, WorkflowStatus
from app.services.orchestrator import events
from app.services.orchestrator.state import transition

log = get_logger(__name__)

IN_PROGRESS = (
    WorkflowStatus.PLANNING,
    WorkflowStatus.RUNNING,
    WorkflowStatus.VALIDATING,
    WorkflowStatus.SYNTHESIZING,
)
INTERRUPTED_TASKS = (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)


async def recover_orphans(session: AsyncSession, *, now: datetime | None = None) -> list[uuid.UUID]:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(seconds=settings.orphan_after_seconds)
    orphans = list(
        await session.scalars(
            select(Workflow).where(
                Workflow.status.in_(IN_PROGRESS),
                or_(Workflow.heartbeat_at.is_(None), Workflow.heartbeat_at < cutoff),
                or_(Workflow.started_at.is_(None), Workflow.started_at < cutoff),
            )
        )
    )
    for workflow in orphans:
        for task in await session.scalars(
            select(Task).where(Task.workflow_id == workflow.id, Task.status.in_(INTERRUPTED_TASKS))
        ):
            task.status = TaskStatus.PENDING
            task.error = "Interrupted by a restart"
        transition(workflow, WorkflowStatus.PAUSED)
        workflow.error = "Interrupted: the process running it stopped. Resume to continue from where it was."
        workflow.reserved_cost_usd = 0.0
    await session.commit()

    for workflow in orphans:
        log.warning("workflow_orphaned", workflow_id=str(workflow.id))
        await events.publish(str(workflow.id), ExecutionEvent.WORKFLOW_PAUSED, {"reason": "interrupted"})
    return [w.id for w in orphans]
