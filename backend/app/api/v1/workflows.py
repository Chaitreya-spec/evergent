"""Workflows: create, run, control and stream."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, OwnedWorkflow, SessionDep, get_owned_project
from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.entities import (
    Artifact,
    Attachment,
    Conversation,
    Message,
    Model,
    Project,
    Review,
    Task,
    TaskDependency,
    ToolCall,
    Workflow,
)
from app.models.enums import (
    TERMINAL_WORKFLOW_STATUSES,
    ExecutionEvent,
    RoutingStrategy,
    TaskStatus,
    WorkflowStatus,
)
from app.schemas.common import Page
from app.schemas.workspace import (
    ArtifactOut,
    ReviewOut,
    TaskDetail,
    ToolCallOut,
    WorkflowCreate,
    WorkflowEventOut,
    WorkflowOut,
    WorkflowReviewOut,
    WorkflowToolCallOut,
)
from app.services import audit
from app.services.agents.roster import load_roster
from app.services.orchestrator import events
from app.services.orchestrator.dispatch import dispatch_workflow
from app.services.orchestrator.state import transition
from app.services.sandbox.workspace import build_archive

log = get_logger(__name__)
router = APIRouter(prefix="/workflows", tags=["workflows"])

# Tasks a previous run may still be executing.
IN_FLIGHT_TASK_STATUSES = (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING)


async def _start_run(session: SessionDep, workflow: Workflow) -> None:
    """Hand a workflow to a fresh engine run.

    Refused while tasks from a previous run are still finishing — a pause lets
    in-flight model calls complete — because a new run would find their
    dependants blocked and skip them. Stamping started_at is what tells any
    previous run it has been superseded, so two never work the same tasks.
    """
    in_flight = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(
            Task.workflow_id == workflow.id,
            Task.status.in_(IN_FLIGHT_TASK_STATUSES),
        )
    )
    if in_flight:
        raise ConflictError(
            f"{in_flight} task(s) from the previous run are still finishing. "
            "Try again in a moment."
        )

    transition(workflow, WorkflowStatus.PENDING)
    workflow.error = None
    workflow.started_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(workflow)


@router.post("", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowCreate,
    session: SessionDep,
    user: CurrentUser,
    background: BackgroundTasks,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, max_length=128),
) -> Workflow:
    project = await get_owned_project(payload.project_id, session, user)

    # A retried request — a double-click, a timeout and resend — returns the
    # workflow the first one created instead of starting the work twice.
    if idempotency_key:
        existing = await session.scalar(
            select(Workflow).where(
                Workflow.project_id == project.id,
                Workflow.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return existing

    attachments: list[Attachment] = []
    if payload.attachment_ids:
        attachments = list(
            await session.scalars(
                select(Attachment).where(
                    Attachment.id.in_(payload.attachment_ids),
                    Attachment.project_id == project.id,
                )
            )
        )
        if len(attachments) != len(set(payload.attachment_ids)):
            raise NotFoundError("One or more attachments were not found in this project")

    team: list[str] | None = None
    if payload.team:
        roster = await load_roster(session)
        available = {a.name for a in roster.assignable()}
        team = list(dict.fromkeys(name.strip() for name in payload.team))
        unknown = [name for name in team if name not in available]
        if unknown:
            raise ValidationError(
                f"Not agents that can take work: {', '.join(unknown)}. "
                f"Choose from: {', '.join(sorted(available))}"
            )

    if payload.model:
        known = await session.scalar(select(Model.id).where(Model.name == payload.model))
        if known is None:
            raise ValidationError(f"There is no model called {payload.model} in the catalog")

    # Budgets are copied at creation, so later changes to a project default
    # never retroactively alter a workflow that is already running.
    workflow = Workflow(
        project_id=project.id,
        goal=payload.goal,
        routing_strategy=RoutingStrategy.MANUAL.value if payload.model else payload.routing_strategy.value,
        max_cost_usd=payload.max_cost_usd
        or project.max_cost_usd
        or settings.max_workflow_cost_usd,
        max_tokens=project.max_tokens or settings.max_workflow_tokens,
        max_seconds=settings.max_workflow_seconds,
        status=WorkflowStatus.PENDING,
        team=team,
        requires_approval=payload.require_approval,
        idempotency_key=idempotency_key,
        pinned_model=payload.model,
        allow_fallback=payload.allow_fallback,
    )
    if payload.conversation_id is not None:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.project_id == project.id,
            )
        )
        if conversation is None:
            raise NotFoundError("Conversation not found in this project")
    else:
        conversation = Conversation(project_id=project.id, title=payload.goal[:120])
        session.add(conversation)
        await session.flush()

    workflow.conversation_id = conversation.id
    session.add(workflow)
    await session.flush()
    session.add(
        Message(
            conversation_id=conversation.id,
            role="user",
            content=payload.goal,
            meta={"workflow_id": str(workflow.id)},
        )
    )
    for attachment in attachments:
        attachment.workflow_id = workflow.id
    audit.record(
        session,
        "workflow.create",
        user_id=user.id,
        target_type="workflow",
        target_id=workflow.id,
        request=request,
        requires_approval=payload.require_approval,
    )
    await session.commit()
    await session.refresh(workflow)
    await events.publish(
        str(workflow.id),
        ExecutionEvent.WORKFLOW_CREATED,
        {"goal": workflow.goal, "requires_approval": workflow.requires_approval},
    )

    if payload.autostart:
        dispatch_workflow(workflow.id, background)

    return workflow


@router.get("", response_model=Page[WorkflowOut])
async def list_workflows(
    session: SessionDep,
    user: CurrentUser,
    project_id: uuid.UUID | None = None,
    status: list[WorkflowStatus] | None = Query(None),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[WorkflowOut]:
    base = select(Workflow).join(Project).where(Project.owner_id == user.id)
    if project_id:
        base = base.where(Workflow.project_id == project_id)
    if status:
        base = base.where(Workflow.status.in_(status))
    if q:
        base = base.where(Workflow.goal.ilike(f"%{q.replace('%', '').replace('_', '')}%"))

    total = await session.scalar(
        select(func.count()).select_from(base.subquery())
    )
    rows = await session.scalars(
        base.order_by(Workflow.created_at.desc()).limit(limit).offset(offset)
    )
    return Page(
        items=[WorkflowOut.model_validate(w) for w in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(workflow: OwnedWorkflow) -> Workflow:
    return workflow


@router.post("/{workflow_id}/plan", response_model=WorkflowOut)
async def plan(
    workflow: OwnedWorkflow, session: SessionDep, background: BackgroundTasks, request: Request
) -> Workflow:
    """Plan without running: the plan waits for approval.

    On a workflow already awaiting approval this replaces the plan.
    """
    if workflow.status not in (WorkflowStatus.PENDING, WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.FAILED):
        raise ConflictError(f"A {workflow.status} workflow cannot be planned")
    started = await session.scalar(
        select(func.count()).select_from(Task).where(Task.workflow_id == workflow.id, Task.started_at.is_not(None))
    )
    if started:
        raise ConflictError("Work has already started on this plan; it cannot be replaced")

    for task in await session.scalars(select(Task).where(Task.workflow_id == workflow.id)):
        await session.delete(task)
    workflow.requires_approval = True
    workflow.approved_at = None
    workflow.brief = None
    audit.record(session, "workflow.plan", target_type="workflow", target_id=workflow.id, request=request)
    await _start_run(session, workflow)
    dispatch_workflow(workflow.id, background)
    return workflow


@router.post("/{workflow_id}/approve", response_model=WorkflowOut)
async def approve(
    workflow: OwnedWorkflow,
    session: SessionDep,
    user: CurrentUser,
    background: BackgroundTasks,
    request: Request,
) -> Workflow:
    """Accept the plan as it stands — including any edits — and run it."""
    if workflow.status != WorkflowStatus.AWAITING_APPROVAL:
        raise ConflictError("Only a plan awaiting approval can be approved")
    workflow.approved_at = datetime.now(UTC)
    audit.record(
        session, "workflow.approve", user_id=user.id, target_type="workflow", target_id=workflow.id, request=request
    )
    await _start_run(session, workflow)
    await events.publish(str(workflow.id), ExecutionEvent.PLAN_APPROVED)
    dispatch_workflow(workflow.id, background)
    return workflow


@router.post("/{workflow_id}/run", response_model=WorkflowOut)
async def run(
    workflow: OwnedWorkflow, session: SessionDep, background: BackgroundTasks
) -> Workflow:
    if workflow.status in (WorkflowStatus.RUNNING, WorkflowStatus.PLANNING):
        raise ConflictError("Workflow is already running")
    if workflow.status == WorkflowStatus.AWAITING_APPROVAL:
        raise ConflictError("The plan is waiting for approval: approve it to run")

    # A workflow that already has a plan keeps it: the engine runs only what
    # is still pending, which is how hand-added and retried tasks get done.
    await _start_run(session, workflow)
    dispatch_workflow(workflow.id, background)
    return workflow


@router.post("/{workflow_id}/pause", response_model=WorkflowOut)
async def pause(workflow: OwnedWorkflow, session: SessionDep) -> Workflow:
    if workflow.status in TERMINAL_WORKFLOW_STATUSES:
        raise ConflictError(f"Workflow is already {workflow.status}")
    transition(workflow, WorkflowStatus.PAUSED)
    await session.commit()
    await session.refresh(workflow)
    return workflow


@router.post("/{workflow_id}/resume", response_model=WorkflowOut)
async def resume(
    workflow: OwnedWorkflow, session: SessionDep, background: BackgroundTasks
) -> Workflow:
    if workflow.status != WorkflowStatus.PAUSED:
        raise ConflictError("Only a paused workflow can be resumed")
    await _start_run(session, workflow)
    dispatch_workflow(workflow.id, background)
    return workflow


@router.post("/{workflow_id}/cancel", response_model=WorkflowOut)
async def cancel(
    workflow: OwnedWorkflow, session: SessionDep, user: CurrentUser, request: Request
) -> Workflow:
    if workflow.status in TERMINAL_WORKFLOW_STATUSES:
        raise ConflictError(f"Workflow is already {workflow.status}")

    transition(workflow, WorkflowStatus.CANCELLED)
    audit.record(
        session, "workflow.cancel", user_id=user.id, target_type="workflow", target_id=workflow.id, request=request
    )
    # Tasks not yet started are cancelled with it; running ones stop at their
    # next checkpoint.
    pending = await session.scalars(
        select(Task).where(
            Task.workflow_id == workflow.id,
            Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED]),
        )
    )
    for task in pending:
        task.status = TaskStatus.CANCELLED
    await session.commit()
    await session.refresh(workflow)
    # An engine that is still running announces this itself at its next
    # checkpoint; one that never started would leave the stream silent.
    await events.publish(str(workflow.id), ExecutionEvent.WORKFLOW_CANCELLED, {"by": "user"})
    return workflow


@router.get("/{workflow_id}/tasks", response_model=list[TaskDetail])
async def workflow_tasks(
    workflow: OwnedWorkflow, session: SessionDep
) -> list[TaskDetail]:
    tasks = list(
        await session.scalars(
            select(Task)
            .options(selectinload(Task.model))
            .where(Task.workflow_id == workflow.id)
            .order_by(Task.key)
        )
    )
    deps = list(
        await session.scalars(
            select(TaskDependency).where(
                TaskDependency.task_id.in_([t.id for t in tasks])
            )
        )
    )

    key_by_id = {t.id: t.key for t in tasks}
    deps_by_task: dict[uuid.UUID, list[str]] = {}
    for dep in deps:
        key = key_by_id.get(dep.depends_on_task_id)
        if key:
            deps_by_task.setdefault(dep.task_id, []).append(key)

    out: list[TaskDetail] = []
    for task in tasks:
        detail = TaskDetail.model_validate(task)
        detail.depends_on = sorted(deps_by_task.get(task.id, []))
        detail.owns = list(task.input_payload.get("owns") or [])
        detail.model_name = task.model.name if task.model else None
        out.append(detail)
    return out


@router.get("/{workflow_id}/artifacts", response_model=list[ArtifactOut])
async def workflow_artifacts(
    workflow: OwnedWorkflow, session: SessionDep
) -> list[Artifact]:
    rows = await session.scalars(
        select(Artifact)
        .where(Artifact.workflow_id == workflow.id)
        .order_by(Artifact.path)
    )
    return list(rows)


@router.get(
    "/{workflow_id}/archive",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}}},
)
async def workflow_archive(workflow: OwnedWorkflow, session: SessionDep) -> Response:
    """Every file the workflow produced, as one zip — the project, ready to open."""
    rows = await session.scalars(
        select(Artifact)
        .where(Artifact.workflow_id == workflow.id)
        .order_by(Artifact.path)
    )
    folder = f"evergent-{str(workflow.id)[:8]}"
    return Response(
        build_archive(((a.path, a.content) for a in rows), folder=folder),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{folder}.zip"'},
    )


@router.get("/{workflow_id}/reviews", response_model=list[WorkflowReviewOut])
async def workflow_reviews(workflow: OwnedWorkflow, session: SessionDep) -> list[WorkflowReviewOut]:
    """Every review of every task in the workflow, oldest first."""
    rows = await session.execute(
        select(Review, Task.key)
        .join(Task, Review.task_id == Task.id)
        .where(Task.workflow_id == workflow.id)
        .order_by(Review.created_at)
    )
    return [
        WorkflowReviewOut(
            **ReviewOut.model_validate(review).model_dump(),
            task_id=review.task_id,
            task_key=key,
            created_at=review.created_at,
        )
        for review, key in rows
    ]


@router.get("/{workflow_id}/tool-calls", response_model=list[WorkflowToolCallOut])
async def workflow_tool_calls(workflow: OwnedWorkflow, session: SessionDep) -> list[WorkflowToolCallOut]:
    """Every tool call in the workflow — test runs included — oldest first."""
    rows = await session.execute(
        select(ToolCall, Task.key)
        .join(Task, ToolCall.task_id == Task.id)
        .where(Task.workflow_id == workflow.id)
        .order_by(ToolCall.created_at)
    )
    return [
        WorkflowToolCallOut(**ToolCallOut.model_validate(call).model_dump(), task_id=call.task_id, task_key=key)
        for call, key in rows
    ]


@router.get("/{workflow_id}/events", response_model=list[WorkflowEventOut])
async def workflow_events(
    workflow: OwnedWorkflow,
    session: SessionDep,
    after: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
) -> list[dict]:
    """The workflow's recorded events, oldest first, after sequence `after`."""
    return await events.history(session, workflow.id, after=after, limit=limit)


