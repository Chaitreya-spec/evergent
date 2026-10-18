"""Attachments and storage, manual routing, and the listing endpoints."""

from __future__ import annotations

import io
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import NoProviderAvailableError
from app.models.entities import Execution, Workflow
from app.models.enums import TaskType
from app.services import storage as storage_module
from app.services.storage import LocalStorage, S3Storage, StorageError, check_key


@pytest.fixture(autouse=True)
def local_storage(tmp_path, monkeypatch) -> LocalStorage:
    store = LocalStorage(tmp_path / "storage")
    monkeypatch.setattr(storage_module, "get_storage", lambda: store)
    monkeypatch.setattr("app.api.v1.attachments.get_storage", lambda: store)
    monkeypatch.setattr("app.api.v1.projects.get_storage", lambda: store)
    monkeypatch.setattr("app.services.orchestrator.engine.get_storage", lambda: store)
    return store


async def _project(client: AsyncClient) -> str:
    return (await client.post("/api/v1/projects", json={"name": "Platform"})).json()["id"]


def _file(name: str, content: bytes) -> dict:
    return {"file": (name, io.BytesIO(content), "application/octet-stream")}


class TestStorage:
    @pytest.mark.parametrize(
        "key", ["../etc/passwd", "a/../../b", "/abs", "", "has space"]
    )
    def test_unsafe_keys_are_refused(self, key) -> None:
        with pytest.raises(StorageError):
            check_key(key)

    async def test_local_round_trip(self, local_storage: LocalStorage) -> None:
        await local_storage.put("projects/p/a.txt", b"hello", "text/plain")
        assert await local_storage.get("projects/p/a.txt") == b"hello"
        await local_storage.delete("projects/p/a.txt")
        with pytest.raises(StorageError):
            await local_storage.get("projects/p/a.txt")

    async def test_s3_encryption_header_can_be_left_off(self) -> None:
        """For stores that encrypt anyway or reject SSE-S3 (MinIO without a KMS)."""
        import boto3
        from botocore.stub import Stubber

        client = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="t", aws_secret_access_key="t"
        )
        stub = Stubber(client)
        stub.add_response(
            "put_object",
            {},
            {"Bucket": "room", "Key": "k", "Body": b"hi", "ContentType": "text/plain"},
        )
        with stub:
            await S3Storage("room", client, encryption="").put("k", b"hi", "text/plain")
        stub.assert_no_pending_responses()

    async def test_s3_sends_encrypted_puts_to_the_bucket(self) -> None:
        import boto3
        from botocore.stub import Stubber

        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        stub = Stubber(client)
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "room",
                "Key": "projects/p/a.txt",
                "Body": b"hi",
                "ContentType": "text/plain",
                "ServerSideEncryption": "AES256",
            },
        )
        stub.add_response(
            "delete_object", {}, {"Bucket": "room", "Key": "projects/p/a.txt"}
        )
        with stub:
            store = S3Storage("room", client)
            await store.put("projects/p/a.txt", b"hi", "text/plain")
            await store.delete("projects/p/a.txt")
        stub.assert_no_pending_responses()


