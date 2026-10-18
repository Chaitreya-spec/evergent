"""The single instrumented path from the orchestrator to any model.

Every call is recorded as an Execution row — successes and failures alike — so
spend, latency and reliability can be reconstructed from the database without
instrumenting each call site.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import BudgetExceededError, ProviderError
from app.core.logging import get_logger
from app.models.entities import Execution, Model, Project, Task, Workflow
from app.services.providers.base import ChatMessage, CompletionRequest, CompletionResult
from app.services.providers.registry import get_provider

log = get_logger(__name__)


def estimate_cost(model: Model, input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * model.cost_per_input_token
        + output_tokens * model.cost_per_output_token
    )


def worst_case_cost(model: Model, system: str | None, messages: list[ChatMessage], max_tokens: int) -> float:
    """The most this call can cost: its prompt (estimated at three characters
    per token, which overestimates) plus every output token it may produce."""
    chars = len(system or "") + sum(len(m.content) for m in messages)
    return estimate_cost(model, chars // 3 + 64, max_tokens)


async def _reserve(session: AsyncSession, workflow_id: uuid.UUID, amount: float) -> None:
    """Hold `amount` against the workflow's ceiling and its owner's daily limit.

    The workflow check and reservation are one conditional UPDATE, so tasks
    running in parallel cannot each see room and jointly overshoot.
    """
    reserved = await session.execute(
        update(Workflow)
        .where(
            Workflow.id == workflow_id,
            Workflow.total_cost_usd + Workflow.reserved_cost_usd + amount <= Workflow.max_cost_usd,
        )
        .values(reserved_cost_usd=Workflow.reserved_cost_usd + amount)
        .execution_options(synchronize_session=False)
    )
    if reserved.rowcount == 0:
        await session.rollback()
        ceiling = await session.scalar(select(Workflow.max_cost_usd).where(Workflow.id == workflow_id))
        raise BudgetExceededError(f"The next model call could take this workflow past its ${ceiling:.2f} ceiling")

    owner = select(Project.owner_id).join(Workflow, Workflow.project_id == Project.id).where(
        Workflow.id == workflow_id
    )
    owners_workflows = select(Workflow.id).join(Project, Workflow.project_id == Project.id).where(
        Project.owner_id == owner.scalar_subquery()
    )
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    spent_today = await session.scalar(
        select(func.coalesce(func.sum(Execution.estimated_cost), 0.0)).where(
            Execution.workflow_id.in_(owners_workflows), Execution.created_at >= midnight
        )
    )
    held = await session.scalar(
        select(func.coalesce(func.sum(Workflow.reserved_cost_usd), 0.0)).where(Workflow.id.in_(owners_workflows))
    )
    if (spent_today or 0.0) + (held or 0.0) > settings.max_daily_cost_usd:
        await session.rollback()
        raise BudgetExceededError(f"The daily spending limit of ${settings.max_daily_cost_usd:.2f} is reached")
    await session.commit()


def _release(amount: float):  # type: ignore[no-untyped-def]
    remaining = Workflow.reserved_cost_usd - amount
    return case((remaining < 0, 0.0), else_=remaining)


async def call_model(
    session: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    model: Model,
    role: str,
    system: str | None,
    messages: list[ChatMessage],
    task_id: uuid.UUID | None = None,
    max_tokens: int = 8192,
    temperature: float | None = None,
    json_mode: bool = False,
    on_delta: Callable[[str], None] | None = None,
) -> CompletionResult:
    provider = get_provider(model.provider.name)

    request = CompletionRequest(
        model=model.remote_id,
        messages=messages,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
        on_delta=on_delta,
    )

    hold = worst_case_cost(model, system, messages, max_tokens)
    await _reserve(session, workflow_id, hold)

    try:
        result = await provider.generate(request)
    except Exception as exc:
        await session.execute(
            update(Workflow)
            .where(Workflow.id == workflow_id)
            .values(reserved_cost_usd=_release(hold))
            .execution_options(synchronize_session=False)
        )
        session.add(
            Execution(
                workflow_id=workflow_id,
                task_id=task_id,
                provider_name=model.provider.name,
                model_name=model.name,
                role=role,
                succeeded=False,
                error=str(exc)[:2000],
            )
        )
        await session.commit()
        log.error(
            "model_call_failed",
            model=model.name,
            provider=model.provider.name,
            role=role,
            error=str(exc),
        )
        raise ProviderError(str(exc)) from exc

    cost = estimate_cost(model, result.input_tokens, result.output_tokens)

    session.add(
        Execution(
            workflow_id=workflow_id,
            task_id=task_id,
            provider_name=model.provider.name,
            model_name=model.name,
            role=role,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.total_tokens,
            estimated_cost=cost,
            latency_ms=result.latency_ms,
            succeeded=True,
        )
    )

    # Roll the spend up onto the workflow so budget checks are a single read.
    #
    # synchronize_session=False matters: the default would expire these
    # columns on any Workflow instance in this session, and a later synchronous
    # read of them would then trigger a lazy load from async context. Callers
    # that need the running total re-read it explicitly instead.
    await session.execute(
        update(Workflow)
        .where(Workflow.id == workflow_id)
        .values(
            total_cost_usd=Workflow.total_cost_usd + cost,
            total_tokens=Workflow.total_tokens + result.total_tokens,
            reserved_cost_usd=_release(hold),
        )
        .execution_options(synchronize_session=False)
    )
    if task_id is not None:
        # Every call made for a task — its agent's attempts and the reviews
        # of them — counts toward what that task cost.
        await session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(
                input_tokens=Task.input_tokens + result.input_tokens,
                output_tokens=Task.output_tokens + result.output_tokens,
                cost_usd=Task.cost_usd + cost,
            )
            .execution_options(synchronize_session=False)
        )
    await session.commit()

    log.info(
        "model_call",
        model=model.name,
        provider=model.provider.name,
        role=role,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=round(cost, 6),
        latency_ms=result.latency_ms,
    )
    return result
