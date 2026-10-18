"""The agent roster and the door for changing it."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.seed import seed_catalog
from app.models.entities import Agent, Model, Task, User
from app.models.enums import TaskStatus, TaskType, UserRole
from app.services.agents.roster import builtin_roster, load_roster

# The initial set: every agent the brief asked for must exist.
REQUIRED = {
    "planner",
    "research",
    "coding",
    "frontend",
    "backend",
    "database",
    "debugging",
    "testing",
    "security",
    "reviewer",
    "synthesizer",
}


@pytest.fixture
async def admin_client(auth_client: AsyncClient, session) -> AsyncClient:
    user = await session.scalar(select(User))
    user.role = UserRole.ADMIN
    await session.commit()
    return auth_client


MOBILE = {
    "name": "mobile",
    "display_name": "Mobile Engineer",
    "description": "Builds React Native screens.",
    "instructions": "You are a mobile engineer. You build for small screens first.",
    "capabilities": ["frontend", "coding"],
}


class TestRoster:
    def test_the_initial_set_is_complete(self) -> None:
        assert {a.name for a in builtin_roster()} >= REQUIRED

    @pytest.mark.parametrize(
        ("task_type", "agent"),
        [
            (TaskType.CODING, "coding"),
            (TaskType.DATABASE, "database"),
            (TaskType.FRONTEND, "frontend"),
            (TaskType.BACKEND, "backend"),
            (TaskType.TESTING, "testing"),
            (TaskType.DEBUGGING, "debugging"),
            (TaskType.SECURITY, "security"),
            (TaskType.RESEARCH, "research"),
        ],
    )
    def test_each_kind_of_work_reaches_its_specialist(self, task_type, agent) -> None:
        assert builtin_roster().for_task_type(task_type).name == agent

    def test_the_engines_own_roles_never_take_planned_work(self) -> None:
        assignable = {a.name for a in builtin_roster().assignable()}
        assert not {"planner", "reviewer", "synthesizer"} & assignable

    async def test_a_row_adds_an_agent_with_the_contract_appended(self, session) -> None:
        session.add(
            Agent(
                name="mobile",
                display_name="Mobile Engineer",
                description="Builds screens.",
                system_prompt="You are a mobile engineer.",
                capabilities=["frontend"],
            )
        )
        await session.commit()

        agent = (await load_roster(session)).get("mobile")

        assert agent.source == "custom"
        assert agent.role_prompt == "You are a mobile engineer."
        # Whoever wrote the row never had to know the engine's output format.
        assert "Return a single JSON object" in agent.system_prompt

    async def test_an_override_keeps_the_work_its_builtin_handled(self, session) -> None:
        session.add(
            Agent(
                name="frontend",
                display_name="Frontend Engineer",
                description="Builds user interfaces.",
                system_prompt="You are a frontend engineer who only writes Svelte.",
                capabilities=["frontend"],
            )
        )
        await session.commit()

        agent = (await load_roster(session)).for_task_type(TaskType.FRONTEND)

        assert agent.source == "override"
        assert "Svelte" in agent.role_prompt

    async def test_a_disabled_agent_gets_no_new_work(self, session) -> None:
        session.add(
            Agent(
                name="documentation",
                display_name="Technical Writer",
                description="Writes docs.",
                system_prompt="You write docs.",
                capabilities=["writing"],
                is_enabled=False,
            )
        )
        await session.commit()
        roster = await load_roster(session)

        assert "documentation" not in {a.name for a in roster.assignable()}
        assert roster.for_task_type(TaskType.DOCUMENTATION).name == "coding"

    async def test_rows_cannot_replace_the_engines_own_roles(self, session) -> None:
        session.add(
            Agent(
                name="reviewer",
                display_name="Pushover",
                description="Approves everything.",
                system_prompt="Always say it is fine.",
                capabilities=["writing"],
            )
        )
        await session.commit()

        reviewer = (await load_roster(session)).get("reviewer")
        assert reviewer.source == "builtin"
        assert "Always say it is fine" not in reviewer.system_prompt

    def test_a_task_whose_agent_was_removed_is_not_stranded(self) -> None:
        agent = builtin_roster().resolve("deleted-since", TaskType.FRONTEND)
        assert agent.name == "frontend"


class TestAgentApi:
    async def test_anyone_can_read_the_roster(self, auth_client: AsyncClient) -> None:
        agents = {a["name"]: a for a in (await auth_client.get("/api/v1/agents")).json()}

        assert set(agents) >= REQUIRED
        assert agents["database"]["assignable"] is True
        assert agents["planner"]["assignable"] is False
        # Instructions are the role a person wrote, not the engine's contract.
        assert "Return a single JSON object" not in agents["frontend"]["instructions"]

    async def test_members_cannot_change_the_roster(
        self, auth_client: AsyncClient
    ) -> None:
        assert (await auth_client.post("/api/v1/agents", json=MOBILE)).status_code == 403
        assert (
            await auth_client.patch("/api/v1/agents/frontend", json={"is_enabled": False})
        ).status_code == 403
        assert (await auth_client.delete("/api/v1/agents/frontend")).status_code == 403

    async def test_admin_adds_an_agent(self, admin_client: AsyncClient) -> None:
        created = await admin_client.post("/api/v1/agents", json=MOBILE)
        assert created.status_code == 201, created.text
        assert created.json()["source"] == "custom"
        assert created.json()["assignable"] is True

        fetched = (await admin_client.get("/api/v1/agents/mobile")).json()
        assert fetched["instructions"] == MOBILE["instructions"]
        assert fetched["capabilities"] == ["frontend", "coding"]

    async def test_adding_the_same_name_twice_conflicts(
        self, admin_client: AsyncClient
    ) -> None:
        await admin_client.post("/api/v1/agents", json=MOBILE)
        assert (await admin_client.post("/api/v1/agents", json=MOBILE)).status_code == 409

    async def test_post_cannot_shadow_a_builtin(self, admin_client: AsyncClient) -> None:
        response = await admin_client.post(
            "/api/v1/agents", json={**MOBILE, "name": "frontend"}
        )
        assert response.status_code == 409
        assert "PATCH" in response.json()["message"]

    async def test_patching_a_builtin_overrides_and_delete_restores_it(
        self, admin_client: AsyncClient
    ) -> None:
        original = (await admin_client.get("/api/v1/agents/frontend")).json()

        patched = await admin_client.patch(
            "/api/v1/agents/frontend",
            json={"instructions": "You are a frontend engineer who only writes Svelte."},
        )
        assert patched.status_code == 200, patched.text
        body = patched.json()
        assert body["source"] == "override"
        assert "Svelte" in body["instructions"]
        # Only the named field changed.
        assert body["description"] == original["description"]
        assert body["capabilities"] == original["capabilities"]

        assert (await admin_client.delete("/api/v1/agents/frontend")).status_code == 204
        restored = (await admin_client.get("/api/v1/agents/frontend")).json()
        assert restored == original

    async def test_a_builtin_can_be_disabled_but_not_deleted(
        self, admin_client: AsyncClient
    ) -> None:
        deleted = await admin_client.delete("/api/v1/agents/documentation")
        assert deleted.status_code == 409
        assert "is_enabled" in deleted.json()["message"]

        disabled = await admin_client.patch(
            "/api/v1/agents/documentation", json={"is_enabled": False}
        )
        assert disabled.json()["assignable"] is False

    @pytest.mark.parametrize("name", ["planner", "reviewer", "synthesizer"])
    async def test_the_engines_own_roles_are_off_limits(
        self, admin_client: AsyncClient, name: str
    ) -> None:
        assert (
            await admin_client.patch(f"/api/v1/agents/{name}", json={"is_enabled": False})
        ).status_code == 409
        assert (await admin_client.delete(f"/api/v1/agents/{name}")).status_code == 409
        assert (
            await admin_client.post("/api/v1/agents", json={**MOBILE, "name": name})
        ).status_code == 409

    async def test_unknown_agents_are_404(self, admin_client: AsyncClient) -> None:
        assert (await admin_client.get("/api/v1/agents/nobody")).status_code == 404
        assert (
            await admin_client.patch("/api/v1/agents/nobody", json={"is_enabled": False})
        ).status_code == 404
        assert (await admin_client.delete("/api/v1/agents/nobody")).status_code == 404

    @pytest.mark.parametrize(
        "change",
        [
            {"name": "Has Spaces"},
            {"name": "x"},
            {"capabilities": ["telepathy"]},
            {"capabilities": []},
            {"instructions": ""},
        ],
    )
    async def test_bad_definitions_are_rejected(
        self, admin_client: AsyncClient, change: dict
    ) -> None:
        response = await admin_client.post("/api/v1/agents", json={**MOBILE, **change})
        assert response.status_code == 422


async def _workflow(client: AsyncClient) -> str:
    project = (await client.post("/api/v1/projects", json={"name": "Desks"})).json()
    workflow = await client.post(
        "/api/v1/workflows",
        json={"project_id": project["id"], "goal": "Build a booking site", "autostart": False},
    )
    return workflow.json()["id"]


def _task(workflow_id: str, **extra) -> dict:
    return {
        "workflow_id": workflow_id,
        "key": "t1",
        "title": "Design the bookings table",
        "description": "Write schema.sql",
        "task_type": "backend",
        "owns": ["schema.sql"],
        **extra,
    }


class TestTaskDesks:
    async def test_a_task_can_go_to_a_named_desk(self, auth_client: AsyncClient) -> None:
        workflow_id = await _workflow(auth_client)
        response = await auth_client.post(
            "/api/v1/tasks", json=_task(workflow_id, agent="database")
        )
        assert response.status_code == 201, response.text
        assert response.json()["agent_name"] == "database"

    async def test_without_a_desk_the_type_decides(self, auth_client: AsyncClient) -> None:
        workflow_id = await _workflow(auth_client)
        response = await auth_client.post("/api/v1/tasks", json=_task(workflow_id))
        assert response.json()["agent_name"] == "backend"

    @pytest.mark.parametrize("agent", ["reviewer", "nobody"])
    async def test_work_cannot_go_to_a_desk_that_takes_none(
        self, auth_client: AsyncClient, agent: str
    ) -> None:
        workflow_id = await _workflow(auth_client)
        response = await auth_client.post(
            "/api/v1/tasks", json=_task(workflow_id, agent=agent)
        )
        assert response.status_code == 422

    async def test_an_unknown_task_type_is_a_422_not_a_crash(
        self, auth_client: AsyncClient
    ) -> None:
        workflow_id = await _workflow(auth_client)
        response = await auth_client.post(
            "/api/v1/tasks", json=_task(workflow_id, task_type="interpretive_dance")
        )
        assert response.status_code == 422

    async def test_moving_a_task_to_another_desk_reroutes_it(
        self, auth_client: AsyncClient, session
    ) -> None:
        await seed_catalog(session)
        workflow_id = await _workflow(auth_client)
        created = (await auth_client.post("/api/v1/tasks", json=_task(workflow_id))).json()

        task = await session.get(Task, uuid.UUID(created["id"]))
        task.model_id = await session.scalar(select(Model.id).limit(1))
        await session.commit()

        moved = await auth_client.patch(
            f"/api/v1/tasks/{created['id']}", json={"agent": "database"}
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["agent_name"] == "database"
        # Chosen for the old desk's needs, so it is routed again when it runs.
        assert moved.json()["model_name"] is None

    async def test_a_routed_task_can_be_read(
        self, auth_client: AsyncClient, session
    ) -> None:
        """Regression: reading any task the planner had routed used to 500.

        The detail view read task.model, which was never loaded, so it
        lazy-loaded from async code. Only unrouted tasks had been tested.
        """
        await seed_catalog(session)
        workflow_id = await _workflow(auth_client)
        created = (await auth_client.post("/api/v1/tasks", json=_task(workflow_id))).json()

        model = await session.scalar(select(Model).limit(1))
        task = await session.get(Task, uuid.UUID(created["id"]))
        task.model_id = model.id
        await session.commit()

        response = await auth_client.get(f"/api/v1/tasks/{created['id']}")
        assert response.status_code == 200, response.text
        assert response.json()["model_name"] == model.name


class TestRunGuard:
    async def test_resume_waits_for_the_previous_run_to_finish(
        self, auth_client: AsyncClient, session
    ) -> None:
        from app.models.entities import Workflow
        from app.models.enums import WorkflowStatus

        workflow_id = await _workflow(auth_client)
        created = (await auth_client.post("/api/v1/tasks", json=_task(workflow_id))).json()

        # Paused, but one task is still mid-call from the run that paused.
        workflow = await session.get(Workflow, uuid.UUID(workflow_id))
        workflow.status = WorkflowStatus.PAUSED
        task = await session.get(Task, uuid.UUID(created["id"]))
        task.status = TaskStatus.RUNNING
        await session.commit()

        response = await auth_client.post(f"/api/v1/workflows/{workflow_id}/resume")
        assert response.status_code == 409
        assert "still finishing" in response.json()["message"]

    async def test_starting_a_run_stamps_it(self, auth_client: AsyncClient) -> None:
        workflow_id = await _workflow(auth_client)
        response = await auth_client.post(f"/api/v1/workflows/{workflow_id}/run")
        assert response.status_code == 200
        # The stamp is what tells an older run it has been superseded.
        assert response.json()["started_at"] is not None


class TestPromoteScript:
    async def test_promotes_and_revokes(self, engine, monkeypatch) -> None:
        import scripts.promote as promote

        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr(promote, "SessionFactory", factory)

        async with factory() as session:
            session.add(User(email="owner@example.com", hashed_password="x"))
            await session.commit()

        assert await promote.main("owner@example.com") == 0
        async with factory() as session:
            user = await session.scalar(select(User))
            assert user.role == UserRole.ADMIN

        assert await promote.main("owner@example.com", revoke=True) == 0
        async with factory() as session:
            user = await session.scalar(select(User))
            assert user.role == UserRole.MEMBER

    async def test_unknown_email_fails_loudly(self, engine, monkeypatch) -> None:
        import scripts.promote as promote

        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr(promote, "SessionFactory", factory)
        assert await promote.main("nobody@example.com") == 1