class TestAttachments:
    async def test_upload_list_download_delete(self, auth_client: AsyncClient) -> None:
        project_id = await _project(auth_client)
        uploaded = await auth_client.post(
            f"/api/v1/projects/{project_id}/attachments",
            files=_file("spec.md", b"# Spec\nMust be dark."),
        )
        assert uploaded.status_code == 201, uploaded.text
        attachment = uploaded.json()
        assert attachment["size_bytes"] == len(b"# Spec\nMust be dark.") and len(attachment["sha256"]) == 64

        listed = (
            await auth_client.get(f"/api/v1/projects/{project_id}/attachments")
        ).json()
        assert [a["id"] for a in listed] == [attachment["id"]]
        download = await auth_client.get(
            f"/api/v1/attachments/{attachment['id']}/download"
        )
        assert download.text == "# Spec\nMust be dark."
        assert 'filename="spec.md"' in download.headers["content-disposition"]

        assert (
            await auth_client.delete(f"/api/v1/attachments/{attachment['id']}")
        ).status_code == 204
        assert (
            await auth_client.get(f"/api/v1/attachments/{attachment['id']}")
        ).status_code == 404

    @pytest.mark.parametrize(
        ("name", "content", "message"),
        [
            ("photo.png", b"\x89PNG", "Only text files"),
            ("binary.txt", b"\xff\xfe\x00bad", "not UTF-8"),
            ("empty.md", b"", "empty"),
        ],
    )
    async def test_only_reasonable_text_is_accepted(
        self, auth_client: AsyncClient, name, content, message
    ) -> None:
        project_id = await _project(auth_client)
        response = await auth_client.post(
            f"/api/v1/projects/{project_id}/attachments", files=_file(name, content)
        )
        assert response.status_code == 422 and message in response.json()["message"]

    async def test_size_is_capped(self, auth_client: AsyncClient, monkeypatch) -> None:
        monkeypatch.setattr(settings, "max_attachment_bytes", 100)
        project_id = await _project(auth_client)
        response = await auth_client.post(
            f"/api/v1/projects/{project_id}/attachments",
            files=_file("big.txt", b"x" * 101),
        )
        assert response.status_code == 422 and "limited" in response.json()["message"]

    async def test_attachments_are_private(
        self, auth_client: AsyncClient, client: AsyncClient
    ) -> None:
        project_id = await _project(auth_client)
        attachment = (
            await auth_client.post(
                f"/api/v1/projects/{project_id}/attachments",
                files=_file("a.txt", b"secret"),
            )
        ).json()
        other = await client.post(
            "/api/v1/auth/register",
            json={"email": "other@example.com", "password": "long-enough-password"},
        )
        headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
        for path in ("", "/download"):
            assert (
                await client.get(
                    f"/api/v1/attachments/{attachment['id']}{path}", headers=headers
                )
            ).status_code == 404

    async def test_a_workflow_takes_its_attachments_with_it(
        self, auth_client: AsyncClient
    ) -> None:
        project_id = await _project(auth_client)
        attachment = (
            await auth_client.post(
                f"/api/v1/projects/{project_id}/attachments",
                files=_file("spec.md", b"dark"),
            )
        ).json()
        workflow = await auth_client.post(
            "/api/v1/workflows",
            json={
                "project_id": project_id,
                "goal": "Build what the spec says",
                "attachment_ids": [attachment["id"]],
                "autostart": False,
            },
        )
        assert workflow.status_code == 201
        linked = (await auth_client.get(f"/api/v1/attachments/{attachment['id']}")).json()
        assert linked["workflow_id"] == workflow.json()["id"]

        missing = await auth_client.post(
            "/api/v1/workflows",
            json={
                "project_id": project_id,
                "goal": "Build what the spec says",
                "attachment_ids": [str(uuid.uuid4())],
                "autostart": False,
            },
        )
        assert missing.status_code == 404

    async def test_the_planner_reads_attached_files(
        self, engine, monkeypatch, local_storage
    ) -> None:
        from app.models.entities import Attachment
        from app.services.orchestrator.engine import ExecutionEngine
        from tests.test_engine import ScriptedProvider, _workspace

        scripted = ScriptedProvider()
        workflow_id, factory = await _workspace(engine, monkeypatch, scripted)
        async with factory() as session:
            workflow = await session.get(Workflow, workflow_id)
            await local_storage.put(
                "projects/x/attachments/spec",
                b"The hero must say STEEP in gold.",
                "text/plain",
            )
            session.add(
                Attachment(
                    project_id=workflow.project_id,
                    workflow_id=workflow.id,
                    filename="spec.md",
                    content_type="text/plain",
                    size_bytes=33,
                    sha256="0" * 64,
                    storage_key="projects/x/attachments/spec",
                )
            )
            await session.commit()

        await ExecutionEngine(workflow_id).run()
        briefing = scripted.commander_messages[0]
        assert "## FILES THE USER ATTACHED" in briefing
        assert "### spec.md" in briefing and "STEEP in gold" in briefing


