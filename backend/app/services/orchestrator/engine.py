"""The execution engine.

Runs a workflow end to end: understand -> decompose -> route -> execute in
parallel -> validate -> refine -> synthesize. Every stage failure is recorded
on the workflow rather than raised to the caller, because this runs in the
background and the workflow row is how results are reported.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.errors import (
    BudgetExceededError,
    NoProviderAvailableError,
    ProviderError,
)
from app.core.logging import get_logger, task_id_ctx, workflow_id_ctx
from app.db.session import SessionFactory
from app.models.entities import (
    Artifact,
    Attachment,
    Message,
    Model,
    Task,
    TaskDependency,
    ToolCall,
    Workflow,
)
from app.models.enums import (
    Capability,
    ExecutionEvent,
    RoutingStrategy,
    TaskStatus,
    TaskType,
    WorkflowStatus,
)
from app.services.agents.base import AgentDefinition
from app.services.agents.roster import Roster, builtin_roster, load_roster
from app.services.context.manager import ContextBudget, ContextManager, summarise
from app.services.memory.store import MemoryScope, MemoryStore
from app.services.orchestrator import events
from app.services.orchestrator.llm import call_model
from app.services.orchestrator.planner import PlannedTask, TaskPlanner
from app.services.orchestrator.state import can_transition, transition
from app.services.orchestrator.synthesizer import synthesize
from app.services.orchestrator.workspace import WorkflowWorkspace, owned_paths
from app.services.providers.base import ChatMessage, extract_json
from app.services.providers.registry import load_credentials
from app.services.router.model_router import ModelRouter, RoutingDecision
from app.services.router.stats import refresh_stats_for_workflow
from app.services.sandbox.docker import RunSpec, SandboxResult, get_sandbox, tail
from app.services.storage import get_storage
from app.services.tools import builtin as _builtin_tools  # noqa: F401  (registers tools)
from app.services.tools.base import Tool, ToolContext, ToolResult, registry
from app.services.validation.reviewer import review_task

log = get_logger(__name__)


@dataclass(slots=True)
class AgentTurn:
    summary: str
    artifacts: list[dict[str, str]]
    run_spec: RunSpec | None = None
    # Files saved mid-turn through write_file. They count as produced even if
    # the final answer does not repeat them.
    written: set[str] = field(default_factory=set)


def tool_protocol(tools: list[Tool]) -> str:
    listing = "\n\n".join(f"- {t.name}: {t.description}\n{t.signature()}" for t in tools)
    return (
        "## TOOLS\n\n"
        "Before your final answer you may use these tools:\n\n"
        f"{listing}\n\n"
        "To use tools, reply with ONLY this JSON object instead of your final answer:\n"
        '{"tool_calls": [{"tool": "<name>", "arguments": {...}}]}\n\n'
        f"- Up to {settings.max_tool_calls_per_round} calls per reply. You get every "
        "result back, then may call more or finish.\n"
        f"- You have {settings.max_tool_rounds} rounds of tool use. Use them where they "
        "make the work more correct — reading a dependency's real file, checking "
        "current documentation, running your code — not by habit.\n"
        "- When you are done, reply with your final JSON object as described above, "
        "and nothing else."
    )


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=1, default=str)


def _recorded(arguments: Any) -> dict[str, Any]:
    """Arguments as stored on the ToolCall row. Long strings (a whole file
    being written) are cut — the artifact itself holds the real content."""
    if not isinstance(arguments, dict):
        return {"raw": str(arguments)[:2000]}
    return {
        key: (value[:2000] + f"... [{len(value)} chars]")
        if isinstance(value, str) and len(value) > 2000
        else value
        for key, value in arguments.items()
    }


class WorkflowCancelled(Exception):
    """Raised internally when a workflow is cancelled, paused or superseded."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        # Paused or superseded work was never started, so it goes back in the
        # queue; cancelled work is cancelled.
        self.requeue = reason in ("paused", "superseded")


