"""Model routing."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.entities import Execution, Model, Project, User, Workflow
from app.models.enums import Capability, RoutingStrategy, TaskType
from app.services.router.model_router import ModelRouter
from app.services.router.stats import refresh_model_stats, refresh_stats_for_workflow


@pytest.fixture
async def router(seeded_session, monkeypatch) -> ModelRouter:
    # Routing must be measurable without credentials, so every seeded provider
    # is treated as reachable for these tests.
    from app.services.providers import registry as provider_registry

    class _Configured:
        def is_configured(self) -> bool:
            return True

    monkeypatch.setattr(provider_registry, "get_provider", lambda _n: _Configured())
    monkeypatch.setattr(
        "app.services.router.model_router.get_provider", lambda _n: _Configured()
    )
    return ModelRouter(seeded_session)


class TestRouting:
    async def test_frontend_goes_to_the_strongest_frontend_model(self, router) -> None:
        decision = await router.route(
            TaskType.FRONTEND, strategy=RoutingStrategy.HIGHEST_QUALITY
        )
        assert decision.model.name == "gemini-3-pro"

    async def test_debugging_goes_to_the_strongest_debugger(self, router) -> None:
        decision = await router.route(
            TaskType.DEBUGGING, strategy=RoutingStrategy.HIGHEST_QUALITY
        )
        assert decision.model.name == "claude-opus-5"

    async def test_cheapest_strategy_picks_a_cheaper_model(self, router) -> None:
        quality = await router.route(
            TaskType.DOCUMENTATION, strategy=RoutingStrategy.HIGHEST_QUALITY
        )
        cheap = await router.route(
            TaskType.DOCUMENTATION, strategy=RoutingStrategy.CHEAPEST
        )
        assert cheap.model.cost_per_output_token <= quality.model.cost_per_output_token

    async def test_fastest_strategy_picks_a_faster_model(self, router) -> None:
        quality = await router.route(
            TaskType.REQUIREMENTS, strategy=RoutingStrategy.HIGHEST_QUALITY
        )
        fast = await router.route(
            TaskType.REQUIREMENTS, strategy=RoutingStrategy.FASTEST
        )
        assert fast.model.avg_latency_ms <= quality.model.avg_latency_ms

    async def test_vision_requirement_is_respected(self, router) -> None:
        decision = await router.route(
            TaskType.DESIGN,
            strategy=RoutingStrategy.HIGHEST_QUALITY,
            required_capabilities=[Capability.VISION],
        )
        assert decision.model.capabilities["vision"] >= 9.0

    async def test_context_requirement_excludes_small_windows(self, router) -> None:
        decision = await router.route(TaskType.SYNTHESIS, min_context_tokens=500_000)
        assert decision.model.context_window >= 500_000

    async def test_decision_explains_itself(self, router) -> None:
        decision = await router.route(TaskType.BACKEND)
        assert "scores" in decision.reason

    async def test_weakest_required_capability_caps_the_score(self, router) -> None:
        # A model strong at one needed capability but weak at another must not
        # outrank a model that is adequate at both.
        decision = await router.route(
            TaskType.BACKEND,
            strategy=RoutingStrategy.HIGHEST_QUALITY,
            required_capabilities=[Capability.BACKEND, Capability.VISION],
        )
        caps = decision.model.capabilities
        assert min(caps["backend"], caps["vision"]) >= 8.8

    async def test_fallback_avoids_the_failed_model(self, router) -> None:
        first = await router.route(TaskType.BACKEND)
        alternative = await router.fallback(first.model.id, TaskType.BACKEND)
        assert alternative is not None
        assert alternative.model.id != first.model.id

    async def test_no_models_raises_clearly(self, seeded_session, monkeypatch) -> None:
        from app.core.errors import NoProviderAvailableError

        class _Unconfigured:
            def is_configured(self) -> bool:
                return False

        monkeypatch.setattr(
            "app.services.router.model_router.get_provider", lambda _n: _Unconfigured()
        )
        # Seeded providers have no stored credential either, so nothing is
        # reachable and the router must say so rather than returning garbage.
        with pytest.raises(NoProviderAvailableError, match="No model is reachable"):
            await ModelRouter(seeded_session).route(TaskType.BACKEND)


class TestRoutingStats:
    """Latency and reliability learned from the execution log."""

    @staticmethod
    async def _workflow(session) -> uuid.UUID:
        user = User(email=f"stats-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
        session.add(user)
        await session.flush()
        project = Project(owner_id=user.id, name="Stats test")
        session.add(project)
        await session.flush()
        workflow = Workflow(project_id=project.id, goal="measure something")
        session.add(workflow)
        await session.commit()
        return workflow.id

    @staticmethod
    async def _record(
        session, workflow_id, model_name: str, *, latency: int, ok: bool = True
    ) -> None:
        session.add(
            Execution(
                workflow_id=workflow_id,
                provider_name="anthropic",
                model_name=model_name,
                role="backend",
                latency_ms=latency,
                succeeded=ok,
            )
        )
        await session.commit()

    async def test_no_executions_leaves_seeded_priors_alone(
        self, seeded_session
    ) -> None:
        before = await seeded_session.scalar(
            select(Model).where(Model.name == "claude-opus-5")
        )
        seeded_latency, seeded_reliability = before.avg_latency_ms, before.reliability

        assert await refresh_model_stats(seeded_session) == {}

        await seeded_session.refresh(before)
        assert before.avg_latency_ms == seeded_latency
        assert before.reliability == seeded_reliability

    async def test_latency_becomes_the_measured_median(self, seeded_session) -> None:
        wid = await self._workflow(seeded_session)
        for latency in (1000, 1200, 1400):
            await self._record(seeded_session, wid, "claude-opus-5", latency=latency)

        updated = await refresh_model_stats(
            seeded_session, model_names=["claude-opus-5"]
        )

        # Seeded prior was 9000ms; the model is measurably faster than that.
        assert updated["claude-opus-5"][0] == 1200

    async def test_an_outlier_does_not_redefine_speed(self, seeded_session) -> None:
        wid = await self._workflow(seeded_session)
        for latency in (1000, 1100, 1200, 1300, 90_000):
            await self._record(seeded_session, wid, "claude-opus-5", latency=latency)

        updated = await refresh_model_stats(
            seeded_session, model_names=["claude-opus-5"]
        )

        # A mean would report ~18900ms. The median refuses to be dragged.
        assert updated["claude-opus-5"][0] == 1200

    async def test_failed_calls_are_excluded_from_latency(self, seeded_session) -> None:
        wid = await self._workflow(seeded_session)
        await self._record(seeded_session, wid, "claude-opus-5", latency=5000)
        # A 401 comes back fast; counting it would make a broken model look
        # like the quickest in the pool.
        for _ in range(4):
            await self._record(seeded_session, wid, "claude-opus-5", latency=12, ok=False)

        updated = await refresh_model_stats(
            seeded_session, model_names=["claude-opus-5"]
        )

        assert updated["claude-opus-5"][0] == 5000

    async def test_one_failure_does_not_retire_a_model(self, seeded_session) -> None:
        wid = await self._workflow(seeded_session)
        await self._record(seeded_session, wid, "claude-opus-5", latency=0, ok=False)

        _, reliability = (
            await refresh_model_stats(seeded_session, model_names=["claude-opus-5"])
        )["claude-opus-5"]

        # Smoothed, not zeroed: a single transient failure must not be a
        # permanent sentence.
        assert 0.75 < reliability < 1.0

    async def test_sustained_failure_drives_reliability_down(
        self, seeded_session
    ) -> None:
        wid = await self._workflow(seeded_session)
        for _ in range(40):
            await self._record(seeded_session, wid, "claude-opus-5", latency=20, ok=False)

        _, reliability = (
            await refresh_model_stats(seeded_session, model_names=["claude-opus-5"])
        )["claude-opus-5"]

        assert reliability < 0.15

    async def test_reliability_can_change_a_routing_decision(self, router) -> None:
        """The whole point: the reliability term must not be inert.

        Every model seeds at reliability 1.0, so before this existed the term
        added the same constant to every candidate and could never move a
        ranking.
        """
        session = router.session
        first = await router.route(TaskType.BACKEND, strategy=RoutingStrategy.BALANCED)

        wid = await self._workflow(session)
        for _ in range(60):
            await self._record(session, wid, first.model.name, latency=400, ok=False)
        await refresh_model_stats(session, model_names=[first.model.name])

        second = await router.route(TaskType.BACKEND, strategy=RoutingStrategy.BALANCED)
        assert second.model.name != first.model.name

    async def test_workflow_scoped_refresh_touches_only_models_it_used(
        self, seeded_session
    ) -> None:
        wid = await self._workflow(seeded_session)
        await self._record(seeded_session, wid, "claude-opus-5", latency=1500)
        # Another model with history, but from a different workflow.
        other = await self._workflow(seeded_session)
        await self._record(seeded_session, other, "gpt-5.2", latency=1500)

        updated = await refresh_stats_for_workflow(seeded_session, wid)

        assert set(updated) == {"claude-opus-5"}