class TestProjectDeletion:
    async def test_files_go_with_the_project(
        self, auth_client: AsyncClient, local_storage: LocalStorage
    ) -> None:
        project_id = await _project(auth_client)
        uploaded = (
            await auth_client.post(
                f"/api/v1/projects/{project_id}/attachments",
                files=_file("notes.md", b"# notes"),
            )
        ).json()
        key = f"projects/{project_id}/attachments/{uploaded['id']}"
        assert await local_storage.get(key) == b"# notes"

        assert (await auth_client.delete(f"/api/v1/projects/{project_id}")).status_code == 204
        with pytest.raises(StorageError):
            await local_storage.get(key)

    async def test_not_while_a_workflow_runs(
        self, auth_client: AsyncClient, session
    ) -> None:
        project_id = await _project(auth_client)
        workflow = (
            await auth_client.post(
                "/api/v1/workflows",
                json={"project_id": project_id, "goal": "Build a todo app", "autostart": False},
            )
        ).json()
        row = await session.get(Workflow, uuid.UUID(workflow["id"]))
        row.status = "running"
        await session.commit()

        refused = await auth_client.delete(f"/api/v1/projects/{project_id}")
        assert refused.status_code == 409
        assert "still running" in refused.json()["message"]


class TestManualRouting:
    async def _router(self, session):
        from app.services.router.model_router import ModelRouter

        return ModelRouter(session)

    @pytest.fixture
    def all_reachable(self, monkeypatch):
        from tests.test_engine import ScriptedProvider

        provider = ScriptedProvider()
        monkeypatch.setattr(
            "app.services.router.model_router.get_provider", lambda _n: provider
        )

    async def test_a_pinned_model_is_used(self, seeded_session, all_reachable) -> None:
        router = await self._router(seeded_session)
        decision = await router.route(TaskType.FRONTEND, pinned="claude-haiku-4-5")
        assert decision.model.name == "claude-haiku-4-5"
        assert decision.reason == "Claude Haiku 4.5 was chosen by hand."

    async def test_an_unreachable_pin_falls_back_only_if_allowed(
        self, seeded_session, all_reachable
    ) -> None:
        router = await self._router(seeded_session)
        fallback = await router.route(
            TaskType.FRONTEND, pinned="no-such-model", allow_fallback=True
        )
        assert fallback.reason.startswith(
            "no-such-model was chosen by hand but is not reachable; fell back."
        )
        with pytest.raises(
            NoProviderAvailableError, match="falling back to another is turned off"
        ):
            await router.route(
                TaskType.FRONTEND, pinned="no-such-model", allow_fallback=False
            )

    async def test_the_api_records_the_choice(
        self, auth_client: AsyncClient, seeded_session
    ) -> None:
        project_id = await _project(auth_client)
        created = (
            await auth_client.post(
                "/api/v1/workflows",
                json={
                    "project_id": project_id,
                    "goal": "Build a todo app",
                    "model": "gpt-5.2",
                    "allow_fallback": False,
                    "autostart": False,
                },
            )
        ).json()
        assert created["routing_strategy"] == "manual"
        assert created["pinned_model"] == "gpt-5.2" and created["allow_fallback"] is False

        unknown = await auth_client.post(
            "/api/v1/workflows",
            json={
                "project_id": project_id,
                "goal": "Build a todo app",
                "model": "imaginary-9",
                "autostart": False,
            },
        )
        assert unknown.status_code == 422 and "imaginary-9" in unknown.json()["message"]


