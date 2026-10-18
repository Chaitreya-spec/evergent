"""Where runs execute, and getting the work back out."""

from __future__ import annotations

import io
import uuid
import zipfile

from httpx import AsyncClient

from app.core.config import settings
from app.models.entities import Artifact


async def _workflow(client: AsyncClient) -> str:
    project = (await client.post("/api/v1/projects", json={"name": "Infra"})).json()
    response = await client.post(
        "/api/v1/workflows",
        json={"project_id": project["id"], "goal": "Build a small CLI tool", "autostart": False},
    )
    return response.json()["id"]


class TestDispatch:
    async def test_celery_backend_enqueues_instead_of_running_in_the_api(
        self, auth_client: AsyncClient, monkeypatch
    ) -> None:
        from app.workers import celery_app

        queued: list[str] = []
        monkeypatch.setattr(settings, "execution_backend", "celery")
        monkeypatch.setattr(celery_app.run_workflow_task, "delay", queued.append)

        workflow_id = await _workflow(auth_client)
        response = await auth_client.post(f"/api/v1/workflows/{workflow_id}/run")

        assert response.status_code == 200
        assert queued == [workflow_id]

    async def test_an_unreachable_queue_is_a_503_not_a_silent_accept(
        self, auth_client: AsyncClient, monkeypatch
    ) -> None:
        from app.workers import celery_app

        def broker_down(_workflow_id: str) -> None:
            raise ConnectionError("Error 61 connecting to localhost:6379")

        monkeypatch.setattr(settings, "execution_backend", "celery")
        monkeypatch.setattr(celery_app.run_workflow_task, "delay", broker_down)

        workflow_id = await _workflow(auth_client)
        response = await auth_client.post(f"/api/v1/workflows/{workflow_id}/run")

        assert response.status_code == 503
        assert "Redis" in response.json()["message"]

    async def test_inline_backend_runs_in_the_api_process(
        self, auth_client: AsyncClient, monkeypatch
    ) -> None:
        ran: list[uuid.UUID] = []

        async def record(workflow_id: uuid.UUID) -> None:
            ran.append(workflow_id)

        monkeypatch.setattr("app.services.orchestrator.dispatch.run_workflow", record)
        workflow_id = await _workflow(auth_client)
        await auth_client.post(f"/api/v1/workflows/{workflow_id}/run")
        assert ran == [uuid.UUID(workflow_id)]


class TestArchive:
    async def test_the_whole_project_downloads_as_one_zip(
        self, auth_client: AsyncClient, session
    ) -> None:
        workflow_id = await _workflow(auth_client)
        for path, content in (("cli.py", "print('hi')"), ("tests/test_cli.py", "def test(): pass")):
            session.add(
                Artifact(workflow_id=uuid.UUID(workflow_id), path=path, content=content)
            )
        await session.commit()

        response = await auth_client.get(f"/api/v1/workflows/{workflow_id}/archive")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = sorted(archive.namelist())
        folder = f"evergent-{workflow_id[:8]}"
        assert names == [f"{folder}/cli.py", f"{folder}/tests/test_cli.py"]

    async def test_someone_elses_archive_is_404(
        self, auth_client: AsyncClient, client: AsyncClient
    ) -> None:
        workflow_id = await _workflow(auth_client)
        other = await client.post(
            "/api/v1/auth/register",
            json={"email": "other@example.com", "password": "long-enough-password"},
        )
        response = await client.get(
            f"/api/v1/workflows/{workflow_id}/archive",
            headers={"Authorization": f"Bearer {other.json()['access_token']}"},
        )
        assert response.status_code == 404
