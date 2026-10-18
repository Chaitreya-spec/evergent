"""API integration tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.entities import User
from app.models.enums import UserRole

pytestmark = pytest.mark.integration


class TestHealth:
    async def test_health_is_open(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_ready_reports_dependencies(self, client: AsyncClient) -> None:
        body = (await client.get("/api/v1/ready")).json()
        assert body["database"] is True
        assert "providers" in body


class TestAuth:
    async def test_register_returns_tokens(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "a@example.com", "password": "long-enough-password"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["access_token"] and body["refresh_token"]

    async def test_duplicate_email_rejected(self, client: AsyncClient) -> None:
        payload = {"email": "dupe@example.com", "password": "long-enough-password"}
        assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201
        second = await client.post("/api/v1/auth/register", json=payload)
        assert second.status_code == 409

    async def test_short_password_rejected(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/register", json={"email": "s@example.com", "password": "short"}
        )
        assert response.status_code == 422

    async def test_login_then_me(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/auth/register",
            json={"email": "login@example.com", "password": "long-enough-password"},
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@example.com", "password": "long-enough-password"},
        )
        assert login.status_code == 200

        token = login.json()["access_token"]
        me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me.json()["email"] == "login@example.com"

    async def test_wrong_password_is_indistinguishable_from_unknown_user(
        self, client: AsyncClient
    ) -> None:
        await client.post(
            "/api/v1/auth/register",
            json={"email": "real@example.com", "password": "long-enough-password"},
        )
        wrong = await client.post(
            "/api/v1/auth/login",
            json={"email": "real@example.com", "password": "wrong-password-here"},
        )
        missing = await client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": "wrong-password-here"},
        )
        # Identical responses, or the endpoint enumerates accounts.
        assert wrong.status_code == missing.status_code == 401
        assert wrong.json()["message"] == missing.json()["message"]

    async def test_refresh_issues_new_access_token(self, client: AsyncClient) -> None:
        registered = await client.post(
            "/api/v1/auth/register",
            json={"email": "refresh@example.com", "password": "long-enough-password"},
        )
        refresh_token = registered.json()["refresh_token"]
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert response.status_code == 200
        assert response.json()["access_token"]

    async def test_access_token_rejected_at_refresh(self, client: AsyncClient) -> None:
        registered = await client.post(
            "/api/v1/auth/register",
            json={"email": "mix@example.com", "password": "long-enough-password"},
        )
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": registered.json()["access_token"]},
        )
        assert response.status_code == 401


class TestAuthorization:
    async def test_protected_route_requires_token(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/projects")).status_code == 401

    async def test_garbage_token_rejected(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/v1/projects", headers={"Authorization": "Bearer not-a-token"}
        )
        assert response.status_code == 401

    async def test_cannot_read_another_users_project(self, client: AsyncClient) -> None:
        first = await client.post(
            "/api/v1/auth/register",
            json={"email": "owner@example.com", "password": "long-enough-password"},
        )
        owner_token = first.json()["access_token"]
        project = await client.post(
            "/api/v1/projects",
            json={"name": "Private"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        project_id = project.json()["id"]

        second = await client.post(
            "/api/v1/auth/register",
            json={"email": "intruder@example.com", "password": "long-enough-password"},
        )
        intruder_token = second.json()["access_token"]

        # Someone else's project must read as absent, not forbidden — 403
        # would confirm it exists.
        response = await client.get(
            f"/api/v1/projects/{project_id}",
            headers={"Authorization": f"Bearer {intruder_token}"},
        )
        assert response.status_code == 404

    async def test_creating_a_provider_requires_admin(
        self, auth_client: AsyncClient
    ) -> None:
        response = await auth_client.post(
            "/api/v1/providers",
            json={"name": "sneaky", "display_name": "Sneaky", "api_key": "sk-test"},
        )
        assert response.status_code == 403

    async def test_refreshing_routing_stats_requires_admin(
        self, auth_client: AsyncClient
    ) -> None:
        # Rewriting the figures the router scores on is not a member action.
        response = await auth_client.post("/api/v1/models/refresh-stats")
        assert response.status_code == 403

    async def test_admin_can_refresh_routing_stats(
        self, auth_client: AsyncClient, session
    ) -> None:
        user = await session.scalar(select(User))
        user.role = UserRole.ADMIN
        await session.commit()

        response = await auth_client.post("/api/v1/models/refresh-stats")
        assert response.status_code == 200
        # No executions recorded yet, so nothing has evidence to update.
        assert response.json() == []


class TestProjects:
    async def test_create_and_list(self, auth_client: AsyncClient) -> None:
        created = await auth_client.post(
            "/api/v1/projects", json={"name": "Travel site", "description": "demo"}
        )
        assert created.status_code == 201

        listed = await auth_client.get("/api/v1/projects")
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["name"] == "Travel site"

    async def test_missing_project_is_404(self, auth_client: AsyncClient) -> None:
        response = await auth_client.get(f"/api/v1/projects/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_delete(self, auth_client: AsyncClient) -> None:
        created = await auth_client.post("/api/v1/projects", json={"name": "Temp"})
        project_id = created.json()["id"]
        assert (await auth_client.delete(f"/api/v1/projects/{project_id}")).status_code == 204
        assert (await auth_client.get(f"/api/v1/projects/{project_id}")).status_code == 404


class TestWorkflows:
    async def _project(self, client: AsyncClient) -> str:
        created = await client.post("/api/v1/projects", json={"name": "P"})
        return created.json()["id"]

    async def test_create_without_autostart(self, auth_client: AsyncClient) -> None:
        project_id = await self._project(auth_client)
        response = await auth_client.post(
            "/api/v1/workflows",
            json={
                "project_id": project_id,
                "goal": "Build a landing page with a contact form",
                "autostart": False,
            },
        )
        assert response.status_code == 201
        assert response.json()["status"] == "pending"

    async def test_goal_too_short_rejected(self, auth_client: AsyncClient) -> None:
        project_id = await self._project(auth_client)
        response = await auth_client.post(
            "/api/v1/workflows",
            json={"project_id": project_id, "goal": "hi", "autostart": False},
        )
        assert response.status_code == 422

    async def test_budget_is_copied_at_creation(self, auth_client: AsyncClient) -> None:
        created = await auth_client.post(
            "/api/v1/projects", json={"name": "Capped", "max_cost_usd": 2.5}
        )
        workflow = await auth_client.post(
            "/api/v1/workflows",
            json={
                "project_id": created.json()["id"],
                "goal": "Build something within budget",
                "autostart": False,
            },
        )
        assert workflow.json()["max_cost_usd"] == 2.5

    async def test_cancel_then_cannot_cancel_again(
        self, auth_client: AsyncClient
    ) -> None:
        project_id = await self._project(auth_client)
        workflow = await auth_client.post(
            "/api/v1/workflows",
            json={
                "project_id": project_id,
                "goal": "Build a thing that will be cancelled",
                "autostart": False,
            },
        )
        workflow_id = workflow.json()["id"]

        first = await auth_client.post(f"/api/v1/workflows/{workflow_id}/cancel")
        assert first.status_code == 200
        assert first.json()["status"] == "cancelled"

        second = await auth_client.post(f"/api/v1/workflows/{workflow_id}/cancel")
        assert second.status_code == 409

    async def test_pause_and_resume(self, auth_client: AsyncClient) -> None:
        project_id = await self._project(auth_client)
        workflow = await auth_client.post(
            "/api/v1/workflows",
            json={
                "project_id": project_id,
                "goal": "Build a thing that will be paused",
                "autostart": False,
            },
        )
        workflow_id = workflow.json()["id"]

        paused = await auth_client.post(f"/api/v1/workflows/{workflow_id}/pause")
        assert paused.json()["status"] == "paused"

        resumed = await auth_client.post(f"/api/v1/workflows/{workflow_id}/resume")
        assert resumed.json()["status"] in ("pending", "planning", "running")

    async def test_resume_requires_paused(self, auth_client: AsyncClient) -> None:
        project_id = await self._project(auth_client)
        workflow = await auth_client.post(
            "/api/v1/workflows",
            json={
                "project_id": project_id,
                "goal": "Build a thing not yet paused",
                "autostart": False,
            },
        )
        response = await auth_client.post(
            f"/api/v1/workflows/{workflow.json()['id']}/resume"
        )
        assert response.status_code == 409

    async def test_tasks_and_artifacts_start_empty(
        self, auth_client: AsyncClient
    ) -> None:
        project_id = await self._project(auth_client)
        workflow = await auth_client.post(
            "/api/v1/workflows",
            json={
                "project_id": project_id,
                "goal": "Build a thing with no tasks yet",
                "autostart": False,
            },
        )
        workflow_id = workflow.json()["id"]
        assert (await auth_client.get(f"/api/v1/workflows/{workflow_id}/tasks")).json() == []
        assert (
            await auth_client.get(f"/api/v1/workflows/{workflow_id}/artifacts")
        ).json() == []


class TestCatalog:
    async def test_agents_are_listed(self, auth_client: AsyncClient) -> None:
        agents = (await auth_client.get("/api/v1/agents")).json()
        names = {a["name"] for a in agents}
        assert {"planner", "reviewer", "synthesizer", "frontend"} <= names

    async def test_usage_starts_at_zero(self, auth_client: AsyncClient) -> None:
        usage = (await auth_client.get("/api/v1/usage")).json()
        assert usage["total_cost_usd"] == 0
        assert usage["call_count"] == 0


class TestSecurityHeaders:
    async def test_headers_present(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-Request-ID"]

    async def test_internal_errors_do_not_leak_detail(
        self, client: AsyncClient
    ) -> None:
        # A malformed UUID reaches the router as a validation error, which must
        # be reported without a stack trace.
        response = await client.get("/api/v1/projects/not-a-uuid")
        assert response.status_code in (401, 422)
        assert "Traceback" not in response.text


class TestTaskManagement:
    async def _workflow_with_task(self, client: AsyncClient) -> tuple[str, str]:
        project = await client.post("/api/v1/projects", json={"name": "Tasks"})
        workflow = await client.post(
            "/api/v1/workflows",
            json={
                "project_id": project.json()["id"],
                "goal": "Build a thing with a manual task",
                "autostart": False,
            },
        )
        workflow_id = workflow.json()["id"]
        task = await client.post(
            "/api/v1/tasks",
            json={
                "workflow_id": workflow_id,
                "key": "t1",
                "title": "Write the markup",
                "description": "Produce index.html",
                "task_type": "frontend",
                "owns": ["index.html"],
            },
        )
        assert task.status_code == 201, task.text
        return workflow_id, task.json()["id"]

    async def test_create_and_read(self, auth_client: AsyncClient) -> None:
        _, task_id = await self._workflow_with_task(auth_client)
        response = await auth_client.get(f"/api/v1/tasks/{task_id}")
        assert response.status_code == 200
        assert response.json()["owns"] == ["index.html"]
        # The agent is derived from the task type, not supplied by the caller.
        assert response.json()["agent_name"] == "frontend"

    async def test_duplicate_key_rejected(self, auth_client: AsyncClient) -> None:
        workflow_id, _ = await self._workflow_with_task(auth_client)
        duplicate = await auth_client.post(
            "/api/v1/tasks",
            json={
                "workflow_id": workflow_id,
                "key": "t1",
                "title": "Clash",
                "description": "same key",
                "task_type": "backend",
            },
        )
        assert duplicate.status_code == 409

    async def test_dependencies_resolve_by_key(self, auth_client: AsyncClient) -> None:
        workflow_id, _ = await self._workflow_with_task(auth_client)
        second = await auth_client.post(
            "/api/v1/tasks",
            json={
                "workflow_id": workflow_id,
                "key": "t2",
                "title": "Style it",
                "description": "Produce styles.css",
                "task_type": "frontend",
                "depends_on": ["t1"],
                "owns": ["styles.css"],
            },
        )
        assert second.json()["depends_on"] == ["t1"]

    async def test_patch_amends_instructions(self, auth_client: AsyncClient) -> None:
        _, task_id = await self._workflow_with_task(auth_client)
        patched = await auth_client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"description": "Produce index.html with a hero section"},
        )
        assert patched.status_code == 200
        assert "hero section" in patched.json()["description"]
        # An unnamed field must be left alone.
        assert patched.json()["title"] == "Write the markup"

    async def test_cannot_edit_a_finished_task(self, auth_client: AsyncClient) -> None:
        _, task_id = await self._workflow_with_task(auth_client)
        await auth_client.post(f"/api/v1/tasks/{task_id}/cancel")
        response = await auth_client.patch(
            f"/api/v1/tasks/{task_id}", json={"title": "too late"}
        )
        assert response.status_code == 409

    async def test_cancel_then_retry(self, auth_client: AsyncClient) -> None:
        _, task_id = await self._workflow_with_task(auth_client)
        cancelled = await auth_client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert cancelled.json()["status"] == "cancelled"

        retried = await auth_client.post(f"/api/v1/tasks/{task_id}/retry")
        assert retried.json()["status"] == "pending"
        assert retried.json()["error"] is None

    async def test_cancel_twice_rejected(self, auth_client: AsyncClient) -> None:
        _, task_id = await self._workflow_with_task(auth_client)
        await auth_client.post(f"/api/v1/tasks/{task_id}/cancel")
        second = await auth_client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert second.status_code == 409

    async def test_another_user_cannot_reach_the_task(
        self, client: AsyncClient
    ) -> None:
        owner = await client.post(
            "/api/v1/auth/register",
            json={"email": "towner@example.com", "password": "long-enough-password"},
        )
        client.headers["Authorization"] = f"Bearer {owner.json()['access_token']}"
        _, task_id = await TestTaskManagement()._workflow_with_task(client)

        intruder = await client.post(
            "/api/v1/auth/register",
            json={"email": "tintruder@example.com", "password": "long-enough-password"},
        )
        client.headers["Authorization"] = f"Bearer {intruder.json()['access_token']}"
        assert (await client.get(f"/api/v1/tasks/{task_id}")).status_code == 404


class TestArtifactManagement:
    async def _artifact(self, auth_client: AsyncClient, session) -> tuple[str, str]:
        """A real artifact, created through the API then written directly."""
        from app.models.entities import Artifact

        project = await auth_client.post("/api/v1/projects", json={"name": "Files"})
        workflow = await auth_client.post(
            "/api/v1/workflows",
            json={
                "project_id": project.json()["id"],
                "goal": "Build a thing that produces a file",
                "autostart": False,
            },
        )
        workflow_id = workflow.json()["id"]

        artifact = Artifact(
            workflow_id=uuid.UUID(workflow_id),
            path="src/index.html",
            content="<h1>hello</h1>",
            content_type="text/html",
        )
        session.add(artifact)
        await session.commit()
        return workflow_id, str(artifact.id)

    async def test_listed_on_its_workflow(self, auth_client, session) -> None:
        workflow_id, _ = await self._artifact(auth_client, session)
        listed = await auth_client.get(f"/api/v1/workflows/{workflow_id}/artifacts")
        assert [a["path"] for a in listed.json()] == ["src/index.html"]

    async def test_read_one(self, auth_client, session) -> None:
        _, artifact_id = await self._artifact(auth_client, session)
        response = await auth_client.get(f"/api/v1/artifacts/{artifact_id}")
        assert response.status_code == 200
        assert response.json()["content"] == "<h1>hello</h1>"

    async def test_download_returns_the_raw_file(self, auth_client, session) -> None:
        _, artifact_id = await self._artifact(auth_client, session)
        response = await auth_client.get(f"/api/v1/artifacts/{artifact_id}/download")
        assert response.status_code == 200
        # The raw bytes, not a JSON envelope, named after the file.
        assert response.text == "<h1>hello</h1>"
        assert "index.html" in response.headers["content-disposition"]

    async def test_delete(self, auth_client, session) -> None:
        _, artifact_id = await self._artifact(auth_client, session)
        assert (
            await auth_client.delete(f"/api/v1/artifacts/{artifact_id}")
        ).status_code == 204
        assert (
            await auth_client.get(f"/api/v1/artifacts/{artifact_id}")
        ).status_code == 404

    async def test_missing_artifact_is_404(self, auth_client: AsyncClient) -> None:
        assert (
            await auth_client.get(f"/api/v1/artifacts/{uuid.uuid4()}")
        ).status_code == 404

    async def test_download_requires_ownership(self, auth_client: AsyncClient) -> None:
        response = await auth_client.get(
            f"/api/v1/artifacts/{uuid.uuid4()}/download"
        )
        assert response.status_code == 404
