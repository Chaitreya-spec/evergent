"""End-to-end orchestration against a scripted provider.

This exercises the real engine — understand, decompose, route, execute in
parallel, validate, synthesise — with a fake provider standing in for the
models, so the whole loop is verifiable without credentials or spend.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.entities import (
    Artifact,
    Execution,
    Model,
    Project,
    Review,
    Task,
    ToolCall,
    User,
    Workflow,
)
from app.models.enums import TaskStatus, WorkflowStatus
from app.services.providers.base import (
    AIProvider,
    CompletionRequest,
    CompletionResult,
)


class ScriptedProvider(AIProvider):
    """Returns a canned response chosen by which agent is calling."""

    name = "scripted"

    def __init__(
        self,
        *,
        fail_first_frontend: bool = False,
        plan: list[dict[str, Any]] | None = None,
        before_worker: Callable[[], Awaitable[None]] | None = None,
        test_run: dict[str, Any] | None = None,
        worker_replies: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(api_key="test")
        self.calls: list[str] = []
        self.fail_first_frontend = fail_first_frontend
        self._frontend_attempts = 0
        # Overrides the default two-task plan.
        self.plan = plan
        # Runs inside every worker call, e.g. to pause the workflow mid-wave.
        self.before_worker = before_worker
        # What a Tester answers in "run", telling the engine how to execute.
        self.test_run = test_run
        self.reviewer_inputs: list[str] = []
        self.synthesizer_inputs: list[str] = []
        # Replies given to worker calls in order, before the default behaviour
        # — how a test acts out an agent using tools.
        self.worker_replies = list(worker_replies or [])
        self.planner_prompts: list[str] = []
        self.commander_messages: list[str] = []
        self.planner_messages: list[str] = []
        self.worker_prompts: list[str] = []
        self.worker_messages: list[str] = []

    def is_configured(self) -> bool:
        return True

    async def generate(self, request: CompletionRequest) -> CompletionResult:
        system = request.system or ""
        self.calls.append(system[:40])

        # Branch on each agent's distinctive opening line. Matching on loose
        # words like "review" would also hit the worker prompt, which mentions
        # the reviewer.
        if "You are the Commander" in system:
            self.commander_messages.append(request.messages[-1].content)
            payload = {
                "objective": "Build a one-page site",
                "deliverable_type": "static_site",
                "complexity": "low",
                "requirements": ["a hero section", "a contact form"],
                "constraints": ["no build step"],
                "stack": ["HTML", "CSS"],
            }
        elif "You are the Planner" in system:
            self.planner_prompts.append(system)
            self.planner_messages.append(request.messages[-1].content)
            payload = (
                {"tasks": self.plan}
                if self.plan
                else {
                    "tasks": [
                        {
                            "key": "t1",
                            "title": "Write the markup",
                            "description": "Produce index.html using class .hero",
                            "task_type": "frontend",
                            "priority": 1,
                            "depends_on": [],
                            "owns": ["index.html"],
                        },
                        {
                            "key": "t2",
                            "title": "Write the styles",
                            "description": "Produce styles.css targeting .hero",
                            "task_type": "frontend",
                            "priority": 2,
                            "depends_on": ["t1"],
                            "owns": ["styles.css"],
                        },
                    ]
                }
            )
        elif "You review work produced by other models" in system:
            self.reviewer_inputs.append(request.messages[-1].content)
            payload = {
                "status": "valid",
                "score": 9.0,
                "feedback": [],
                "summary": "Fits.",
            }
        elif "You produce the final answer" in system:
            self.synthesizer_inputs.append(request.messages[-1].content)
            return CompletionResult(
                text="Final deliverable: a one-page site.",
                input_tokens=100,
                output_tokens=50,
            )
        else:
            # A worker. Write whatever the task says it owns, which is listed
            # in the context the engine built.
            prompt = request.messages[-1].content
            self.worker_prompts.append(system)
            self.worker_messages.append(prompt)
            if self.worker_replies:
                return CompletionResult(
                    text=json.dumps(self.worker_replies.pop(0)),
                    input_tokens=50,
                    output_tokens=30,
                )
            if self.before_worker is not None:
                await self.before_worker()
            self._frontend_attempts += 1
            if self.fail_first_frontend and self._frontend_attempts == 1:
                raise RuntimeError("simulated provider outage")

            section = prompt.split("## FILES YOU OWN", 1)[-1].split("\n\n", 1)[0]
            owned = re.findall(r"^- (\S+)$", section, flags=re.MULTILINE)

            if owned and not {"index.html", "styles.css"} & set(owned):
                payload = {
                    "artifacts": [
                        {"path": path, "content": f"<!-- {path} -->"} for path in owned
                    ],
                    "summary": f"Wrote {', '.join(owned)}",
                }
            elif "styles.css" in prompt:
                payload = {
                    "artifacts": [{"path": "styles.css", "content": ".hero{color:red}"}],
                    "summary": "Wrote the styles",
                }
            else:
                payload = {
                    "artifacts": [
                        {"path": "index.html", "content": "<div class='hero'>hi</div>"},
                        # Deliberately unowned: the engine must refuse it.
                        {"path": "secrets.env", "content": "KEY=leak"},
                    ],
                    "summary": "Wrote the markup",
                }

        if (
            self.test_run
            and "Your tests are executed" in system
            and "artifacts" in payload
        ):
            payload["run"] = self.test_run

        return CompletionResult(
            text=json.dumps(payload), input_tokens=120, output_tokens=80
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        result = await self.generate(request)
        yield result.text


@pytest.fixture
async def engine_env(engine, monkeypatch):
    """A workflow ready to run, with every model served by the script."""
    from app.db import seed as seed_module
    from app.services.providers import registry as provider_registry

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # The engine opens its own sessions, so the factory it reaches for must be
    # the test one.
    monkeypatch.setattr("app.services.orchestrator.engine.SessionFactory", factory)
    monkeypatch.setattr("app.services.orchestrator.events.SessionFactory", factory)

    scripted = ScriptedProvider()
    monkeypatch.setitem(
        provider_registry.PROVIDER_FACTORIES, "scripted", ScriptedProvider
    )
    monkeypatch.setattr(provider_registry, "get_provider", lambda _n: scripted)
    monkeypatch.setattr(
        "app.services.router.model_router.get_provider", lambda _n: scripted
    )
    monkeypatch.setattr("app.services.orchestrator.llm.get_provider", lambda _n: scripted)

    async with factory() as session:
        await seed_module.seed_catalog(session)

        user = User(email="engine@example.com", hashed_password="x")
        session.add(user)
        await session.flush()

        project = Project(owner_id=user.id, name="Engine test")
        session.add(project)
        await session.flush()

        workflow = Workflow(
            project_id=project.id,
            goal="Build a one-page site with a hero and a contact form",
            max_cost_usd=100.0,
            max_tokens=10_000_000,
            max_seconds=600,
        )
        session.add(workflow)
        await session.commit()
        workflow_id = workflow.id

    return workflow_id, factory, scripted


class TestFullRun:
    async def test_workflow_completes(self, engine_env) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        workflow_id, factory, _ = engine_env
        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            assert workflow is not None
            assert workflow.status == WorkflowStatus.COMPLETED, workflow.error
            assert workflow.final_result
            assert workflow.brief["deliverable_type"] == "static_site"

    async def test_tasks_all_complete(self, engine_env) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        workflow_id, factory, _ = engine_env
        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            tasks = list(
                await session.scalars(
                    select(Task).where(Task.workflow_id == workflow_id).order_by(Task.key)
                )
            )
            assert [t.key for t in tasks] == ["t1", "t2"]
            assert all(t.status == TaskStatus.COMPLETED for t in tasks)
            # Every task must have been routed to a model and said why.
            assert all(t.model_id and t.routing_reason for t in tasks)

    async def test_unowned_artifact_is_refused(self, engine_env) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        workflow_id, factory, _ = engine_env
        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            paths = {
                a.path
                for a in await session.scalars(
                    select(Artifact).where(Artifact.workflow_id == workflow_id)
                )
            }

        # The frontend agent also tried to write secrets.env, which it does not
        # own. Letting that through is how parallel agents destroy each other.
        assert paths == {"index.html", "styles.css"}
        assert "secrets.env" not in paths

    async def test_spend_is_recorded(self, engine_env) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        workflow_id, factory, _ = engine_env
        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            executions = list(
                await session.scalars(
                    select(Execution).where(Execution.workflow_id == workflow_id)
                )
            )

        assert len(executions) >= 5  # commander, planner, 2 agents, reviewers, synth
        assert all(e.succeeded for e in executions)
        assert workflow.total_tokens > 0
        assert workflow.total_cost_usd > 0
        # The rollup must agree with the individual calls.
        assert workflow.total_tokens == sum(e.total_tokens for e in executions)

    async def test_review_is_recorded(self, engine_env) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        workflow_id, factory, _ = engine_env
        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            reviews = list(await session.scalars(select(Review)))

        assert reviews, "frontend work should have been reviewed"
        assert all(r.status == "valid" for r in reviews)


class TestResilience:
    async def test_retries_then_succeeds(self, engine, monkeypatch) -> None:
        """A provider failure must be retried, not fail the workflow."""
        from app.db import seed as seed_module
        from app.services.orchestrator.engine import ExecutionEngine
        from app.services.providers import registry as provider_registry

        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr("app.services.orchestrator.engine.SessionFactory", factory)
        monkeypatch.setattr("app.services.orchestrator.events.SessionFactory", factory)

        scripted = ScriptedProvider(fail_first_frontend=True)
        monkeypatch.setattr(provider_registry, "get_provider", lambda _n: scripted)
        monkeypatch.setattr(
            "app.services.router.model_router.get_provider", lambda _n: scripted
        )
        monkeypatch.setattr(
            "app.services.orchestrator.llm.get_provider", lambda _n: scripted
        )

        async with factory() as session:
            await seed_module.seed_catalog(session)
            user = User(email="retry@example.com", hashed_password="x")
            session.add(user)
            await session.flush()
            project = Project(owner_id=user.id, name="Retry test")
            session.add(project)
            await session.flush()
            workflow = Workflow(
                project_id=project.id,
                goal="Build a one-page site that fails once first",
                max_cost_usd=100.0,
                max_tokens=10_000_000,
                max_seconds=600,
            )
            session.add(workflow)
            await session.commit()
            workflow_id = workflow.id

        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            tasks = list(
                await session.scalars(select(Task).where(Task.workflow_id == workflow_id))
            )
            failed_calls = list(
                await session.scalars(
                    select(Execution).where(
                        Execution.workflow_id == workflow_id,
                        Execution.succeeded.is_(False),
                    )
                )
            )

        assert workflow.status == WorkflowStatus.COMPLETED, workflow.error
        assert any(t.retry_count > 0 for t in tasks), "the failure should have retried"
        # The failed attempt is still recorded — telemetry must not hide it.
        assert failed_calls


async def _workspace(engine, monkeypatch, scripted: ScriptedProvider):
    """Seeded database plus a workflow, with every model served by `scripted`."""
    from app.db import seed as seed_module
    from app.services.providers import registry as provider_registry

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.services.orchestrator.engine.SessionFactory", factory)
    monkeypatch.setattr("app.services.orchestrator.events.SessionFactory", factory)
    for target in (
        "app.services.router.model_router.get_provider",
        "app.services.orchestrator.llm.get_provider",
    ):
        monkeypatch.setattr(target, lambda _n: scripted)
    monkeypatch.setattr(provider_registry, "get_provider", lambda _n: scripted)

    async with factory() as session:
        await seed_module.seed_catalog(session)
        user = User(email="flow@example.com", hashed_password="x")
        session.add(user)
        await session.flush()
        project = Project(owner_id=user.id, name="Flow test")
        session.add(project)
        await session.flush()
        workflow = Workflow(
            project_id=project.id,
            goal="Build a one-page site",
            max_cost_usd=100.0,
            max_tokens=10_000_000,
            max_seconds=600,
        )
        session.add(workflow)
        await session.commit()
        return workflow.id, factory


async def _tasks(factory, workflow_id) -> dict[str, Task]:
    async with factory() as session:
        rows = await session.scalars(select(Task).where(Task.workflow_id == workflow_id))
        return {t.key: t for t in rows}


def _planner_calls(scripted: ScriptedProvider) -> int:
    return len(scripted.planner_prompts)


class TestRerun:
    async def test_rerun_runs_hand_added_work_without_replanning(
        self, engine, monkeypatch
    ) -> None:
        """Running a workflow that has a plan must not plan it again.

        It used to: the second plan collided with the first on task keys, and
        the failure handler then crashed on the broken transaction, leaving
        the workflow stuck in PLANNING where nothing could restart it.
        """
        from app.services.orchestrator.engine import ExecutionEngine

        scripted = ScriptedProvider()
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        await ExecutionEngine(workflow_id).run()

        # Exactly what POST /tasks writes: no model, owned file, a desk.
        async with factory() as session:
            session.add(
                Task(
                    workflow_id=workflow_id,
                    key="t9",
                    title="Add a footer",
                    description="Write footer.html",
                    task_type="frontend",
                    status=TaskStatus.PENDING,
                    agent_name="frontend",
                    required_capabilities=["frontend", "coding"],
                    input_payload={"owns": ["footer.html"]},
                )
            )
            workflow = await session.get(Workflow, workflow_id)
            workflow.status = WorkflowStatus.PENDING
            await session.commit()

        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            paths = set(
                await session.scalars(
                    select(Artifact.path).where(Artifact.workflow_id == workflow_id)
                )
            )
        tasks = await _tasks(factory, workflow_id)

        assert workflow.status == WorkflowStatus.COMPLETED, workflow.error
        assert _planner_calls(scripted) == 1
        assert set(tasks) == {"t1", "t2", "t9"}
        assert tasks["t9"].status == TaskStatus.COMPLETED
        # It arrived unrouted and was routed when it ran.
        assert tasks["t9"].model_id and tasks["t9"].routing_reason
        assert "footer.html" in paths

    async def test_a_failure_while_saving_is_recorded_not_stranded(
        self, engine, monkeypatch
    ) -> None:
        """A failed flush must still leave the workflow marked FAILED."""
        from app.services.orchestrator.engine import ExecutionEngine

        scripted = ScriptedProvider()
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)

        async def colliding_persist(self, session, *_args) -> None:
            for _ in range(2):
                session.add(
                    Task(
                        workflow_id=self.workflow_id,
                        key="same",
                        title="t",
                        description="d",
                        task_type="coding",
                    )
                )
            await session.flush()

        monkeypatch.setattr(ExecutionEngine, "_persist_plan", colliding_persist)
        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
        assert workflow.status == WorkflowStatus.FAILED
        assert "UNIQUE" in (workflow.error or "").upper()


class TestRerouting:
    async def test_a_retry_really_runs_on_the_new_model(
        self, engine, monkeypatch
    ) -> None:
        """The reroute used to be recorded but not used.

        The task's model_id changed, yet the cached relationship still pointed
        at the failed model, so the retry ran on it again while routing_reason
        claimed otherwise.
        """
        from app.services.orchestrator.engine import ExecutionEngine

        scripted = ScriptedProvider(fail_first_frontend=True)
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        await ExecutionEngine(workflow_id).run()

        tasks = await _tasks(factory, workflow_id)
        async with factory() as session:
            calls = list(
                await session.execute(
                    select(Execution.model_name, Execution.succeeded)
                    .where(
                        Execution.task_id == tasks["t1"].id, Execution.role == "frontend"
                    )
                    .order_by(Execution.created_at)
                )
            )
            recorded = await session.scalar(
                select(Model.name).where(Model.id == tasks["t1"].model_id)
            )

        (failed_model, failed_ok), (retry_model, retry_ok) = calls
        assert not failed_ok and retry_ok
        assert retry_model != failed_model
        # What the task says it used is what it actually used.
        assert recorded == retry_model


class TestPauseAndResume:
    async def test_pause_requeues_unstarted_work_and_resume_finishes_it(
        self, engine, monkeypatch
    ) -> None:
        from app.core.config import settings
        from app.services.orchestrator.engine import ExecutionEngine

        # One desk at a time, so the second task is still waiting its turn
        # when the first pauses the workflow.
        monkeypatch.setattr(settings, "max_parallel_tasks", 1)

        holder: dict[str, Any] = {}

        async def pause_once() -> None:
            if holder.get("paused"):
                return
            holder["paused"] = True
            async with holder["factory"]() as session:
                workflow = await session.get(Workflow, holder["workflow_id"])
                workflow.status = WorkflowStatus.PAUSED
                await session.commit()

        independent = [
            {
                "key": key,
                "title": f"Write {path}",
                "description": f"Produce {path}",
                "task_type": "frontend",
                "depends_on": [],
                "owns": [path],
            }
            for key, path in (("a", "a.html"), ("b", "b.html"))
        ]
        scripted = ScriptedProvider(plan=independent, before_worker=pause_once)
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        holder.update(factory=factory, workflow_id=workflow_id)

        await ExecutionEngine(workflow_id).run()

        tasks = await _tasks(factory, workflow_id)
        statuses = sorted(t.status for t in tasks.values())
        # The task in flight finished; the one waiting went back in the queue.
        # It used to be CANCELLED, which resume then never picked up.
        assert statuses == [TaskStatus.COMPLETED, TaskStatus.PENDING]

        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            workflow.status = WorkflowStatus.PENDING
            await session.commit()
        await ExecutionEngine(workflow_id).run()

        tasks = await _tasks(factory, workflow_id)
        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
        assert workflow.status == WorkflowStatus.COMPLETED, workflow.error
        assert all(t.status == TaskStatus.COMPLETED for t in tasks.values())
        assert _planner_calls(scripted) == 1

    async def test_a_superseded_run_stands_down(self, engine, monkeypatch) -> None:
        from datetime import UTC, datetime, timedelta

        from app.services.orchestrator.engine import ExecutionEngine, WorkflowCancelled

        workflow_id, factory = await _workspace(engine, monkeypatch, ScriptedProvider())
        old_run = ExecutionEngine(workflow_id)
        old_run._run_started = datetime.now(UTC) - timedelta(minutes=1)

        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            # A newer run stamped the workflow after this one started.
            workflow.started_at = datetime.now(UTC)
            await session.commit()

            with pytest.raises(WorkflowCancelled) as raised:
                await old_run._check_control(session)

        assert str(raised.value) == "superseded"
        assert raised.value.requeue


class TestCustomAgents:
    async def test_an_added_agent_gets_planned_work_and_its_own_instructions(
        self, engine, monkeypatch
    ) -> None:
        """The door, end to end: add an agent, and the next plan can use it."""
        from app.models.entities import Agent
        from app.services.orchestrator.engine import ExecutionEngine

        plan = [
            {
                "key": "m1",
                "title": "Build the mobile screen",
                "description": "Produce screen.tsx",
                "task_type": "frontend",
                "agent": "mobile",
                "depends_on": [],
                "owns": ["screen.tsx"],
            }
        ]
        scripted = ScriptedProvider(plan=plan)
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)

        async with factory() as session:
            session.add(
                Agent(
                    name="mobile",
                    display_name="Mobile Engineer",
                    description="Builds React Native screens.",
                    system_prompt="You are a mobile engineer. You build for small screens.",
                    capabilities=["frontend"],
                )
            )
            await session.commit()

        await ExecutionEngine(workflow_id).run()

        task = (await _tasks(factory, workflow_id))["m1"]
        assert task.status == TaskStatus.COMPLETED, task.error
        assert task.agent_name == "mobile"
        # Routed by what the agent declared it needs from a model.
        assert task.required_capabilities == ["frontend"]

        # The planner was told the agent exists...
        assert "- mobile: Builds React Native screens." in scripted.planner_prompts[0]
        # ...and the agent worked from its own instructions, with the output
        # contract appended for it.
        worker_system = scripted.worker_prompts[0]
        assert worker_system.startswith("You are a mobile engineer.")
        assert "Return a single JSON object" in worker_system

    async def test_a_plan_naming_an_unknown_agent_is_sent_back(
        self, engine, monkeypatch
    ) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        plan = [
            {
                "key": "x1",
                "title": "Cast a spell",
                "description": "Produce spell.txt",
                "task_type": "coding",
                "agent": "wizard",
                "owns": ["spell.txt"],
            }
        ]
        scripted = ScriptedProvider(plan=plan)
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
        # Rejected, retried with the problem fed back, then refused outright —
        # never persisted with a desk that does not exist.
        assert workflow.status == WorkflowStatus.FAILED
        assert _planner_calls(scripted) == 2
        # The second attempt was told exactly what was wrong with the first.
        assert 'unknown agent "wizard"' in scripted.planner_messages[1]
        assert await _tasks(factory, workflow_id) == {}


class FakeSandbox:
    """Answers like the Docker sandbox, from a script of results."""

    def __init__(self, results: list[Any] | None = None, *, available: bool = True):
        self.results = list(results or [])
        self._available = available
        self.runs: list[tuple[list[tuple[str, str]], Any]] = []

    async def available(self) -> tuple[bool, str]:
        return (True, "fake") if self._available else (False, "'docker' is not installed")

    async def run(self, files, spec):
        from app.services.sandbox.docker import SandboxResult

        self.runs.append((list(files), spec))
        if self.results and self.results[0] == "unmounted":
            self.results.pop(0)
            return SandboxResult(None, "workspace not mounted", 3, stage="setup")
        code = self.results.pop(0) if self.results else 0
        output = (
            "1 passed" if code == 0 else "FAILED test_app.py::test_add - assert 3 == 4"
        )
        return SandboxResult(exit_code=code, output=output, duration_ms=5)


CODE_AND_TESTS = [
    {
        "key": "c1",
        "title": "Write the module",
        "description": "Produce app.py with add(a, b)",
        "task_type": "coding",
        "depends_on": [],
        "owns": ["app.py"],
    },
    {
        "key": "q1",
        "title": "Test the module",
        "description": "Produce test_app.py",
        "task_type": "testing",
        "depends_on": ["c1"],
        "owns": ["test_app.py"],
    },
]
PYTEST = {
    "runtime": "python",
    "install": "pip install pytest",
    "command": "python -m pytest -q",
}


class TestTestExecution:
    async def _run(self, engine, monkeypatch, sandbox: FakeSandbox):
        from app.services.orchestrator.engine import ExecutionEngine

        scripted = ScriptedProvider(plan=CODE_AND_TESTS, test_run=PYTEST)
        monkeypatch.setattr(
            "app.services.orchestrator.engine.get_sandbox", lambda: sandbox
        )
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        await ExecutionEngine(workflow_id).run()
        async with factory() as session:
            calls = list(await session.scalars(select(ToolCall)))
        return scripted, await _tasks(factory, workflow_id), calls

    async def test_passing_tests_are_executed_and_shown_to_the_reviewer(
        self, engine, monkeypatch
    ) -> None:
        sandbox = FakeSandbox([0])
        scripted, tasks, calls = await self._run(engine, monkeypatch, sandbox)

        assert tasks["q1"].status == TaskStatus.COMPLETED, tasks["q1"].error
        # The tests ran against the whole workspace: the code AND the tests.
        files, spec = sandbox.runs[0]
        assert {path for path, _ in files} == {"app.py", "test_app.py"}
        assert spec.command == "python -m pytest -q"
        assert [c.tool_name for c in calls] == ["run_tests"] and calls[0].succeeded
        assert any("TEST RUN (executed, exit 0)" in r for r in scripted.reviewer_inputs)

    async def test_a_failure_is_fed_back_and_the_retry_can_fix_it(
        self, engine, monkeypatch
    ) -> None:
        sandbox = FakeSandbox([1, 0])
        scripted, tasks, calls = await self._run(engine, monkeypatch, sandbox)

        assert tasks["q1"].status == TaskStatus.COMPLETED
        assert tasks["q1"].retry_count == 1
        assert [c.succeeded for c in calls] == [False, True]
        # The second attempt saw the real failure output.
        tester_turns = [
            message
            for system, message in zip(
                scripted.worker_prompts, scripted.worker_messages, strict=True
            )
            if "Your tests are executed" in system
        ]
        assert len(tester_turns) == 2
        assert "assert 3 == 4" not in tester_turns[0]
        assert "assert 3 == 4" in tester_turns[1]

    async def test_tests_that_keep_failing_fail_the_task(
        self, engine, monkeypatch
    ) -> None:
        sandbox = FakeSandbox([1, 1, 1, 1])
        _, tasks, calls = await self._run(engine, monkeypatch, sandbox)

        assert tasks["q1"].status == TaskStatus.FAILED
        assert "Tests failed" in (tasks["q1"].error or "")
        assert len(calls) == 4 and not any(c.succeeded for c in calls)

    async def test_without_a_container_runtime_tests_are_reported_not_run(
        self, engine, monkeypatch
    ) -> None:
        sandbox = FakeSandbox(available=False)
        _, tasks, calls = await self._run(engine, monkeypatch, sandbox)

        # Missing infrastructure is not the same as failing tests.
        assert tasks["q1"].status == TaskStatus.COMPLETED
        assert sandbox.runs == []
        assert "Tests not run" in (calls[0].error or "")

    async def test_a_broken_mount_is_not_blamed_on_the_tests(
        self, engine, monkeypatch
    ) -> None:
        sandbox = FakeSandbox(["unmounted"])
        _, tasks, calls = await self._run(engine, monkeypatch, sandbox)

        # Not fed back as a test failure, not retried: reported as not run.
        assert tasks["q1"].status == TaskStatus.COMPLETED
        assert tasks["q1"].retry_count == 0
        assert "Tests not run" in (calls[0].error or "")


class TestConversationAndMemory:
    async def test_a_follow_up_is_planned_with_the_thread_and_what_was_built(
        self, engine, monkeypatch
    ) -> None:
        """Turn two of a conversation must know what turn one did."""
        from app.models.entities import Conversation, Memory, Message
        from app.services.orchestrator.engine import ExecutionEngine

        scripted = ScriptedProvider()
        first_id, factory = await _workspace(engine, monkeypatch, scripted)

        # What POST /workflows does: open a thread, record the request.
        async with factory() as session:
            first = await session.get(Workflow, first_id)
            thread = Conversation(project_id=first.project_id, title="site")
            session.add(thread)
            await session.flush()
            first.conversation_id = thread.id
            session.add(
                Message(
                    conversation_id=thread.id,
                    role="user",
                    content=first.goal,
                    meta={"workflow_id": str(first_id)},
                )
            )
            await session.commit()
            thread_id, project_id = thread.id, first.project_id

        await ExecutionEngine(first_id).run()

        async with factory() as session:
            messages = list(
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == thread_id)
                    .order_by(Message.created_at)
                )
            )
            memories = list(await session.scalars(select(Memory)))
        # The outcome was written back to the thread and to project memory.
        assert [m.role for m in messages] == ["user", "assistant"]
        assert "one-page site" in messages[1].content
        assert len(memories) == 1
        assert "index.html" in memories[0].content and "styles.css" in memories[0].content

        async with factory() as session:
            follow_up = Workflow(
                project_id=project_id,
                conversation_id=thread_id,
                goal="Now add a dark mode toggle to the site",
                max_cost_usd=100.0,
                max_tokens=10_000_000,
                max_seconds=600,
            )
            session.add(follow_up)
            await session.flush()
            session.add(
                Message(
                    conversation_id=thread_id,
                    role="user",
                    content=follow_up.goal,
                    meta={"workflow_id": str(follow_up.id)},
                )
            )
            await session.commit()
            follow_up_id = follow_up.id

        await ExecutionEngine(follow_up_id).run()

        briefing = scripted.commander_messages[1]
        assert briefing.startswith("## REQUEST\nNow add a dark mode toggle")
        assert "## EARLIER IN THIS CONVERSATION" in briefing
        assert "USER: Build a one-page site" in briefing
        assert "## WHAT THIS PROJECT ALREADY HAS" in briefing
        assert "index.html" in briefing
        # Its own request is not echoed back to it as history.
        assert briefing.count("Now add a dark mode toggle") == 1

    async def test_a_failed_run_is_recorded_in_the_thread_but_not_remembered(
        self, engine, monkeypatch
    ) -> None:
        from app.models.entities import Conversation, Memory, Message
        from app.services.orchestrator.engine import ExecutionEngine

        scripted = ScriptedProvider(
            plan=[
                {
                    "key": "x",
                    "title": "t",
                    "description": "d",
                    "agent": "nobody",
                    "owns": ["x.txt"],
                }
            ]
        )
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            thread = Conversation(project_id=workflow.project_id)
            session.add(thread)
            await session.flush()
            workflow.conversation_id = thread.id
            await session.commit()

        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            replies = list(
                await session.scalars(select(Message).where(Message.role == "assistant"))
            )
            assert await session.scalar(select(func.count()).select_from(Memory)) == 0
        assert len(replies) == 1 and replies[0].content.startswith("This run failed:")


class TestTaskCost:
    async def test_each_task_carries_what_its_calls_cost(
        self, engine, monkeypatch
    ) -> None:
        """Task cost used to be stored as $0 for every task."""
        from app.services.orchestrator.engine import ExecutionEngine

        workflow_id, factory = await _workspace(engine, monkeypatch, ScriptedProvider())
        await ExecutionEngine(workflow_id).run()

        tasks = await _tasks(factory, workflow_id)
        async with factory() as session:
            for task in tasks.values():
                calls = list(
                    await session.scalars(
                        select(Execution).where(Execution.task_id == task.id)
                    )
                )
                # The agent's call and the review of it both count.
                assert {c.role for c in calls} == {"frontend", "reviewer"}
                assert task.cost_usd > 0
                assert task.cost_usd == pytest.approx(
                    sum(c.estimated_cost for c in calls)
                )
                assert task.input_tokens == sum(c.input_tokens for c in calls)


ONE_PAGE = [
    {
        "key": "t1",
        "title": "Write the page",
        "description": "Produce index.html",
        "task_type": "frontend",
        "agent": "frontend",
        "owns": ["index.html"],
    }
]


class TestToolLoop:
    async def test_an_agent_uses_tools_then_answers(self, engine, monkeypatch) -> None:
        """Tool calls run, results go back to the agent, files written by a
        tool count as produced — and every call is on the record."""
        from app.services.orchestrator.engine import ExecutionEngine

        scripted = ScriptedProvider(
            plan=ONE_PAGE,
            worker_replies=[
                {"tool_calls": [
                    {"tool": "list_files", "arguments": {}},
                    {"tool": "write_file", "arguments": {"path": "index.html", "content": "<h1>hi</h1>"}},
                ]},
                {"tool_calls": [{"tool": "read_file", "arguments": {"path": "index.html"}}]},
                {"artifacts": [], "summary": "Wrote index.html with write_file"},
            ],
        )  # fmt: skip
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        await ExecutionEngine(workflow_id).run()

        task = (await _tasks(factory, workflow_id))["t1"]
        async with factory() as session:
            calls = list(
                await session.scalars(select(ToolCall).order_by(ToolCall.created_at))
            )
            content = await session.scalar(
                select(Artifact.content).where(Artifact.path == "index.html")
            )

        assert task.status == TaskStatus.COMPLETED, task.error
        assert content == "<h1>hi</h1>"
        assert [c.tool_name for c in calls] == ["list_files", "write_file", "read_file"]
        assert all(c.succeeded for c in calls)
        # The agent saw the results of each round, including its own file.
        assert scripted.worker_messages[1].startswith("## TOOL RESULTS (round 1 of")
        assert "<h1>hi</h1>" in scripted.worker_messages[2]
        # The tools were described to it, and only the ones it may use.
        assert "## TOOLS" in scripted.worker_prompts[0]
        assert "- read_file:" in scripted.worker_prompts[0]
        assert "- run_sql:" not in scripted.worker_prompts[0]  # frontend has no SQL

    async def test_a_tool_outside_the_agents_grant_is_refused(
        self, engine, monkeypatch
    ) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        scripted = ScriptedProvider(
            plan=ONE_PAGE,
            worker_replies=[
                {
                    "tool_calls": [
                        {"tool": "run_sql", "arguments": {"sql": "drop table users"}}
                    ]
                },
                {
                    "artifacts": [{"path": "index.html", "content": "<p>ok</p>"}],
                    "summary": "done",
                },
            ],
        )
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            call = await session.scalar(select(ToolCall))
        assert not call.succeeded
        assert "not one of your tools" in call.error
        assert "not one of your tools" in scripted.worker_messages[1]

    async def test_a_writes_outside_ownership_are_refused_mid_turn(
        self, engine, monkeypatch
    ) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        scripted = ScriptedProvider(
            plan=ONE_PAGE,
            worker_replies=[
                {"tool_calls": [{"tool": "write_file", "arguments": {"path": "server.py", "content": "x"}}]},
                {"artifacts": [{"path": "index.html", "content": "<p>ok</p>"}], "summary": "done"},
            ],
        )  # fmt: skip
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            paths = set(await session.scalars(select(Artifact.path)))
        assert paths == {"index.html"}
        assert "server.py is not owned by t1" in scripted.worker_messages[1]

    async def test_an_agent_that_will_not_stop_is_told_to_finish(
        self, engine, monkeypatch
    ) -> None:
        from app.core.config import settings
        from app.services.orchestrator.engine import ExecutionEngine

        monkeypatch.setattr(settings, "max_tool_rounds", 2)
        listing = {"tool_calls": [{"tool": "list_files", "arguments": {}}]}
        scripted = ScriptedProvider(
            plan=ONE_PAGE,
            worker_replies=[
                listing,
                listing,
                listing,
                {
                    "artifacts": [{"path": "index.html", "content": "<p>ok</p>"}],
                    "summary": "done",
                },
            ],
        )
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            ran = await session.scalar(select(func.count()).select_from(ToolCall))
        assert ran == 2  # the third request was not run
        assert "You have used all 2 rounds" in scripted.worker_messages[3]
        assert (await _tasks(factory, workflow_id))["t1"].status == TaskStatus.COMPLETED


class TestCrashes:
    async def test_a_crash_inside_a_task_is_recorded_not_lost(
        self, engine, monkeypatch
    ) -> None:
        """Regression: gather(return_exceptions=True) swallowed task crashes,
        leaving the task RUNNING forever while the workflow reported success."""
        from app.services.orchestrator.engine import ExecutionEngine

        workflow_id, factory = await _workspace(engine, monkeypatch, ScriptedProvider())

        async def explode(self, session, task_id, *args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(ExecutionEngine, "_execute_task", explode)
        await ExecutionEngine(workflow_id).run()

        tasks = await _tasks(factory, workflow_id)
        assert tasks["t1"].status == TaskStatus.FAILED
        assert tasks["t1"].error == "Internal error: RuntimeError: boom"


class TestTeam:
    async def _setup(self, engine, monkeypatch, scripted, team):
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            workflow.team = team
            await session.commit()
        return workflow_id, factory

    async def test_the_planner_only_sees_the_chosen_engineers(
        self, engine, monkeypatch
    ) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        scripted = ScriptedProvider(plan=ONE_PAGE)
        workflow_id, _ = await self._setup(engine, monkeypatch, scripted, ["frontend"])
        await ExecutionEngine(workflow_id).run()

        prompt = scripted.planner_prompts[0]
        assert "- frontend:" in prompt
        assert "- backend:" not in prompt and "- testing:" not in prompt
        assert "These are the ONLY engineers on this job" in prompt
        # The final write-up knows the scope was the user's choice.
        assert "The user limited this job to: frontend" in scripted.synthesizer_inputs[0]

    async def test_work_for_someone_off_the_team_is_sent_back(
        self, engine, monkeypatch
    ) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        off_team = [{**ONE_PAGE[0], "agent": "backend"}]
        scripted = ScriptedProvider(plan=off_team)
        workflow_id, factory = await self._setup(
            engine, monkeypatch, scripted, ["frontend"]
        )
        await ExecutionEngine(workflow_id).run()

        assert 'unknown agent "backend"' in scripted.planner_messages[1]
        assert await _tasks(factory, workflow_id) == {}

    async def test_with_a_team_every_task_must_name_its_engineer(
        self, engine, monkeypatch
    ) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        unassigned = [{k: v for k, v in ONE_PAGE[0].items() if k != "agent"}]
        scripted = ScriptedProvider(plan=unassigned)
        workflow_id, _ = await self._setup(engine, monkeypatch, scripted, ["frontend"])
        await ExecutionEngine(workflow_id).run()

        assert 'task "t1" names no agent' in scripted.planner_messages[1]

    async def test_without_a_team_everyone_is_available(
        self, engine, monkeypatch
    ) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        scripted = ScriptedProvider()
        workflow_id, _ = await _workspace(engine, monkeypatch, scripted)
        await ExecutionEngine(workflow_id).run()

        prompt = scripted.planner_prompts[0]
        assert "- backend:" in prompt and "- testing:" in prompt
        assert "ONLY engineers" not in prompt


class TestStuckTasks:
    async def test_a_task_that_never_leaves_the_queue_cannot_spin_forever(
        self, engine, monkeypatch
    ) -> None:
        """Whatever the cause, a task that returns still PENDING used to be
        dispatched again, forever, until the time budget killed the run."""
        from app.services.orchestrator.engine import ExecutionEngine

        workflow_id, factory = await _workspace(engine, monkeypatch, ScriptedProvider())

        async def does_nothing(self, session, task_id, *args, **kwargs):
            return None

        monkeypatch.setattr(ExecutionEngine, "_execute_task", does_nothing)
        await asyncio.wait_for(ExecutionEngine(workflow_id).run(), timeout=30)

        tasks = await _tasks(factory, workflow_id)
        assert tasks["t1"].status == TaskStatus.FAILED
        assert "Never left the queue" in tasks["t1"].error
