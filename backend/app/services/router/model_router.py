"""Intelligent model router.

Picks the model for each subtask by scoring capability against cost, latency
and reliability. There is deliberately no "frontend -> Gemini" style mapping
anywhere: capability scores live on the Model rows, so a benchmark job can
rewrite them and routing improves without a code change.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import NoProviderAvailableError
from app.models.entities import Model
from app.models.enums import Capability, RoutingStrategy, TaskType
from app.services.providers.registry import get_provider

# Which capabilities each kind of task actually needs. Used when the planner
# does not state them explicitly.
TASK_CAPABILITIES: dict[TaskType, list[Capability]] = {
    TaskType.REQUIREMENTS: [Capability.REASONING, Capability.WRITING],
    TaskType.RESEARCH: [Capability.RESEARCH, Capability.REASONING],
    TaskType.CODING: [Capability.CODING, Capability.REASONING],
    TaskType.ARCHITECTURE: [Capability.REASONING, Capability.CODING],
    TaskType.DESIGN: [Capability.FRONTEND, Capability.WRITING],
    TaskType.FRONTEND: [Capability.FRONTEND, Capability.CODING],
    TaskType.BACKEND: [Capability.BACKEND, Capability.CODING],
    TaskType.DATABASE: [Capability.BACKEND, Capability.REASONING],
    TaskType.AUTHENTICATION: [Capability.BACKEND, Capability.CODING],
    TaskType.INTEGRATION: [Capability.CODING, Capability.REASONING],
    TaskType.TESTING: [Capability.CODING, Capability.DEBUGGING],
    TaskType.DEBUGGING: [Capability.DEBUGGING, Capability.REASONING],
    TaskType.SECURITY: [Capability.REASONING, Capability.CODING],
    TaskType.DOCUMENTATION: [Capability.WRITING],
    TaskType.REVIEW: [Capability.REASONING, Capability.DEBUGGING],
    TaskType.SYNTHESIS: [Capability.REASONING, Capability.WRITING, Capability.LONG_CONTEXT],
}


@dataclass(slots=True)
class RoutingDecision:
    model: Model
    score: float
    # Shown in the API and logs, so a routing decision is never a black box.
    reason: str


@dataclass(slots=True)
class StrategyWeights:
    """How much each factor matters. Strategies are just different weights."""

    capability: float
    cost: float
    latency: float
    reliability: float


# Capability scores span roughly 8-10, so real differences between models are
# fractions of a point. Cost and latency indices are normalised to 0-1, and
# must be scaled into the same neighbourhood or they drown the thing being
# optimised for: at a 10x scale, "highest quality" would reject the best model
# merely for being the slowest.
PENALTY_SCALE = 4.0
RELIABILITY_SCALE = 2.0

STRATEGY_WEIGHTS: dict[RoutingStrategy, StrategyWeights] = {
    RoutingStrategy.HIGHEST_QUALITY: StrategyWeights(1.0, 0.05, 0.05, 0.3),
    RoutingStrategy.CHEAPEST: StrategyWeights(0.35, 1.0, 0.1, 0.2),
    RoutingStrategy.FASTEST: StrategyWeights(0.35, 0.1, 1.0, 0.2),
    RoutingStrategy.BALANCED: StrategyWeights(1.0, 0.45, 0.25, 0.3),
}


class ModelRouter:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _candidates(self) -> list[Model]:
        """Enabled models whose provider is actually reachable."""
        rows = await self.session.scalars(
            select(Model)
            .options(selectinload(Model.provider))
            .where(Model.is_enabled.is_(True))
        )
        models = list(rows)

        usable: list[Model] = []
        for model in models:
            if not model.provider.is_enabled:
                continue
            try:
                provider = get_provider(model.provider.name)
            except NoProviderAvailableError:
                continue
            # A model whose provider has no credential cannot run, so it must
            # not win a routing decision and fail at call time.
            if provider.is_configured():
                usable.append(model)
        return usable

    @staticmethod
    def _capability_score(model: Model, needed: list[Capability]) -> float:
        if not needed:
            return float(model.capabilities.get("reasoning", 5.0))
        scores = [float(model.capabilities.get(c.value, 5.0)) for c in needed]
        # The weakest required capability caps the model: a model that cannot
        # do one necessary thing is not saved by excelling at another.
        return (min(scores) * 0.6) + (sum(scores) / len(scores) * 0.4)

    @staticmethod
    def _blended_cost(model: Model) -> float:
        # Output tokens dominate agent work, so they weigh more.
        return (
            model.cost_per_input_token * 0.3 + model.cost_per_output_token * 0.7
        ) * 1_000_000

    async def route(
        self,
        task_type: TaskType,
        *,
        strategy: RoutingStrategy = RoutingStrategy.BALANCED,
        required_capabilities: list[Capability] | None = None,
        min_context_tokens: int = 0,
        custom_weights: StrategyWeights | None = None,
        pinned: str | None = None,
        allow_fallback: bool = True,
    ) -> RoutingDecision:
        needed = required_capabilities or TASK_CAPABILITIES.get(task_type, [])
        candidates = await self._candidates()

        fallback_note = ""
        if pinned:
            chosen = next((m for m in candidates if m.name == pinned), None)
            if chosen is not None:
                return RoutingDecision(
                    model=chosen, score=0.0, reason=f"{chosen.display_name} was chosen by hand."
                )
            if not allow_fallback:
                raise NoProviderAvailableError(
                    f"The chosen model {pinned} is not reachable, and falling back to another is turned off."
                )
            fallback_note = f"{pinned} was chosen by hand but is not reachable; fell back. "

        if min_context_tokens:
            fits = [m for m in candidates if m.context_window >= min_context_tokens]
            # Falling back is better than failing: a tight context is a reason
            # to prefer a bigger model, not to refuse the task outright.
            candidates = fits or candidates

        if not candidates:
            raise NoProviderAvailableError(
                "No model is reachable. Configure a provider credential, or "
                "enable a model in the catalog."
            )

        weights = custom_weights or STRATEGY_WEIGHTS.get(
            strategy, STRATEGY_WEIGHTS[RoutingStrategy.BALANCED]
        )

        costs = [self._blended_cost(m) for m in candidates]
        latencies = [m.avg_latency_ms for m in candidates]
        cost_span = (max(costs) - min(costs)) or 1.0
        latency_span = (max(latencies) - min(latencies)) or 1
        min_cost, min_latency = min(costs), min(latencies)

        scored: list[tuple[float, Model, float]] = []
        for model in candidates:
            capability = self._capability_score(model, needed)
            # 0 for the cheapest/fastest in the pool, 1 for the worst.
            cost_index = (self._blended_cost(model) - min_cost) / cost_span
            latency_index = (model.avg_latency_ms - min_latency) / latency_span

            total = (
                weights.capability * capability
                - weights.cost * cost_index * PENALTY_SCALE
                - weights.latency * latency_index * PENALTY_SCALE
                + weights.reliability * model.reliability * RELIABILITY_SCALE
            )
            scored.append((total, model, capability))

        scored.sort(key=lambda row: row[0], reverse=True)
        best_total, best, best_capability = scored[0]

        if len(scored) > 1:
            _, runner_up, runner_capability = scored[1]
            reason = (
                f"{best.display_name} scores {best_capability:.1f} on "
                f"{task_type.value} (vs {runner_up.display_name} at "
                f"{runner_capability:.1f}) under the {strategy.value} strategy."
            )
        else:
            reason = f"{best.display_name} is the only reachable model."
        reason = fallback_note + reason

        return RoutingDecision(model=best, score=best_total, reason=reason)

    async def fallback(
        self, exclude_model_id, task_type: TaskType, **kwargs
    ) -> RoutingDecision | None:
        """Best model excluding one that just failed, or None if there is none."""
        decision = await self.route(task_type, **kwargs)
        if decision.model.id != exclude_model_id:
            return decision

        candidates = [m for m in await self._candidates() if m.id != exclude_model_id]
        if not candidates:
            return None

        needed = kwargs.get("required_capabilities") or TASK_CAPABILITIES.get(
            task_type, []
        )
        best = max(candidates, key=lambda m: self._capability_score(m, needed))
        return RoutingDecision(
            model=best,
            score=self._capability_score(best, needed),
            reason=f"Rerouted to {best.display_name} after the previous model failed.",
        )