class TestListings:
    async def test_project_settings_can_change(self, auth_client: AsyncClient) -> None:
        project_id = await _project(auth_client)
        updated = await auth_client.patch(
            f"/api/v1/projects/{project_id}",
            json={"name": "Renamed", "max_cost_usd": 2.5},
        )
        assert updated.status_code == 200
        assert (
            updated.json()["name"] == "Renamed" and updated.json()["max_cost_usd"] == 2.5
        )

    async def test_artifacts_across_builds(
        self, auth_client: AsyncClient, session
    ) -> None:
        from app.models.entities import Artifact

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
        session.add(
            Artifact(
                workflow_id=uuid.UUID(workflow["id"]),
                path="index.html",
                content="<h1>hi</h1>",
            )
        )
        await session.commit()

        listed = (await auth_client.get("/api/v1/artifacts")).json()
        assert [(a["path"], a["size"], a["workflow_goal"]) for a in listed] == [
            ("index.html", 11, "Build a todo app")
        ]
        assert "content" not in listed[0]

    async def test_usage_by_day(self, auth_client: AsyncClient, session) -> None:
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
        for cost, ok in ((0.25, True), (0.0, False)):
            session.add(
                Execution(
                    workflow_id=uuid.UUID(workflow["id"]),
                    provider_name="google",
                    model_name="gemini",
                    role="frontend",
                    total_tokens=1000,
                    estimated_cost=cost,
                    succeeded=ok,
                )
            )
        await session.commit()

        usage = (await auth_client.get("/api/v1/usage")).json()
        assert usage["failed_calls"] == 1 and usage["by_role"] == {"frontend": 0.25}
        assert len(usage["daily"]) == 1 and usage["daily"][0]["calls"] == 2
        assert usage["spent_today_usd"] == 0.25 and usage["estimated"] is True

    async def test_tool_calls_across_a_workflow(
        self, auth_client: AsyncClient, session
    ) -> None:
        from app.models.entities import Task, ToolCall

        project_id = await _project(auth_client)
        workflow = (
            await auth_client.post(
                "/api/v1/workflows",
                json={"project_id": project_id, "goal": "Build a todo app", "autostart": False},
            )
        ).json()
        task = Task(
            workflow_id=uuid.UUID(workflow["id"]),
            key="t1",
            title="Tests",
            description="d",
            task_type="testing",
        )
        session.add(task)
        await session.flush()
        session.add(
            ToolCall(
                task_id=task.id,
                tool_name="run_code",
                arguments={"language": "python"},
                result={"exit_code": 1},
                succeeded=False,
                error="1 failed",
            )
        )
        await session.commit()

        calls = (
            await auth_client.get(f"/api/v1/workflows/{workflow['id']}/tool-calls")
        ).json()
        assert [(c["task_key"], c["tool_name"], c["succeeded"]) for c in calls] == [
            ("t1", "run_code", False)
        ]

        from app.models.entities import Review

        session.add(
            Review(
                task_id=task.id,
                reviewer_agent="reviewer",
                status="needs_revision",
                score=5.5,
                feedback=[{"severity": "high", "message": "No edge cases"}],
                summary="Thin tests",
            )
        )
        await session.commit()
        reviews = (
            await auth_client.get(f"/api/v1/workflows/{workflow['id']}/reviews")
        ).json()
        assert [(r["task_key"], r["status"], r["score"]) for r in reviews] == [
            ("t1", "needs_revision", 5.5)
        ]

    async def test_workflows_filter_by_status_and_text(
        self, auth_client: AsyncClient, session
    ) -> None:
        project_id = await _project(auth_client)
        ids = {}
        for goal in ("Build a todo app", "Write a parser"):
            ids[goal] = (
                await auth_client.post(
                    "/api/v1/workflows",
                    json={"project_id": project_id, "goal": goal, "autostart": False},
                )
            ).json()["id"]
        workflow = await session.get(Workflow, uuid.UUID(ids["Write a parser"]))
        workflow.status = "failed"
        await session.commit()

        def goals(response) -> list[str]:
            return [w["goal"] for w in response.json()["items"]]

        assert goals(await auth_client.get("/api/v1/workflows?status=failed")) == [
            "Write a parser"
        ]
        assert goals(
            await auth_client.get("/api/v1/workflows?status=pending&status=running")
        ) == ["Build a todo app"]
        assert goals(await auth_client.get("/api/v1/workflows?q=TODO")) == [
            "Build a todo app"
        ]

    async def test_members_see_only_their_own_audit_trail(
        self, auth_client: AsyncClient, client: AsyncClient
    ) -> None:
        await _project(auth_client)
        await client.post(
            "/api/v1/auth/register",
            json={"email": "other@example.com", "password": "long-enough-password"},
        )
        mine = (await auth_client.get("/api/v1/audit-logs")).json()
        assert {e["action"] for e in mine} == {"auth.register"}
        assert len(mine) == 1

    async def test_the_integration_engineer_is_on_the_roster(
        self, auth_client: AsyncClient
    ) -> None:
        agents = {a["name"]: a for a in (await auth_client.get("/api/v1/agents")).json()}
        assert agents["integration"]["handles"] == ["integration"]
        assert "integration" not in agents["backend"]["handles"]


_ = select