# budget_exceeded is how a workflow fails when it runs out of money.
FINAL_EVENTS = {"workflow_completed", "workflow_failed", "workflow_cancelled", "budget_exceeded"}


def _sse(event: dict) -> str:
    lines = []
    if event.get("seq"):
        # What the browser sends back as Last-Event-ID when it reconnects.
        lines.append(f"id: {event['seq']}")
    lines.append(f"event: {event.get('event', 'message')}")
    lines.append(f"data: {json.dumps(event)}")
    return "\n".join(lines) + "\n\n"


@router.get("/{workflow_id}/stream")
async def stream(
    workflow: OwnedWorkflow,
    session: SessionDep,
    request: Request,
    after: int | None = Query(None, ge=0),
) -> StreamingResponse:
    """Server-sent events for one workflow.

    A client that reconnects (EventSource sends Last-Event-ID) or passes
    `after` first receives every recorded event it missed, then live ones.
    Each event is delivered once even where replay and live overlap.
    """
    try:
        cursor = after if after is not None else int(request.headers.get("last-event-id") or 0)
    except ValueError:
        cursor = 0

    # Subscribe before reading history, so no event can fall in between.
    subscription = events.subscribe(str(workflow.id))
    subscribed = await anext(subscription)
    missed = await events.history(session, workflow.id, after=cursor)
    finished = workflow.status in TERMINAL_WORKFLOW_STATUSES

    # Said last, before the server closes the stream on purpose. Without it a
    # browser's EventSource cannot tell "finished" from "dropped" and would
    # reconnect forever to a workflow with nothing more to say.
    end = {"event": "stream_end", "workflow_id": str(workflow.id), "data": {}}

    async def generator() -> AsyncIterator[str]:
        try:
            yield _sse(subscribed)
            seen = cursor
            # A replay is not cut short at a final event: a workflow that was
            # run again after finishing has newer events after it.
            for event in missed:
                seen = event["seq"]
                yield _sse(event)
            if finished:
                yield _sse(end)
                return
            async for event in subscription:
                if event.get("seq") is not None and event["seq"] <= seen:
                    continue
                yield _sse(event)
                if event.get("event") in FINAL_EVENTS:
                    yield _sse(end)
                    break
        finally:
            await subscription.aclose()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx buffering the stream into uselessness.
            "X-Accel-Buffering": "no",
        },
    )
