"""Where a workflow run actually executes.

The API never runs the engine itself; it calls dispatch_workflow, and the
EXECUTION_BACKEND setting decides between the API process and the Celery
workers. The engine is identical either way.
"""

from __future__ import annotations

import uuid

from fastapi import BackgroundTasks

from app.core.config import settings
from app.core.errors import QueueUnavailableError
from app.core.logging import get_logger
from app.services.orchestrator.engine import run_workflow

log = get_logger(__name__)


def dispatch_workflow(workflow_id: uuid.UUID, background: BackgroundTasks) -> str:
    """Start a run. Returns the backend that took it."""
    if settings.execution_backend == "celery":
        from app.workers.celery_app import run_workflow_task

        try:
            run_workflow_task.delay(str(workflow_id))
        except Exception as exc:
            # Saying so beats accepting the request and never running it.
            log.error("enqueue_failed", workflow_id=str(workflow_id), error=str(exc))
            raise QueueUnavailableError(
                "The task queue is unreachable, so the workflow was not started. "
                "Check that Redis is running, then run it again."
            ) from exc
        return "celery"

    background.add_task(run_workflow, workflow_id)
    return "inline"
