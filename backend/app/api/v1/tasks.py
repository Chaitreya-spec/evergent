"""Task management.

Tasks are normally created by the planner, but they are addressable in their
own right so a caller can inspect, amend, cancel or retry one without going
through its workflow.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CurrentUser, OwnedTask, SessionDep, get_owned_workflow
from app.core.errors import ConflictError, ValidationError
from app.models.entities import Model, Review, Task, TaskDependency, ToolCall, Workflow
from app.models.enums import TaskStatus
from app.schemas.workspace import (
    ReviewOut,
    TaskCreate,
    TaskDetail,
    TaskUpdate,
    ToolCallOut,
)
from app.services.agents.base import AgentDefinition
from app.services.agents.roster import Roster, load_roster

router = APIRouter(prefix="/tasks", tags=["tasks"])

# A task that has finished one way or another cannot be cancelled again.
TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED}
)


async def _detail(session: SessionDep, task: Task) -> TaskDetail:
    deps = await session.scalars(
        select(TaskDependency).where(TaskDependency.task_id == task.id)
    )
    dep_ids = [d.depends_on_task_id for d in deps]

    keys: list[str] = []
    if dep_ids:
        rows = await session.scalars(select(Task.key).where(Task.id.in_(dep_ids)))
        keys = sorted(rows)

    detail = TaskDetail.model_validate(task)
    detail.depends_on = keys
    detail.owns = list(task.input_payload.get("owns") or [])
    # Queried, not read off task.model: that relationship is not loaded here,
    # and touching it would lazy-load from async code and fail.
    detail.model_name = (
        await session.scalar(select(Model.name).where(Model.id == task.model_id))
        if task.model_id
        else None
    )
    return detail


async def _roster_for(session: SessionDep, workflow_id) -> Roster:  # type: ignore[no-untyped-def]
    """The roster as this workflow sees it: its team, if one was chosen."""
    team = await session.scalar(select(Workflow.team).where(Workflow.id == workflow_id))
    return (await load_roster(session)).for_team(team)


def _assignable(roster: Roster, name: str) -> AgentDefinition:
    agent = roster.find(name)
    if agent is None or agent not in roster.assignable():
        names = ", ".join(a.name for a in roster.assignable())
        whose = "this workflow's team" if roster.team else "agents that can take work"
        raise ValidationError(f"'{name}' is not one of {whose}. Choose from: {names}")
    return agent


@router.post("", response_model=TaskDetail, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate, session: SessionDep, user: CurrentUser
) -> TaskDetail:
    """Add a task to an existing workflow by hand."""
    workflow = await get_owned_workflow(payload.workflow_id, session, user)

    existing = await session.scalar(
        select(Task).where(Task.workflow_id == workflow.id, Task.key == payload.key)
    )
    if existing is not None:
        raise ConflictError(f"Task '{payload.key}' already exists in this workflow")

    # Put it on a specific desk, or let whoever handles its type take it. It
    # is routed to a model when it runs, by what that agent needs.
    roster = await _roster_for(session, workflow.id)
    if payload.agent:
        agent = _assignable(roster, payload.agent)
    else:
        found = roster.specialist_for(payload.task_type)
        if found is None:
            raise ValidationError(
                f"Nobody on this workflow's team does {payload.task_type.value} work. "
                f"Name an agent: {', '.join(a.name for a in roster.assignable())}"
            )
        agent = found
    task = Task(
        workflow_id=workflow.id,
        key=payload.key,
        title=payload.title,
        description=payload.description,
        task_type=payload.task_type.value,
        priority=payload.priority,
        status=TaskStatus.PENDING,
        agent_name=agent.name,
        required_capabilities=[c.value for c in agent.capabilities],
        input_payload={"owns": payload.owns},
    )
    session.add(task)
    await session.flush()

    # Dependencies are given by key, which is how the planner refers to tasks.
    if payload.depends_on:
        rows = await session.scalars(
            select(Task).where(
                Task.workflow_id == workflow.id, Task.key.in_(payload.depends_on)
            )
        )
        for dependency in rows:
            session.add(TaskDependency(task_id=task.id, depends_on_task_id=dependency.id))

    await session.commit()
    await session.refresh(task)
    return await _detail(session, task)


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(task: OwnedTask, session: SessionDep) -> TaskDetail:
    return await _detail(session, task)


@router.patch("/{task_id}", response_model=TaskDetail)
async def update_task(
    task: OwnedTask, payload: TaskUpdate, session: SessionDep
) -> TaskDetail:
    """Amend a task's instructions before it runs.

    Only a task that has not started can be edited; rewriting the brief of a
    task already in flight would make its output untraceable to any instruction.
    """
    if task.status not in (TaskStatus.PENDING, TaskStatus.QUEUED):
        raise ConflictError(
            f"Cannot edit a task that is {task.status}; cancel or retry it first"
        )

    if payload.title is not None:
        task.title = payload.title
    if payload.description is not None:
        task.description = payload.description
    if payload.priority is not None:
        task.priority = payload.priority
    if payload.owns is not None:
        task.input_payload = {**task.input_payload, "owns": payload.owns}
    if payload.agent is not None and payload.agent != task.agent_name:
        agent = _assignable(await _roster_for(session, task.workflow_id), payload.agent)
        task.agent_name = agent.name
        task.required_capabilities = [c.value for c in agent.capabilities]
        # Its model was chosen for the previous agent's needs. Clearing it
        # makes the engine route afresh when the task runs.
        task.model_id = None
        task.routing_reason = None

    if payload.model is not None:
        pinned = payload.model.strip()
        if pinned:
            model = await session.scalar(select(Model).where(Model.name == pinned))
            if model is None:
                raise ValidationError(f"There is no model called {pinned} in the catalog")
            task.input_payload = {**task.input_payload, "pinned_model": pinned}
            task.model_id = model.id
            task.routing_reason = f"{model.display_name} was chosen by hand."
        else:
            task.input_payload = {k: v for k, v in task.input_payload.items() if k != "pinned_model"}
            task.model_id = None
            task.routing_reason = None

    await session.commit()
    await session.refresh(task)
    return await _detail(session, task)


@router.post("/{task_id}/cancel", response_model=TaskDetail)
async def cancel_task(task: OwnedTask, session: SessionDep) -> TaskDetail:
    if task.status in TERMINAL_TASK_STATUSES:
        raise ConflictError(f"Task is already {task.status}")
    task.status = TaskStatus.CANCELLED
    await session.commit()
    await session.refresh(task)
    return await _detail(session, task)


@router.post("/{task_id}/retry", response_model=TaskDetail)
async def retry_task(task: OwnedTask, session: SessionDep) -> TaskDetail:
    """Put a finished task back in the queue.

    The next run of its workflow picks it up; dependants re-run against the
    new output because they only advance once their dependencies complete.
    """
    if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
        raise ConflictError(f"Task is already {task.status}")

    task.status = TaskStatus.PENDING
    task.error = None
    task.output = None
    task.started_at = None
    task.completed_at = None
    await session.commit()
    await session.refresh(task)
    return await _detail(session, task)


@router.get("/{task_id}/reviews", response_model=list[ReviewOut])
async def task_reviews(task: OwnedTask, session: SessionDep) -> list[Review]:
    """Every review of this task's attempts, oldest first."""
    rows = await session.scalars(
        select(Review).where(Review.task_id == task.id).order_by(Review.created_at)
    )
    return list(rows)


@router.get("/{task_id}/tool-calls", response_model=list[ToolCallOut])
async def task_tool_calls(task: OwnedTask, session: SessionDep) -> list[ToolCall]:
    """Tools this task used — including every test run and its output."""
    rows = await session.scalars(
        select(ToolCall).where(ToolCall.task_id == task.id).order_by(ToolCall.created_at)
    )
    return list(rows)
