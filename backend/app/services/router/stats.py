"""Feed real execution outcomes back into the routing inputs.

Every model call is already recorded as an Execution row, but nothing read
those rows back, so `avg_latency_ms` and `reliability` sat at their seeded
defaults forever. Reliability defaults to 1.0 for every model, which made its
term in the routing score an identical constant across all candidates — it
could not influence a decision at all. This closes that loop: measured speed
and measured failure rate, derived from what actually happened.

Capability scores are deliberately NOT touched here. Whether a model wrote
*good* frontend code is not something an execution row knows; that needs a
benchmark job with graded outputs.
"""

from __future__ import annotations

import uuid
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.entities import Execution, Model

log = get_logger(__name__)

# How far back to look. Bounded windows matter: a model that was flaky last
# month and is solid now should be able to earn its score back.
RELIABILITY_WINDOW = 100
LATENCY_WINDOW = 50

# Optimistic smoothing, in pseudo-calls. Without it a model that fails its
# very first call drops to reliability 0 and is never chosen again, so a single
# transient 503 would permanently retire it.
PRIOR_WEIGHT = 5.0
PRIOR_RELIABILITY = 1.0


async def refresh_model_stats(
    session: AsyncSession, *, model_names: list[str] | None = None
) -> dict[str, tuple[int, float]]:
    """Recompute latency and reliability from the execution log.

    Returns {model_name: (avg_latency_ms, reliability)} for models that had
    enough data to change. Models with no executions are left untouched — no
    data is not evidence of slowness.
    """
    query = select(Model)
    if model_names is not None:
        if not model_names:
            return {}
        query = query.where(Model.name.in_(model_names))

    updated: dict[str, tuple[int, float]] = {}

    for model in await session.scalars(query):
        rows = (
            await session.execute(
                select(Execution.latency_ms, Execution.succeeded)
                .where(Execution.model_name == model.name)
                # id breaks ties: rows written in the same transaction share a
                # timestamp, and the order still has to be deterministic.
                .order_by(Execution.created_at.desc(), Execution.id.desc())
                .limit(RELIABILITY_WINDOW)
            )
        ).all()

        if not rows:
            continue

        successes = sum(1 for row in rows if row.succeeded)
        model.reliability = round(
            (successes + PRIOR_WEIGHT * PRIOR_RELIABILITY) / (len(rows) + PRIOR_WEIGHT),
            4,
        )

        # Only successful calls, and only ones that recorded a duration: a
        # failed call's latency is usually a fast rejection, which would make a
        # broken model look like the quickest one in the pool.
        timings = [
            row.latency_ms for row in rows if row.succeeded and row.latency_ms > 0
        ][:LATENCY_WINDOW]
        if timings:
            # Median, not mean — one 60-second outlier should not redefine how
            # fast a model is.
            model.avg_latency_ms = int(median(timings))

        updated[model.name] = (model.avg_latency_ms, model.reliability)

    if updated:
        await session.commit()
        log.info("routing_stats_refreshed", models=len(updated))
    return updated


async def refresh_stats_for_workflow(
    session: AsyncSession, workflow_id: uuid.UUID
) -> dict[str, tuple[int, float]]:
    """Refresh only the models a given workflow actually called."""
    names = list(
        await session.scalars(
            select(Execution.model_name)
            .where(Execution.workflow_id == workflow_id)
            .distinct()
        )
    )
    return await refresh_model_stats(session, model_names=names)
