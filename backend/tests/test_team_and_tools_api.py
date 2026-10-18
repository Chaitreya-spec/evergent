"""Choosing the engineers for a job, and the tools they carry."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.entities import User
from app.models.enums import UserRole


async def _project(client: AsyncClient) -> str:
    return (await client.post("/api/v1/projects", json={"name": "Team"})).json()["id"]


async def _workflow(client: AsyncClient, **extra):
    return await client.post(
        "/api/v1/workflows",
        json={"project_id": await _project(client), "goal": "Build a booking site",
              "autostart": False, **extra},
    )  # fmt: skip


class TestTeam:
    async def test_a_team_is_chosen_at_the_start(self, auth_client: AsyncClient) -> None:
        response = await _workflow(auth_client, team=["frontend", "backend", "frontend"])
        assert response.status_code == 201, response.text
        assert response.json()["team"] == ["frontend", "backend"]

    async def test_no_team_means_everyone(self, auth_client: AsyncClient) -> None:
        assert (await _workflow(auth_client)).json()["team"] is None

    @pytest.mark.parametrize("name", ["wizard", "reviewer", "planner"])
    async def test_only_real_engineers_can_be_chosen(
        self, auth_client: AsyncClient, name: str
    ) -> None:
        response = await _workflow(auth_client, team=["frontend", name])
        assert response.status_code == 422
        assert name in response.json()["message"]

    async def test_an_empty_team_is_refused(self, auth_client: AsyncClient) -> None:
        assert (await _workflow(auth_client, team=[])).status_code == 422

    async def _task(self, client: AsyncClient, workflow_id: str, **extra):
        return await client.post(
            "/api/v1/tasks",
            json={"workflow_id": workflow_id, "key": "t1", "title": "Schema",
                  "description": "Write schema.sql", "task_type": "database", **extra},
        )  # fmt: skip

    async def test_hand_added_work_stays_on_the_team(
        self, auth_client: AsyncClient
    ) -> None:
        workflow_id = (await _workflow(auth_client, team=["frontend", "backend"])).json()[
            "id"
        ]

        # Nobody on the team does database work, so it cannot land anywhere.
        unassigned = await self._task(auth_client, workflow_id)
        assert unassigned.status_code == 422
        assert (
            "Nobody on this workflow's team does database work"
            in unassigned.json()["message"]
        )

        outsider = await self._task(auth_client, workflow_id, agent="database")
        assert outsider.status_code == 422
        assert "this workflow's team" in outsider.json()["message"]

        member = await self._task(auth_client, workflow_id, agent="backend")
        assert member.status_code == 201 and member.json()["agent_name"] == "backend"

    async def test_moving_a_task_stays_on_the_team(
        self, auth_client: AsyncClient
    ) -> None:
        workflow_id = (await _workflow(auth_client, team=["frontend", "backend"])).json()[
            "id"
        ]
        task = (await self._task(auth_client, workflow_id, agent="backend")).json()

        moved = await auth_client.patch(
            f"/api/v1/tasks/{task['id']}", json={"agent": "security"}
        )
        assert moved.status_code == 422


class TestTools:
    async def test_every_tool_reports_whether_it_works(
        self, auth_client: AsyncClient
    ) -> None:
        tools = {t["name"]: t for t in (await auth_client.get("/api/v1/tools")).json()}

        assert set(tools) == {"list_files", "read_file", "write_file", "web_search",
                              "fetch_url", "run_code", "run_sql"}  # fmt: skip
        # No search key in the test environment, and the reason says how to fix it.
        assert tools["web_search"]["available"] is False
        assert "TAVILY_API_KEY" in tools["web_search"]["status"]
        assert "database" in tools["run_sql"]["agents"]
        assert "frontend" not in tools["run_sql"]["agents"]

    @pytest.fixture
    async def admin(self, auth_client: AsyncClient, session) -> AsyncClient:
        user = await session.scalar(select(User))
        user.role = UserRole.ADMIN
        await session.commit()
        return auth_client

    BASE = {
        "display_name": "Data Engineer",
        "description": "Builds pipelines.",
        "instructions": "You are a data engineer.",
        "capabilities": ["backend"],
    }

    async def test_a_new_agent_gets_its_chosen_tools_plus_files(
        self, admin: AsyncClient
    ) -> None:
        created = await admin.post(
            "/api/v1/agents", json={**self.BASE, "name": "data", "tools": ["run_sql"]}
        )
        assert created.status_code == 201, created.text
        assert created.json()["allowed_tools"] == [
            "list_files",
            "read_file",
            "write_file",
            "run_sql",
        ]

    async def test_a_new_agent_defaults_to_the_web(self, admin: AsyncClient) -> None:
        created = await admin.post("/api/v1/agents", json={**self.BASE, "name": "data"})
        assert "web_search" in created.json()["allowed_tools"]
        assert "run_code" not in created.json()["allowed_tools"]

    async def test_unknown_tools_are_refused(self, admin: AsyncClient) -> None:
        created = await admin.post(
            "/api/v1/agents", json={**self.BASE, "name": "data", "tools": ["rm_rf"]}
        )
        assert created.status_code == 422 and "rm_rf" in created.json()["message"]

    async def test_a_builtins_tools_can_be_changed(self, admin: AsyncClient) -> None:
        patched = await admin.patch(
            "/api/v1/agents/frontend", json={"tools": ["run_code"]}
        )
        assert patched.json()["allowed_tools"] == [
            "list_files",
            "read_file",
            "write_file",
            "run_code",
        ]
