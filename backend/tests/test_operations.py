"""Spending reservations, restart recovery, and provider credentials."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import BudgetExceededError
from app.models.entities import Execution, Model, Project, Provider, Task, User, Workflow
from app.models.enums import TaskStatus, WorkflowStatus
from app.services.orchestrator import llm
from app.services.orchestrator.recovery import recover_orphans
from app.services.providers.base import (
    AIProvider,
    ChatMessage,
    CompletionRequest,
    CompletionResult,
)


async def _workflow(
    session: AsyncSession, *, max_cost: float = 1.0, **fields
) -> Workflow:
    user = User(email=f"{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(owner_id=user.id, name="Ops")
    session.add(project)
    await session.flush()
    workflow = Workflow(project_id=project.id, goal="g", max_cost_usd=max_cost, **fields)
    session.add(workflow)
    await session.commit()
    return workflow


class TestReservations:
    async def test_parallel_calls_cannot_jointly_overshoot(self, engine) -> None:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            workflow = await _workflow(session, max_cost=1.0)

        async def reserve() -> bool:
            async with factory() as session:
                try:
                    await llm._reserve(session, workflow.id, 0.3)
                    return True
                except BudgetExceededError:
                    return False

        outcomes = await asyncio.gather(*(reserve() for _ in range(5)))
        # 3 x $0.30 fits under $1.00; a fourth would not, whatever the timing.
        assert outcomes.count(True) == 3
        async with factory() as session:
            assert await session.scalar(
                select(Workflow.reserved_cost_usd)
            ) == pytest.approx(0.9)

    async def test_the_daily_limit_spans_every_workflow(
        self, session, monkeypatch
    ) -> None:
        monkeypatch.setattr(settings, "max_daily_cost_usd", 0.5)
        workflow = await _workflow(session, max_cost=10.0)
        session.add(
            Execution(
                workflow_id=workflow.id,
                provider_name="p",
                model_name="m",
                role="worker",
                estimated_cost=0.45,
            )
        )
        await session.commit()
        with pytest.raises(BudgetExceededError, match="daily spending limit"):
            await llm._reserve(session, workflow.id, 0.1)

    async def test_a_call_releases_its_hold_whatever_happens(
        self, seeded_session, monkeypatch
    ) -> None:
        session = seeded_session
        workflow = await _workflow(session, max_cost=50.0)
        model = await session.scalar(
            select(Model).where(Model.name == "claude-haiku-4-5")
        )
        await session.refresh(model, ["provider"])

        class Works(AIProvider):
            name = "fake"

            def is_configured(self) -> bool:
                return True

            async def generate(self, request: CompletionRequest) -> CompletionResult:
                held = await session.scalar(
                    select(Workflow.reserved_cost_usd).where(Workflow.id == workflow.id)
                )
                assert held > 0  # reserved while the call is in flight
                return CompletionResult(text="ok", input_tokens=100, output_tokens=50)

            def stream(self, request):  # pragma: no cover
                raise NotImplementedError

        class Breaks(Works):
            async def generate(self, request: CompletionRequest) -> CompletionResult:
                raise RuntimeError("provider down")

        kwargs = {
            "workflow_id": workflow.id,
            "model": model,
            "role": "worker",
            "system": "s",
            "messages": [ChatMessage("user", "hi")],
        }
        monkeypatch.setattr(llm, "get_provider", lambda _name: Works())
        await llm.call_model(session, **kwargs)
        monkeypatch.setattr(llm, "get_provider", lambda _name: Breaks())
        with pytest.raises(Exception, match="provider down"):
            await llm.call_model(session, **kwargs)

        await session.refresh(workflow)
        assert workflow.reserved_cost_usd == pytest.approx(0.0)
        assert workflow.total_cost_usd == pytest.approx(llm.estimate_cost(model, 100, 50))

    async def test_a_hold_bigger_than_the_whole_budget_is_refused(self, session) -> None:
        workflow = await _workflow(session, max_cost=0.01)
        with pytest.raises(BudgetExceededError, match=r"\$0\.01 ceiling"):
            await llm._reserve(session, workflow.id, 0.5)


class TestRecovery:
    async def test_a_silent_workflow_is_paused_with_its_work_requeued(
        self, session
    ) -> None:
        long_ago = datetime.now(UTC) - timedelta(minutes=30)
        workflow = await _workflow(
            session,
            status=WorkflowStatus.RUNNING,
            started_at=long_ago,
            heartbeat_at=long_ago,
            reserved_cost_usd=0.4,
        )
        session.add_all(
            [
                Task(
                    workflow_id=workflow.id,
                    key="t1",
                    title="a",
                    description="d",
                    task_type="coding",
                    status=TaskStatus.RUNNING,
                ),
                Task(
                    workflow_id=workflow.id,
                    key="t2",
                    title="b",
                    description="d",
                    task_type="coding",
                    status=TaskStatus.COMPLETED,
                ),
            ]
        )
        await session.commit()

        assert await recover_orphans(session) == [workflow.id]

        await session.refresh(workflow)
        assert workflow.status == WorkflowStatus.PAUSED
        assert "Resume to continue" in workflow.error
        assert workflow.reserved_cost_usd == 0.0
        statuses = {t.key: t.status for t in await session.scalars(select(Task))}
        assert statuses == {"t1": TaskStatus.PENDING, "t2": TaskStatus.COMPLETED}

    async def test_a_workflow_with_a_live_heartbeat_is_left_alone(self, session) -> None:
        now = datetime.now(UTC)
        await _workflow(
            session,
            status=WorkflowStatus.RUNNING,
            started_at=now - timedelta(minutes=30),
            heartbeat_at=now,
        )
        assert await recover_orphans(session) == []

    async def test_finished_and_paused_workflows_are_not_touched(self, session) -> None:
        long_ago = datetime.now(UTC) - timedelta(hours=2)
        for status in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.PAUSED,
            WorkflowStatus.AWAITING_APPROVAL,
        ):
            await _workflow(
                session, status=status, started_at=long_ago, heartbeat_at=long_ago
            )
        assert await recover_orphans(session) == []


class TestProviders:
    @pytest.fixture
    async def admin(self, auth_client: AsyncClient, session) -> AsyncClient:
        from app.models.enums import UserRole

        user = await session.scalar(select(User))
        user.role = UserRole.ADMIN
        await session.commit()
        return auth_client

    async def _provider(self, client: AsyncClient, name: str) -> dict:
        return next(
            p for p in (await client.get("/api/v1/providers")).json() if p["name"] == name
        )

    async def test_a_stored_key_is_actually_used(
        self, admin: AdminClient, seeded_session
    ) -> None:
        """Regression: keys saved through the API were encrypted and then
        never read, so a provider looked configured but every call failed."""
        from app.services.providers.registry import get_provider

        provider = await self._provider(admin, "openrouter")
        assert provider["configured"] is False

        updated = await admin.patch(
            f"/api/v1/providers/{provider['id']}",
            json={"api_key": "sk-or-test-1234567890"},
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["configured"] is True and body["credential_source"] == "database"
        assert body["api_key_preview"] != "sk-or-test-1234567890"  # never the key itself
        assert get_provider("openrouter").api_key == "sk-or-test-1234567890"

        cleared = await admin.patch(
            f"/api/v1/providers/{provider['id']}", json={"api_key": ""}
        )
        assert cleared.json()["configured"] is False

    async def test_verification_records_a_real_answer(
        self, admin: AsyncClient, seeded_session, monkeypatch
    ) -> None:
        from app.services.providers import registry

        provider = await self._provider(admin, "openrouter")
        await admin.patch(
            f"/api/v1/providers/{provider['id']}",
            json={"api_key": "sk-or-test-1234567890"},
        )

        async def lists(self) -> list[str]:
            return ["anthropic/claude-sonnet-5", "google/gemini-3-pro"]

        monkeypatch.setattr(registry.PROVIDER_FACTORIES["openrouter"], "verify", lists)
        verified = (await admin.post(f"/api/v1/providers/{provider['id']}/verify")).json()
        assert (
            verified["verification_ok"] is True and verified["verified_model_count"] == 2
        )
        assert verified["verified_at"] is not None

        async def rejects(self) -> list[str]:
            raise RuntimeError("401 invalid api key")

        monkeypatch.setattr(registry.PROVIDER_FACTORIES["openrouter"], "verify", rejects)
        failed = (await admin.post(f"/api/v1/providers/{provider['id']}/verify")).json()
        assert failed["verification_ok"] is False
        assert "invalid api key" in failed["verification_error"]
        # Still configured — a key exists — but now known not to work.
        assert failed["configured"] is True

    async def test_unconfigured_providers_are_reported_not_called(
        self, admin: AsyncClient, seeded_session
    ) -> None:
        provider = await self._provider(admin, "anthropic")
        result = (await admin.post(f"/api/v1/providers/{provider['id']}/verify")).json()
        assert result["verification_ok"] is False
        assert result["verification_error"] == "No credential is configured"

    async def test_a_provider_needs_an_adapter(self, admin: AsyncClient) -> None:
        response = await admin.post(
            "/api/v1/providers",
            json={"name": "madeup", "display_name": "Made up", "api_key": "x" * 20},
        )
        assert response.status_code == 422
        assert "no adapter" in response.json()["message"]

    async def test_members_cannot_change_providers(
        self, auth_client: AsyncClient, seeded_session
    ) -> None:
        provider = await self._provider(auth_client, "anthropic")
        for method, path in (("patch", ""), ("post", "/verify")):
            kwargs = {"json": {"is_enabled": False}} if method == "patch" else {}
            response = await getattr(auth_client, method)(
                f"/api/v1/providers/{provider['id']}{path}", **kwargs
            )
            assert response.status_code == 403


AdminClient = AsyncClient
_ = Provider