def _as_utc(value: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes even for timezone-aware columns.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _capabilities(task: Task) -> list[Capability]:
    found: list[Capability] = []
    for value in task.required_capabilities or []:
        try:
            found.append(Capability(value))
        except ValueError:
            continue
    return found


class ExecutionEngine:
    def __init__(self, workflow_id: uuid.UUID):
        self.workflow_id = workflow_id
        self._semaphore = asyncio.Semaphore(settings.max_parallel_tasks)
        self._roster: Roster | None = None
        # This run's identity. Each run stamps started_at afresh, so a stamp
        # that no longer matches means a newer run has taken over.
        self._run_started: datetime | None = None
        # name -> (available, reason). Checking Docker or a search key once per
        # run is enough; an agent is only ever offered tools that work.
        self._tool_status: dict[str, tuple[bool, str]] = {}
        # How often each task has been handed to a worker in this run.
        self._dispatches: dict[uuid.UUID, int] = {}
        self._pinned_model: str | None = None
        self._allow_fallback = True

    @property
    def roster(self) -> Roster:
        # Loaded at the start of each run; the built-ins stand in before that.
        return self._roster or builtin_roster()

    # --- helpers ----------------------------------------------------------

    async def _load_workflow(self, session: AsyncSession) -> Workflow:
        workflow = await session.get(Workflow, self.workflow_id)
        if workflow is None:
            raise LookupError(f"Workflow {self.workflow_id} not found")
        return workflow

    async def _check_control(self, session: AsyncSession) -> Workflow:
        """Re-read status so cancel and pause take effect between tasks."""
        workflow = await self._load_workflow(session)
        await session.refresh(workflow)
        if self._run_started and _as_utc(workflow.started_at) != self._run_started:
            # A newer run of this workflow owns the work now. Carrying on would
            # execute the same tasks twice.
            raise WorkflowCancelled("superseded")
        if workflow.status == WorkflowStatus.CANCELLED:
            raise WorkflowCancelled("cancelled")
        if workflow.status == WorkflowStatus.PAUSED:
            raise WorkflowCancelled("paused")
        return workflow

    async def _check_budget(self, session: AsyncSession) -> None:
        """Enforce the cost, token and time ceilings.

        Values are read fresh rather than off a cached ORM object: concurrent
        tasks accumulate spend from their own sessions, so anything held in
        this session's identity map is already stale.
        """
        row = (
            await session.execute(
                select(
                    Workflow.total_cost_usd,
                    Workflow.total_tokens,
                    Workflow.max_cost_usd,
                    Workflow.max_tokens,
                    Workflow.max_seconds,
                    Workflow.started_at,
                ).where(Workflow.id == self.workflow_id)
            )
        ).one()

        if row.total_cost_usd >= row.max_cost_usd:
            raise BudgetExceededError(
                f"Workflow exceeded its cost ceiling of ${row.max_cost_usd:.2f}"
            )
        if row.total_tokens >= row.max_tokens:
            raise BudgetExceededError(
                f"Workflow exceeded its token ceiling of {row.max_tokens}"
            )
        started = _as_utc(row.started_at)
        if started and (datetime.now(UTC) - started).total_seconds() >= row.max_seconds:
            raise BudgetExceededError(
                f"Workflow exceeded its time limit of {row.max_seconds}s"
            )

    # --- lifecycle --------------------------------------------------------

    async def _heartbeat(self) -> None:
        """Mark the workflow alive while this engine works on it, so a
        restart that kills the engine is noticed rather than silently
        leaving the workflow "running" forever."""
        while True:
            try:
                async with SessionFactory() as session:
                    await session.execute(
                        update(Workflow)
                        .where(Workflow.id == self.workflow_id)
                        .values(heartbeat_at=datetime.now(UTC))
                    )
                    await session.commit()
            except Exception as exc:
                log.warning("heartbeat_failed", error=str(exc)[:200])
            await asyncio.sleep(settings.heartbeat_seconds)

    async def run(self) -> None:
        workflow_id_ctx.set(str(self.workflow_id))
        heartbeat = asyncio.create_task(self._heartbeat())
        async with SessionFactory() as session:
            try:
                await self._run(session)
            except WorkflowCancelled as exc:
                log.info("workflow_halted", reason=str(exc))
                # A superseded run hands over silently: its successor owns the
                # event stream now.
                if str(exc) != "superseded":
                    await events.publish(
                        str(self.workflow_id),
                        ExecutionEvent.WORKFLOW_PAUSED
                        if exc.requeue
                        else ExecutionEvent.WORKFLOW_CANCELLED,
                    )
            except BudgetExceededError as exc:
                await self._fail(session, str(exc), event=ExecutionEvent.BUDGET_EXCEEDED)
            except Exception as exc:
                log.exception("workflow_failed", error=str(exc))
                await self._fail(session, str(exc))
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
                await self._learn_from_run()
                workflow_id_ctx.set(None)

    async def _learn_from_run(self) -> None:
        """Fold this run's measurements back into the routing inputs.

        Runs on every exit path, not just success: a workflow that died because
        a provider kept failing is exactly the evidence reliability needs. Uses
        its own session because the run's session may be in a failed
        transaction by now, and never raises — the work is already done and
        reported, so a statistics problem must not rewrite that outcome.
        """
        try:
            async with SessionFactory() as stats_session:
                await refresh_stats_for_workflow(stats_session, self.workflow_id)
        except Exception as exc:
            log.warning("routing_stats_refresh_failed", error=str(exc))

    async def _fail(
        self,
        session: AsyncSession,
        message: str,
        *,
        event: ExecutionEvent = ExecutionEvent.WORKFLOW_FAILED,
    ) -> None:
        # Whatever brought us here may have left the transaction unusable — a
        # failed flush does — and recording the failure must not itself fail,
        # or the workflow is stranded in a running state nothing can clear.
        await session.rollback()
        workflow = await self._load_workflow(session)
        await session.refresh(workflow)
        if not can_transition(workflow.status, WorkflowStatus.FAILED):
            # Already finished — cancelled while this failure unwound, say.
            log.warning("failure_after_finish", status=workflow.status, error=message[:200])
            return
        transition(workflow, WorkflowStatus.FAILED)
        workflow.error = message[:2000]
        workflow.completed_at = datetime.now(UTC)
        await session.commit()
        await self._record_outcome(session, workflow, f"This run failed: {message}")
        await events.publish(str(self.workflow_id), event, {"error": message})

    async def _run(self, session: AsyncSession) -> None:
        # Keys stored through the API may have changed since this process
        # (a long-lived worker, say) last read them.
        await load_credentials(session)
        workflow = await self._load_workflow(session)
        self._roster = (await load_roster(session)).for_team(workflow.team)
        self._pinned_model = workflow.pinned_model
        self._allow_fallback = workflow.allow_fallback

        # A workflow that already has a plan is being resumed — after a pause,
        # or to run tasks added by hand. Planning again would duplicate every
        # task it already has.
        resuming = bool(
            await session.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.workflow_id == self.workflow_id)
            )
        )

        if workflow.status != WorkflowStatus.PENDING:
            # Cancelled or paused between being queued and starting.
            log.info("run_not_started", status=workflow.status)
            return

        self._run_started = datetime.now(UTC)
        transition(workflow, WorkflowStatus.RUNNING if resuming else WorkflowStatus.PLANNING)
        workflow.started_at = self._run_started
        workflow.completed_at = None
        await session.commit()

        wid = str(self.workflow_id)
        await events.publish(
            wid,
            ExecutionEvent.WORKFLOW_STARTED,
            {"goal": workflow.goal, "resumed": resuming},
        )

        router = ModelRouter(session)
        strategy = RoutingStrategy(workflow.routing_strategy)

        if resuming:
            brief = workflow.brief or {"objective": workflow.goal}
            await self._requeue_skipped(session)
        else:
            brief = await self._plan(session, workflow, router, strategy)
            if workflow.requires_approval and workflow.approved_at is None:
                # The plan is saved; a person decides whether it runs.
                workflow = await self._check_control(session)
                transition(workflow, WorkflowStatus.AWAITING_APPROVAL)
                await session.commit()
                return

        # --- Execute ------------------------------------------------------
        transition(workflow, WorkflowStatus.RUNNING)
        await session.commit()
        await self._execute_graph(session, brief, router, strategy)

        # --- Synthesize ---------------------------------------------------
        workflow = await self._check_control(session)
        transition(workflow, WorkflowStatus.SYNTHESIZING)
        await session.commit()
        await events.publish(wid, ExecutionEvent.SYNTHESIS_STARTED)

        tasks = list(
            await session.scalars(
                select(Task).where(Task.workflow_id == self.workflow_id).order_by(Task.key)
            )
        )
        artifacts = list(
            await session.scalars(
                select(Artifact)
                .where(Artifact.workflow_id == self.workflow_id)
                .order_by(Artifact.path)
            )
        )
        unresolved = [
            f"{t.key}: {t.error}" for t in tasks if t.status == TaskStatus.FAILED and t.error
        ]

        synth_model = (await router.route(TaskType.SYNTHESIS, strategy=strategy)).model
        final = await synthesize(
            session,
            workflow_id=self.workflow_id,
            goal=workflow.goal,
            brief=brief,
            tasks=tasks,
            artifacts=artifacts,
            unresolved=unresolved,
            model=synth_model,
            team=workflow.team,
        )

        workflow = await self._load_workflow(session)
        workflow.final_result = final
        transition(workflow, WorkflowStatus.COMPLETED)
        workflow.completed_at = datetime.now(UTC)
        await session.commit()
        await self._record_outcome(
            session, workflow, final, files=[a.path for a in artifacts]
        )
        # Totals were accumulated by other sessions, so re-read before
        # reporting them.
        await session.refresh(workflow)

        await events.publish(
            wid,
            ExecutionEvent.WORKFLOW_COMPLETED,
            {
                "cost_usd": round(workflow.total_cost_usd, 6),
                "tokens": workflow.total_tokens,
                "tasks": len(tasks),
            },
        )

    # --- planning ---------------------------------------------------------

    async def _plan(
        self,
        session: AsyncSession,
        workflow: Workflow,
        router: ModelRouter,
        strategy: RoutingStrategy,
    ) -> dict[str, Any]:
        wid = str(self.workflow_id)
        planner = TaskPlanner(session, self.workflow_id)

        # --- Understand ---------------------------------------------------
        await events.publish(wid, ExecutionEvent.PLANNING_STARTED)
        commander_model = (
            await router.route(TaskType.REQUIREMENTS, strategy=strategy)
        ).model
        brief = await planner.understand(
            workflow.goal, commander_model, context=await self._background(session, workflow)
        )
        workflow.brief = brief
        await session.commit()

        # --- Decompose ----------------------------------------------------
        planner_model = (
            await router.route(TaskType.ARCHITECTURE, strategy=strategy)
        ).model
        planned = await planner.decompose(
            brief,
            planner_model,
            max_tasks=settings.max_tasks_per_workflow,
            roster=self.roster,
        )
        # Planning takes a while. If the workflow was paused, cancelled or
        # restarted meanwhile, this plan must not be saved: a restarted run is
        # planning too, and two saved plans would collide.
        await self._check_control(session)
        await self._persist_plan(session, planned, router, strategy)
        await events.publish(
            wid,
            ExecutionEvent.PLAN_READY,
            {
                "task_count": len(planned),
                "awaiting_approval": workflow.requires_approval and workflow.approved_at is None,
            },
        )
        return brief

    async def _background(self, session: AsyncSession, workflow: Workflow) -> str | None:
        """What the Commander should know beyond the request itself.

        Earlier turns of the same conversation, so a follow-up like "now add
        dark mode" has a referent, and the project's most relevant memories,
        so new work stays consistent with what was already built. Missing
        either is not an error: the request alone is still plannable.
        """
        parts: list[str] = []

        if workflow.conversation_id:
            recent = list(
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == workflow.conversation_id)
                    .order_by(Message.created_at.desc())
                    .limit(8)
                )
            )
            turns = [
                f"{m.role.upper()}: {summarise(m.content, 1500)}"
                for m in reversed(recent)
                if m.meta.get("workflow_id") != str(self.workflow_id)
            ][-6:]
            if turns:
                parts.append("## EARLIER IN THIS CONVERSATION\n" + "\n\n".join(turns))

        try:
            recalled = await MemoryStore(session, workflow.project_id).search(
                workflow.goal, limit=settings.memory_recall_limit
            )
        except Exception as exc:
            log.warning("memory_recall_failed", error=str(exc)[:300])
            recalled = []
        memories = [
            f"- {summarise(r.memory.content, 1200)}"
            for r in recalled
            if r.memory.key != f"workflow:{self.workflow_id}"
        ]
        if memories:
            parts.append(
                "## WHAT THIS PROJECT ALREADY HAS (from earlier work)\n"
                + "\n".join(memories)
            )
        attached = list(
            await session.scalars(
                select(Attachment).where(Attachment.workflow_id == self.workflow_id).order_by(Attachment.created_at)
            )
        )
        if attached:
            files: list[str] = []
            budget = 24_000
            for attachment in attached:
                try:
                    text = (await get_storage().get(attachment.storage_key)).decode("utf-8", errors="replace")
                except Exception as exc:
                    log.warning("attachment_unreadable", attachment=str(attachment.id), error=str(exc)[:200])
                    continue
                clipped = summarise(text, min(8_000, max(budget, 0)))
                budget -= len(clipped)
                files.append(f"### {attachment.filename}\n```\n{clipped}\n```")
            if files:
                parts.append("## FILES THE USER ATTACHED\n" + "\n\n".join(files))
        return "\n\n".join(parts) or None

    async def _record_outcome(
        self,
        session: AsyncSession,
        workflow: Workflow,
        result: str,
        *,
        files: list[str] | None = None,
    ) -> None:
        """Write the outcome into the conversation and, if it succeeded, memory.

        Never raises: the run is already finished and reported, and losing a
        transcript line must not turn a success into a failure.
        """
        try:
            if workflow.conversation_id:
                session.add(
                    Message(
                        conversation_id=workflow.conversation_id,
                        role="assistant",
                        content=result,
                        meta={"workflow_id": str(self.workflow_id), "status": workflow.status},
                    )
                )
                await session.commit()

            if files is None:
                return
            brief = workflow.brief or {}
            listed = ", ".join(files[:40]) + (" …" if len(files) > 40 else "")
            await MemoryStore(session, workflow.project_id).remember(
                MemoryScope.PROJECT,
                f"workflow:{self.workflow_id}",
                "\n".join(
                    line
                    for line in (
                        f"Request: {workflow.goal}",
                        f"Delivered: {brief.get('objective', '')}",
                        f"Stack: {', '.join(map(str, brief.get('stack') or []))}",
                        f"Files: {listed}" if files else "",
                        f"Summary: {summarise(result, 1200)}",
                    )
                    if line and not line.endswith(": ")
                ),
                workflow_id=str(self.workflow_id),
            )
        except Exception as exc:
            log.warning("outcome_not_recorded", error=str(exc)[:300])

    async def _requeue_skipped(self, session: AsyncSession) -> None:
        """Give tasks skipped behind a failure another chance on a rerun.

        If the failed dependency has since been retried, its dependants should
        run; if it is still failing, they are simply skipped again.
        """
        skipped = await session.scalars(
            select(Task).where(
                Task.workflow_id == self.workflow_id,
                Task.status == TaskStatus.SKIPPED,
            )
        )
        for task in skipped:
            task.status = TaskStatus.PENDING
            task.error = None
        await session.commit()

    async def _persist_plan(
        self,
        session: AsyncSession,
        planned: list[PlannedTask],
        router: ModelRouter,
        strategy: RoutingStrategy,
    ) -> None:
        wid = str(self.workflow_id)
        created: dict[str, Task] = {}

        for spec in planned:
            agent = self.roster.resolve(spec.agent, spec.task_type)
            task = Task(
                workflow_id=self.workflow_id,
                key=spec.key,
                title=spec.title,
                description=spec.description,
                task_type=spec.task_type.value,
                priority=spec.priority,
                status=TaskStatus.PENDING,
                agent_name=agent.name,
                required_capabilities=[c.value for c in agent.capabilities],
                input_payload={"owns": spec.owns},
            )
            session.add(task)
            created[spec.key] = task
        await session.flush()

        for spec in planned:
            for dep_key in spec.depends_on:
                if dep_key in created:
                    session.add(
                        TaskDependency(
                            task_id=created[spec.key].id,
                            depends_on_task_id=created[dep_key].id,
                        )
                    )

        # Route each task up front so the plan shows its intended model; the
        # engine re-routes if that model fails at execution time.
        decisions = {}
        for spec in planned:
            task = created[spec.key]
            decision = await self._route(router, task, strategy)
            task.model_id = decision.model.id
            task.routing_reason = decision.reason
            decisions[spec.key] = decision

        # Events describe what is committed, so they go out only after it is.
        await session.commit()

        for spec in planned:
            await events.publish(
                wid,
                ExecutionEvent.TASK_CREATED,
                {
                    "task_key": spec.key,
                    "title": spec.title,
                    "task_type": spec.task_type.value,
                    "agent": created[spec.key].agent_name,
                    "depends_on": spec.depends_on,
                    "owns": spec.owns,
                },
            )
            await events.publish(
                wid,
                ExecutionEvent.MODEL_SELECTED,
                {
                    "task_key": spec.key,
                    "model": decisions[spec.key].model.name,
                    "reason": decisions[spec.key].reason,
                },
            )

    # --- routing ----------------------------------------------------------

    async def _route(
        self,
        router: ModelRouter,
        task: Task,
        strategy: RoutingStrategy,
        *,
        exclude_model_id: uuid.UUID | None = None,
    ) -> RoutingDecision | None:
        """Route by what the assigned agent needs from a model.

        The agent's capabilities are recorded on the task when it is assigned,
        and they are a better guide than the task type: a custom agent has no
        type of its own, and an override may have been re-tuned.
        """
        task_type = TaskType(task.task_type)
        needed = _capabilities(task) or None
        # A model pinned on the task beats one pinned on the workflow.
        pinned = (task.input_payload or {}).get("pinned_model") or self._pinned_model
        if pinned:
            if exclude_model_id is not None and not self._allow_fallback:
                return None  # keep the chosen model; policy forbids swapping it
            if exclude_model_id is None:
                return await router.route(
                    task_type,
                    strategy=strategy,
                    required_capabilities=needed,
                    pinned=pinned,
                    allow_fallback=self._allow_fallback,
                )
        if exclude_model_id is not None:
            return await router.fallback(
                exclude_model_id,
                task_type,
                strategy=strategy,
                required_capabilities=needed,
            )
        return await router.route(
            task_type, strategy=strategy, required_capabilities=needed
        )

    @staticmethod
    async def _reload(session: AsyncSession, task_id: uuid.UUID) -> Task | None:
        """Re-read a task with its model.

        populate_existing is the point. Sessions here do not expire on commit,
        so a plain get() hands back the cached object whose `model` still
        points at the previous model even after model_id changed — the retry
        would run on the very model it was rerouted away from.
        """
        return await session.get(
            Task,
            task_id,
            options=[selectinload(Task.model).selectinload(Model.provider)],
            populate_existing=True,
        )

    # --- execution --------------------------------------------------------

    async def _execute_graph(
        self,
        session: AsyncSession,
        brief: dict[str, Any],
        router: ModelRouter,
        strategy: RoutingStrategy,
    ) -> None:
        """Run the DAG in waves, each wave as wide as dependencies allow."""
        while True:
            await self._check_control(session)
            await self._check_budget(session)

            tasks = list(
                await session.scalars(
                    select(Task)
                    .options(selectinload(Task.model).selectinload(Model.provider))
                    .where(Task.workflow_id == self.workflow_id)
                )
            )
            deps = list(
                await session.scalars(
                    select(TaskDependency).where(
                        TaskDependency.task_id.in_([t.id for t in tasks])
                    )
                )
            )

            status_by_id = {t.id: t.status for t in tasks}
            deps_by_task: dict[uuid.UUID, list[uuid.UUID]] = {}
            for dep in deps:
                deps_by_task.setdefault(dep.task_id, []).append(dep.depends_on_task_id)

            ready = [
                t
                for t in tasks
                if t.status == TaskStatus.PENDING
                and all(
                    status_by_id.get(d) == TaskStatus.COMPLETED
                    for d in deps_by_task.get(t.id, [])
                )
            ]

            if not ready:
                # Anything still pending is blocked behind a failure. Mark it
                # skipped so the workflow terminates instead of spinning.
                stuck = [t for t in tasks if t.status == TaskStatus.PENDING]
                for task in stuck:
                    task.status = TaskStatus.SKIPPED
                    task.error = "Blocked by a failed dependency"
                if stuck:
                    await session.commit()
                    log.warning("tasks_skipped", count=len(stuck))
                return

            ready.sort(key=lambda t: t.priority)

            # A task picked again and again without ever leaving PENDING would
            # spin this loop until the time budget ran out. Each task gets its
            # retries plus slack for a pause or hand-over; past that it fails
            # with a reason instead.
            for task in ready:
                self._dispatches[task.id] = self._dispatches.get(task.id, 0) + 1
            stuck = [
                t for t in ready if self._dispatches[t.id] > settings.max_task_retries + 3
            ]
            if stuck:
                for task in stuck:
                    task.status = TaskStatus.FAILED
                    task.error = "Never left the queue: it was dispatched repeatedly without running"
                    log.error("task_never_ran", task=task.key)
                await session.commit()
                continue

            # Dependencies are bound as a default argument rather than closed
            # over, so each scheduled coroutine keeps the mapping from its own
            # iteration.
            async def run_one(
                task: Task, dependencies: dict[uuid.UUID, list[uuid.UUID]] = deps_by_task
            ) -> None:
                # Each concurrent task gets its own session: a single
                # AsyncSession is not safe to share across tasks.
                async with self._semaphore, SessionFactory() as task_session:
                    try:
                        await self._execute_task(
                            task_session,
                            task.id,
                            brief,
                            dependencies.get(task.id, []),
                            router_strategy=strategy,
                        )
                    except Exception as exc:
                        # gather() below would swallow this. Unrecorded, the
                        # task would sit in RUNNING forever while the workflow
                        # carried on and reported success.
                        log.exception("task_crashed", task=task.key)
                        await self._mark_crashed(task.id, exc)

            for task in ready:
                await events.publish(
                    str(self.workflow_id),
                    ExecutionEvent.TASK_QUEUED,
                    {"task_key": task.key, "agent": task.agent_name},
                )
            await asyncio.gather(*(run_one(t) for t in ready), return_exceptions=True)
            session.expire_all()

    async def _mark_crashed(self, task_id: uuid.UUID, exc: Exception) -> None:
        async with SessionFactory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return
            task.status = TaskStatus.FAILED
            task.error = (
                str(exc) if isinstance(exc, BudgetExceededError) else f"Internal error: {type(exc).__name__}: {exc}"
            )[:2000]
            task.completed_at = datetime.now(UTC)
            await session.commit()
            await events.publish(
                str(self.workflow_id),
                ExecutionEvent.TASK_FAILED,
                {"task_key": task.key, "error": task.error},
            )

    async def _execute_task(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        brief: dict[str, Any],
        dependency_ids: list[uuid.UUID],
        *,
        router_strategy: RoutingStrategy,
    ) -> None:
        task_id_ctx.set(str(task_id))
        wid = str(self.workflow_id)
        router = ModelRouter(session)

        task = await session.get(
            Task, task_id, options=[selectinload(Task.model).selectinload(Model.provider)]
        )
        if task is None:
            return

        task_type = TaskType(task.task_type)
        agent = self.roster.resolve(task.agent_name, task_type)
        review_feedback: str | None = None

        if task.model is None:
            # Hand-added and reassigned tasks arrive unrouted, as do tasks
            # whose model was removed from the catalog after planning.
            try:
                decision = await self._route(router, task, router_strategy)
            except NoProviderAvailableError as exc:
                task.status = TaskStatus.FAILED
                task.error = str(exc)
                task.completed_at = datetime.now(UTC)
                await session.commit()
                await events.publish(
                    wid,
                    ExecutionEvent.TASK_FAILED,
                    {"task_key": task.key, "error": task.error},
                )
                return
            assert decision is not None
            task.model_id = decision.model.id
            task.routing_reason = decision.reason
            await session.commit()
            task = await self._reload(session, task_id)
            assert task is not None
            await events.publish(
                wid,
                ExecutionEvent.MODEL_SELECTED,
                {
                    "task_key": task.key,
                    "model": decision.model.name,
                    "reason": decision.reason,
                },
            )

        for attempt in range(settings.max_task_retries + 1):
            try:
                await self._check_control(session)
                await self._check_budget(session)
            except WorkflowCancelled as exc:
                # Nothing was started this attempt, so nothing is lost by
                # queueing it again: a paused workflow resumes it, and a newer
                # run picks it up.
                task.status = (
                    TaskStatus.PENDING if exc.requeue else TaskStatus.CANCELLED
                )
                await session.commit()
                return
            except BudgetExceededError:
                task.status = TaskStatus.CANCELLED
                await session.commit()
                return

            if attempt:
                task.status = TaskStatus.RETRYING
                task.retry_count = attempt
                await session.commit()
                await events.publish(
                    wid,
                    ExecutionEvent.RETRY_STARTED,
                    {"task_key": task.key, "attempt": attempt},
                )
                # Repeated failure is a signal about the model, not just luck:
                # reroute to a different one rather than retrying identically.
                alternative = await self._route(
                    router, task, router_strategy, exclude_model_id=task.model_id
                )
                if alternative is not None:
                    task.model_id = alternative.model.id
                    task.routing_reason = alternative.reason
                    await session.commit()
                    task = await self._reload(session, task_id)
                    assert task is not None

            if task.model is None:
                task.status = TaskStatus.FAILED
                task.error = "No model could be routed to this task"
                await session.commit()
                return

            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(UTC)
            await session.commit()
            await events.publish(
                wid,
                ExecutionEvent.TASK_STARTED,
                {
                    "task_key": task.key,
                    "agent": agent.name,
                    "model": task.model.name,
                    "attempt": attempt + 1,
                },
            )

            try:
                turn = await asyncio.wait_for(
                    self._run_agent(
                        session, task, agent, brief, dependency_ids, review_feedback
                    ),
                    timeout=settings.task_timeout_seconds,
                )
            except TimeoutError:
                task.error = f"Timed out after {settings.task_timeout_seconds}s"
                log.warning("task_timeout", task=task.key)
                continue
            except BudgetExceededError as exc:
                # Retrying cannot help: there is no room for another call.
                task.status = TaskStatus.FAILED
                task.error = str(exc)
                task.completed_at = datetime.now(UTC)
                await session.commit()
                await events.publish(wid, ExecutionEvent.TASK_FAILED, {"task_key": task.key, "error": task.error})
                return
            except ProviderError as exc:
                task.error = str(exc)
                log.warning("task_provider_error", task=task.key, error=str(exc))
                continue
            except Exception as exc:
                task.error = str(exc)
                log.exception("task_error", task=task.key)
                continue

            output, run_spec = turn.summary, turn.run_spec
            written = sorted(
                set(await self._write_artifacts(session, task, turn.artifacts))
                | turn.written
            )

            # An agent that owns files but wrote none of them has not done its
            # job, whatever it claims in its summary. Treat it as a failure so
            # the retry path runs instead of silently shipping nothing.
            owned = sorted(owned_paths(task))
            if owned and not written:
                task.error = (
                    f"Produced none of the files it owns ({', '.join(owned)})"
                )
                await session.commit()
                log.warning("task_produced_nothing", task=task.key, owns=owned)
                continue

            # --- Testing -------------------------------------------------
            # A Tester's tests are executed, not just read. Failure goes back
            # to it as evidence, exactly like review feedback.
            test_result = None
            if run_spec is not None and TaskType.TESTING in agent.handles:
                test_result = await self._run_tests(session, task, run_spec)
                if test_result is not None and not test_result.passed:
                    task.error = (
                        "Tests timed out"
                        if test_result.timed_out
                        else f"Tests failed ({test_result.stage}, exit {test_result.exit_code})"
                    )
                    review_feedback = (
                        f"Your tests were executed and FAILED at the {test_result.stage} "
                        f"step (exit code {test_result.exit_code}). Output:\n\n"
                        f"{tail(test_result.output, 6000)}\n\n"
                        "Fix a test that is wrong. If the code under test is wrong, "
                        "keep the test and name the faulty file in your summary."
                    )
                    await session.commit()
                    continue

            # --- Validation ----------------------------------------------
            if agent.requires_review and written:
                await events.publish(
                    wid, ExecutionEvent.VALIDATION_STARTED, {"task_key": task.key}
                )
                reviewer_model = (
                    await router.route(TaskType.REVIEW, strategy=router_strategy)
                ).model
                # What is actually saved, whichever way the agent wrote it.
                workspace = WorkflowWorkspace(session, self.workflow_id)
                files = [f"--- {path} ---\n{await workspace.read(path)}" for path in written]
                rendered = "\n\n".join(files)
                if test_result is not None:
                    # Evidence beats opinion: the reviewer sees that the tests
                    # actually ran and passed.
                    rendered += (
                        f"\n\n## TEST RUN (executed, exit {test_result.exit_code})\n"
                        f"{tail(test_result.output, 3000)}"
                    )
                review = await review_task(
                    session,
                    workflow_id=self.workflow_id,
                    task=task,
                    produced=rendered,
                    brief=brief,
                    model=reviewer_model,
                )
                await events.publish(
                    wid,
                    ExecutionEvent.REVIEW_COMPLETED,
                    {
                        "task_key": task.key,
                        "status": review.status.value,
                        "score": review.score,
                    },
                )

                if review.needs_work and attempt < settings.max_task_retries:
                    review_feedback = review.as_prompt()
                    task.error = f"Review: {review.summary}"
                    continue

            task.status = TaskStatus.COMPLETED
            task.output = output
            task.error = None
            task.completed_at = datetime.now(UTC)
            task.execution_time_ms = int(
                (task.completed_at - task.started_at).total_seconds() * 1000
            )
            await session.commit()
            await events.publish(
                wid,
                ExecutionEvent.TASK_COMPLETED,
                {"task_key": task.key, "artifacts": written},
            )
            task_id_ctx.set(None)
            return

        task.status = TaskStatus.FAILED
        task.completed_at = datetime.now(UTC)
        await session.commit()
        await events.publish(
            wid,
            ExecutionEvent.TASK_FAILED,
            {"task_key": task.key, "error": task.error},
        )
        task_id_ctx.set(None)

    async def _run_agent(
        self,
        session: AsyncSession,
        task: Task,
        agent: AgentDefinition,
        brief: dict[str, Any],
        dependency_ids: list[uuid.UUID],
        review_feedback: str | None,
    ) -> AgentTurn:
        """One agent turn: any number of tool rounds, then a final answer."""
        assert task.model is not None

        dep_tasks = (
            list(await session.scalars(select(Task).where(Task.id.in_(dependency_ids))))
            if dependency_ids
            else []
        )
        dep_artifacts = (
            list(
                await session.scalars(
                    select(Artifact).where(Artifact.task_id.in_(dependency_ids))
                )
            )
            if dependency_ids
            else []
        )

        context = ContextManager(
            ContextBudget(
                context_window=task.model.context_window,
                max_output_tokens=task.model.max_output_tokens,
            )
        ).build(
            brief=brief,
            task=task,
            dependency_outputs=[(t.key, t.output or "") for t in dep_tasks],
            dependency_artifacts=dep_artifacts,
            review_feedback=review_feedback,
        )

        tools = await self._offered_tools(agent) if agent.output_schema else []
        allowed = {t.name for t in tools}
        system = agent.system_prompt + (f"\n\n{tool_protocol(tools)}" if tools else "")
        tool_context = ToolContext(
            workflow_id=self.workflow_id,
            task=task,
            agent_name=agent.name,
            workspace=WorkflowWorkspace(session, self.workflow_id),
        )
        messages = [ChatMessage(role="user", content=context)]
        rounds = 0
        told_to_finish = False

        while True:
            result = await call_model(
                session,
                workflow_id=self.workflow_id,
                task_id=task.id,
                model=task.model,
                role=agent.name,
                system=system,
                messages=messages,
                max_tokens=min(task.model.max_output_tokens, 16000),
                json_mode=bool(agent.output_schema),
            )
            if not agent.output_schema:
                return AgentTurn(result.text, [])

            payload = extract_json(result.text)
            calls = (
                payload.get("tool_calls")
                if isinstance(payload, dict) and "artifacts" not in payload
                else None
            )
            if not (tools and isinstance(calls, list) and calls) or told_to_finish:
                break

            messages.append(ChatMessage(role="assistant", content=result.text))
            if rounds >= settings.max_tool_rounds:
                told_to_finish = True
                messages.append(
                    ChatMessage(
                        role="user",
                        content=(
                            f"You have used all {settings.max_tool_rounds} rounds of tool use. "
                            "Reply now with your final JSON object — no more tool calls."
                        ),
                    )
                )
                continue

            rounds += 1
            await events.publish(
                str(self.workflow_id),
                ExecutionEvent.TASK_PROGRESS,
                {"task_key": task.key, "tool_round": rounds, "max_tool_rounds": settings.max_tool_rounds},
            )
            sections: list[str] = []
            for number, call in enumerate(calls[: settings.max_tool_calls_per_round], 1):
                name = str(call.get("tool") or "") if isinstance(call, dict) else ""
                arguments = call.get("arguments", {}) if isinstance(call, dict) else {}
                outcome = await self._invoke_tool(
                    session, task, tool_context, name, arguments, allowed=allowed
                )
                body = (
                    _render_value(outcome.output)
                    if outcome.ok
                    else f"ERROR: {outcome.error}"
                    + (f"\n{_render_value(outcome.output)}" if outcome.output else "")
                )
                sections.append(
                    f"### {number}. {name or '(no tool named)'} — {'ok' if outcome.ok else 'failed'}\n"
                    + tail(body, settings.tool_result_chars)
                )
            if len(calls) > settings.max_tool_calls_per_round:
                sections.append(
                    f"(Only the first {settings.max_tool_calls_per_round} calls were run.)"
                )
            messages.append(
                ChatMessage(
                    role="user",
                    content=f"## TOOL RESULTS (round {rounds} of {settings.max_tool_rounds})\n\n"
                    + "\n\n".join(sections),
                )
            )

        if not isinstance(payload, dict):
            return AgentTurn(result.text, [], written=tool_context.written)

        artifacts = [
            {"path": str(a["path"]), "content": str(a.get("content", ""))}
            for a in payload.get("artifacts") or []
            if isinstance(a, dict) and a.get("path")
        ]
        run = payload.get("run")
        run_spec = (
            RunSpec(
                runtime=str(run.get("runtime") or "").strip().lower(),
                command=str(run["command"]),
                install=str(run["install"]) if run.get("install") else None,
            )
            if isinstance(run, dict) and run.get("command")
            else None
        )
        return AgentTurn(
            str(payload.get("summary") or result.text),
            artifacts,
            run_spec,
            written=tool_context.written,
        )

    async def _offered_tools(self, agent: AgentDefinition) -> list[Tool]:
        """The agent's allowed tools that can actually work right now."""
        offered: list[Tool] = []
        for name in agent.allowed_tools:
            tool = registry.get(name)
            if tool is None:
                continue
            if name not in self._tool_status:
                self._tool_status[name] = await tool.available()
                if not self._tool_status[name][0]:
                    log.info("tool_unavailable", tool=name, reason=self._tool_status[name][1])
            if self._tool_status[name][0]:
                offered.append(tool)
        return offered

    async def _invoke_tool(
        self,
        session: AsyncSession,
        task: Task,
        context: ToolContext,
        name: str,
        arguments: Any,
        *,
        allowed: set[str],
    ) -> ToolResult:
        """Run one tool call, recording it and announcing it on the stream."""
        wid = str(self.workflow_id)
        await events.publish(
            wid, ExecutionEvent.TOOL_STARTED, {"task_key": task.key, "tool": name}
        )
        outcome = await registry.invoke(name, arguments, context, allowed=allowed)
        if name == "write_file" and outcome.ok and isinstance(outcome.output, dict):
            await events.publish(
                wid,
                ExecutionEvent.ARTIFACT_CREATED,
                {"task_key": task.key, "path": outcome.output.get("written"), "chars": outcome.output.get("chars")},
            )
        session.add(
            ToolCall(
                task_id=task.id,
                tool_name=(name or "(none)")[:64],
                arguments=_recorded(arguments),
                result={"output": outcome.output},
                succeeded=outcome.ok,
                error=outcome.error,
                duration_ms=outcome.duration_ms,
            )
        )
        await session.commit()
        await events.publish(
            wid,
            ExecutionEvent.TOOL_COMPLETED,
            {
                "task_key": task.key,
                "tool": name,
                "ok": outcome.ok,
                "duration_ms": outcome.duration_ms,
                "error": outcome.error,
            },
        )
        return outcome

    async def _run_tests(
        self, session: AsyncSession, task: Task, spec: RunSpec
    ) -> SandboxResult | None:
        """Execute a Tester's tests against the whole workspace.

        Returns None when they could not be run at all — no container runtime,
        or the sandbox switched off. That is recorded and the task proceeds on
        review alone, as it did before execution existed: missing
        infrastructure is reported, not treated as failing tests.
        """
        wid = str(self.workflow_id)
        sandbox = get_sandbox()
        arguments = {"runtime": spec.runtime, "install": spec.install, "command": spec.command}

        ok, reason = await sandbox.available()
        if not ok:
            session.add(
                ToolCall(
                    task_id=task.id,
                    tool_name="run_tests",
                    arguments=arguments,
                    result={"skipped": True},
                    succeeded=False,
                    error=f"Tests not run: {reason}",
                )
            )
            await session.commit()
            await events.publish(
                wid, ExecutionEvent.TESTS_SKIPPED, {"task_key": task.key, "reason": reason}
            )
            log.warning("tests_not_run", task=task.key, reason=reason)
            return None

        files = [
            (a.path, a.content)
            for a in await session.scalars(
                select(Artifact).where(Artifact.workflow_id == self.workflow_id)
            )
        ]
        await events.publish(
            wid, ExecutionEvent.TESTS_STARTED, {"task_key": task.key, "command": spec.command}
        )
        result = await sandbox.run(files, spec)
        if result.infrastructure_failed:
            session.add(
                ToolCall(
                    task_id=task.id,
                    tool_name="run_tests",
                    arguments=arguments,
                    result={"skipped": True, "output": result.output},
                    succeeded=False,
                    error=f"Tests not run: {result.output}",
                )
            )
            await session.commit()
            await events.publish(
                wid,
                ExecutionEvent.TESTS_SKIPPED,
                {"task_key": task.key, "reason": result.output},
            )
            log.error("sandbox_setup_failed", task=task.key, reason=result.output)
            return None

        session.add(
            ToolCall(
                task_id=task.id,
                tool_name="run_tests",
                arguments=arguments,
                result={
                    "exit_code": result.exit_code,
                    "stage": result.stage,
                    "timed_out": result.timed_out,
                    "output": result.output,
                },
                succeeded=result.passed,
                error=None if result.passed else f"{result.stage} exit {result.exit_code}",
                duration_ms=result.duration_ms,
            )
        )
        await session.commit()
        await events.publish(
            wid,
            ExecutionEvent.TESTS_COMPLETED,
            {
                "task_key": task.key,
                "passed": result.passed,
                "exit_code": result.exit_code,
                "stage": result.stage,
            },
        )
        return result

    async def _write_artifacts(
        self, session: AsyncSession, task: Task, artifacts: list[dict[str, str]]
    ) -> list[str]:
        """Persist a final answer's files, enforcing exclusive ownership.

        The same writer serves the write_file tool, so a file is held to the
        same rule whichever way an agent produced it.
        """
        workspace = WorkflowWorkspace(session, self.workflow_id)
        written: list[str] = []
        refused: list[str] = []
        for artifact in artifacts:
            outcome = await workspace.write(task, artifact["path"], artifact["content"])
            if outcome.path:
                written.append(outcome.path)
                await events.publish(
                    str(self.workflow_id),
                    ExecutionEvent.ARTIFACT_CREATED,
                    {"task_key": task.key, "path": outcome.path, "chars": len(artifact["content"])},
                )
            else:
                refused.append(str(outcome.error))
        if refused:
            log.warning("artifacts_refused", task=task.key, reasons=refused)
        return written


async def run_workflow(workflow_id: uuid.UUID) -> None:
    """Entry point used by the API background task and the Celery worker."""
    await ExecutionEngine(workflow_id).run()
