"""A workflow's life: the state machine, plan approval, idempotent creation,
and the event history a reconnecting client replays."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.errors import ConflictError
from app.models.entities import AuditLog, Workflow, WorkflowEvent
from app.models.enums import TaskStatus, WorkflowStatus
from app.services.orchestrator.state import ALLOWED, can_transition, transition
from tests.test_engine import ScriptedProvider, _planner_calls, _tasks, _workspace

S = WorkflowStatus


class TestStateMachine:
    def test_every_status_has_rules(self) -> None:
        assert set(ALLOWED) == set(WorkflowStatus)

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (S.COMPLETED, S.RUNNING),
            (S.CANCELLED, S.SYNTHESIZING),
            (S.AWAITING_APPROVAL, S.RUNNING),
            (S.PENDING, S.COMPLETED),
            (S.FAILED, S.COMPLETED),
        ],
    )
    def test_impossible_moves_are_refused(self, current, target) -> None:
        workflow = Workflow(project_id=uuid.uuid4(), goal="g", status=current)
        assert not can_transition(current, target)
        with pytest.raises(ConflictError, match="cannot become"):
            transition(workflow, target)
        assert workflow.status == current

    def test_failure_is_reachable_from_every_live_state(self) -> None:
        for status in (
            S.PENDING,
            S.PLANNING,
            S.RUNNING,
            S.VALIDATING,
            S.SYNTHESIZING,
            S.PAUSED,
        ):
            assert can_transition(status, S.FAILED), status


class TestPlanApproval:
    async def test_the_engine_stops_for_approval_then_runs_the_approved_plan(
        self, engine, monkeypatch
    ) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        scripted = ScriptedProvider()
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            workflow.requires_approval = True
            await session.commit()

        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            assert workflow.status == S.AWAITING_APPROVAL
            assert workflow.brief is not None
        tasks = await _tasks(factory, workflow_id)
        assert set(tasks) == {"t1", "t2"}
        assert all(t.status == TaskStatus.PENDING for t in tasks.values())
        # Planned, but no agent has worked on anything.
        assert scripted.worker_prompts == []

        # A person edits the plan, then approves it.
        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            task = await session.get(type(tasks["t1"]), tasks["t1"].id)
            task.description = (
                "Produce index.html with a big .hero heading — edited before approval"
            )
            workflow.approved_at = workflow.created_at
            workflow.status = S.PENDING
            await session.commit()

        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            assert workflow.status == S.COMPLETED, workflow.error
        assert _planner_calls(scripted) == 1  # the approved plan was not replaced
        assert any("edited before approval" in p for p in scripted.worker_messages)

    async def test_plan_ready_says_it_waits(self, engine, monkeypatch) -> None:
        from app.services.orchestrator.engine import ExecutionEngine

        workflow_id, factory = await _workspace(engine, monkeypatch, ScriptedProvider())
        async with factory() as session:
            (await session.get(Workflow, workflow_id)).requires_approval = True
            await session.commit()
        await ExecutionEngine(workflow_id).run()

        async with factory() as session:
            events = list(
                await session.scalars(select(WorkflowEvent).order_by(WorkflowEvent.seq))
            )
        names = [e.event for e in events]
        assert names[:2] == ["workflow_started", "planning_started"]
        ready = events[names.index("plan_ready")]
        assert ready.data == {"task_count": 2, "awaiting_approval": True}
        # Every task is announced before the plan is declared ready.
        assert names.index("plan_ready") > max(i for i, n in enumerate(names) if n == "task_created")
        assert "task_started" not in names


async def _project(client: AsyncClient) -> str:
    return (await client.post("/api/v1/projects", json={"name": "Lifecycle"})).json()[
        "id"
    ]


class TestApi:
    async def test_approve_only_a_plan_awaiting_approval(
        self, auth_client: AsyncClient
    ) -> None:
        project_id = await _project(auth_client)
        workflow = (
            await auth_client.post(
                "/api/v1/workflows",
                json={
                    "project_id": project_id,
                    "goal": "Build a todo app",
                    "autostart": False,
                },
            )
        ).json()
        response = await auth_client.post(f"/api/v1/workflows/{workflow['id']}/approve")
        assert response.status_code == 409
        assert "awaiting approval" in response.json()["message"]

    async def test_approval_runs_the_plan(
        self, auth_client: AsyncClient, session
    ) -> None:
        project_id = await _project(auth_client)
        workflow = (
            await auth_client.post(
                "/api/v1/workflows",
                json={
                    "project_id": project_id,
                    "goal": "Build a todo app",
                    "require_approval": True,
                    "autostart": False,
                },
            )
        ).json()
        assert workflow["requires_approval"] is True

        row = await session.get(Workflow, uuid.UUID(workflow["id"]))
        row.status = S.AWAITING_APPROVAL
        await session.commit()

        # Running an unapproved plan is refused; approving runs it.
        assert (
            await auth_client.post(f"/api/v1/workflows/{workflow['id']}/run")
        ).status_code == 409
        approved = await auth_client.post(f"/api/v1/workflows/{workflow['id']}/approve")
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "pending"
        assert approved.json()["approved_at"] is not None

        actions = list(await session.scalars(select(AuditLog.action)))
        assert "workflow.create" in actions and "workflow.approve" in actions

    async def test_the_same_idempotency_key_never_starts_work_twice(
        self, auth_client: AsyncClient
    ) -> None:
        project_id = await _project(auth_client)
        body = {"project_id": project_id, "goal": "Build a todo app", "autostart": False}
        headers = {"Idempotency-Key": "click-123"}

        first = await auth_client.post("/api/v1/workflows", json=body, headers=headers)
        second = await auth_client.post("/api/v1/workflows", json=body, headers=headers)
        other = await auth_client.post(
            "/api/v1/workflows", json=body, headers={"Idempotency-Key": "click-456"}
        )

        assert first.status_code == 201 and second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        assert other.json()["id"] != first.json()["id"]
        listed = (
            await auth_client.get(f"/api/v1/workflows?project_id={project_id}")
        ).json()
        assert listed["total"] == 2

    async def test_a_reconnecting_client_replays_only_what_it_missed(
        self, auth_client: AsyncClient
    ) -> None:
        project_id = await _project(auth_client)
        workflow = (
            await auth_client.post(
                "/api/v1/workflows",
                json={
                    "project_id": project_id,
                    "goal": "Build a todo app",
                    "autostart": False,
                },
            )
        ).json()
        await auth_client.post(f"/api/v1/workflows/{workflow['id']}/cancel")

        history = (
            await auth_client.get(f"/api/v1/workflows/{workflow['id']}/events")
        ).json()
        assert [e["event"] for e in history] == ["workflow_created", "workflow_cancelled"]
        assert history[0]["seq"] < history[1]["seq"]

        def parse(body: str) -> list[dict]:
            out = []
            for block in body.strip().split("\n\n"):
                fields = dict(line.split(": ", 1) for line in block.splitlines())
                out.append({**json.loads(fields["data"]), "sse_id": fields.get("id")})
            return out

        # From the start: everything, then the stream closes — the workflow is over.
        full = parse(
            (await auth_client.get(f"/api/v1/workflows/{workflow['id']}/stream")).text
        )
        assert [e["event"] for e in full] == [
            "subscribed",
            "workflow_created",
            "workflow_cancelled",
            "stream_end",
        ]
        assert full[1]["sse_id"] == str(history[0]["seq"])

        # Reconnecting after the first event: only the one it missed.
        resumed = parse(
            (
                await auth_client.get(
                    f"/api/v1/workflows/{workflow['id']}/stream",
                    headers={"Last-Event-ID": str(history[0]["seq"])},
                )
            ).text
        )
        assert [e["event"] for e in resumed] == [
            "subscribed",
            "workflow_cancelled",
            "stream_end",
        ]

        # Run again after finishing: a replay goes past the old final event.
        await auth_client.post(f"/api/v1/workflows/{workflow['id']}/run")
        await auth_client.post(f"/api/v1/workflows/{workflow['id']}/cancel")
        again = parse(
            (await auth_client.get(f"/api/v1/workflows/{workflow['id']}/stream")).text
        )
        assert [e["event"] for e in again].count("workflow_cancelled") == 2
        assert again[-1]["event"] == "stream_end"

    async def test_events_belong_to_their_owner(
        self, auth_client: AsyncClient, client
    ) -> None:
        project_id = await _project(auth_client)
        workflow = (
            await auth_client.post(
                "/api/v1/workflows",
                json={
                    "project_id": project_id,
                    "goal": "Build a todo app",
                    "autostart": False,
                },
            )
        ).json()
        other = await client.post(
            "/api/v1/auth/register",
            json={"email": "intruder@example.com", "password": "long-enough-password"},
        )
        token = other.json()["access_token"]
        for path in ("events", "stream"):
            response = await client.get(
                f"/api/v1/workflows/{workflow['id']}/{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 404, path
