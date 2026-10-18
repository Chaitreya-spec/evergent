"""Models, providers, usage and executions."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import encrypt_secret, mask_secret
from app.models.entities import Execution, Model, Project, Provider, Workflow
from app.schemas.catalog import (
    ExecutionOut,
    ModelOut,
    ModelStatsOut,
    ProviderCreate,
    ProviderOut,
    ProviderUpdate,
    UsageDay,
    UsageOut,
)
from app.services import audit
from app.services.providers.registry import (
    PROVIDER_FACTORIES,
    credential_source,
    get_provider,
    load_credentials,
)
from app.services.router.stats import refresh_model_stats

router = APIRouter(tags=["catalog"])


@router.get("/models", response_model=list[ModelOut])
async def list_models(session: SessionDep, _: CurrentUser) -> list[ModelOut]:
    rows = await session.scalars(
        select(Model).options(selectinload(Model.provider)).order_by(Model.name)
    )

    def listed(m: Model) -> bool | None:
        if not m.provider.verification_ok:
            return None
        return m.remote_id in set(m.provider.verified_models or [])

    return [
        ModelOut(
            listed_by_provider=listed(m),
            id=m.id,
            name=m.name,
            display_name=m.display_name,
            remote_id=m.remote_id,
            provider_name=m.provider.name,
            context_window=m.context_window,
            max_output_tokens=m.max_output_tokens,
            cost_per_input_token=m.cost_per_input_token,
            cost_per_output_token=m.cost_per_output_token,
            capabilities=m.capabilities,
            avg_latency_ms=m.avg_latency_ms,
            reliability=m.reliability,
            is_enabled=m.is_enabled,
        )
        for m in rows
    ]


@router.post("/models/refresh-stats", response_model=list[ModelStatsOut])
async def refresh_stats(session: SessionDep, _: AdminUser) -> list[ModelStatsOut]:
    """Recompute every model's latency and reliability from past executions.

    The engine does this per workflow for the models it used; this recomputes
    the whole catalog, which is what you want after importing history or
    changing the window constants. Models with no executions keep their seeded
    priors and are omitted from the response.
    """
    updated = await refresh_model_stats(session)
    return [
        ModelStatsOut(name=name, avg_latency_ms=latency, reliability=reliability)
        for name, (latency, reliability) in sorted(updated.items())
    ]


def _provider_out(p: Provider) -> ProviderOut:
    source = credential_source(p.name)
    return ProviderOut(
        id=p.id,
        name=p.name,
        display_name=p.display_name,
        base_url=p.base_url,
        is_enabled=p.is_enabled,
        # Only ever a mask. The credential never leaves the server.
        api_key_preview=mask_secret(p.encrypted_api_key) if p.encrypted_api_key else None,
        model_count=len(p.models),
        configured=source is not None and p.is_enabled,
        credential_source=source,
        verified_at=p.verified_at,
        verification_ok=p.verification_ok,
        verification_error=p.verification_error,
        verified_model_count=len(p.verified_models or []),
    )


async def _owned_provider(session: SessionDep, provider_id: uuid.UUID) -> Provider:
    provider = await session.scalar(
        select(Provider)
        .options(selectinload(Provider.models))
        .where(Provider.id == provider_id)
    )
    if provider is None:
        raise NotFoundError("Provider not found")
    return provider


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(session: SessionDep, _: CurrentUser) -> list[ProviderOut]:
    rows = await session.scalars(
        select(Provider).options(selectinload(Provider.models)).order_by(Provider.name)
    )
    return [_provider_out(p) for p in rows]


@router.post(
    "/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED
)
async def create_provider(
    payload: ProviderCreate, session: SessionDep, admin: AdminUser, request: Request
) -> ProviderOut:
    if payload.name not in PROVIDER_FACTORIES:
        # A row with no adapter behind it would look configured and never work.
        raise ValidationError(
            f"There is no adapter for '{payload.name}'. "
            f"Supported: {', '.join(sorted(PROVIDER_FACTORIES))}"
        )
    existing = await session.scalar(select(Provider).where(Provider.name == payload.name))
    if existing is not None:
        raise ConflictError(f"Provider '{payload.name}' already exists")

    provider = Provider(
        name=payload.name,
        display_name=payload.display_name,
        base_url=payload.base_url,
        is_enabled=payload.is_enabled,
        encrypted_api_key=encrypt_secret(payload.api_key) if payload.api_key else None,
    )
    session.add(provider)
    audit.record(
        session,
        "provider.create",
        user_id=admin.id,
        target_type="provider",
        target_id=payload.name,
        request=request,
    )
    await session.commit()
    await load_credentials(session)
    return _provider_out(await _owned_provider(session, provider.id))


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: uuid.UUID,
    payload: ProviderUpdate,
    session: SessionDep,
    admin: AdminUser,
    request: Request,
) -> ProviderOut:
    provider = await _owned_provider(session, provider_id)
    changed = payload.model_dump(exclude_unset=True)
    if "display_name" in changed and payload.display_name:
        provider.display_name = payload.display_name
    if "base_url" in changed:
        provider.base_url = payload.base_url or None
    if "is_enabled" in changed and payload.is_enabled is not None:
        provider.is_enabled = payload.is_enabled
    if "api_key" in changed:
        provider.encrypted_api_key = (
            encrypt_secret(payload.api_key) if payload.api_key else None
        )
    if {"api_key", "base_url"} & changed.keys():
        # A new credential has not been proved yet.
        provider.verified_at = None
        provider.verification_ok = None
        provider.verification_error = None
        provider.verified_models = []
    audit.record(
        session,
        "provider.update",
        user_id=admin.id,
        target_type="provider",
        target_id=provider.name,
        request=request,
        fields=sorted(changed),
    )
    await session.commit()
    await load_credentials(session)
    return _provider_out(await _owned_provider(session, provider_id))


@router.post("/providers/{provider_id}/verify", response_model=ProviderOut)
async def verify_provider(
    provider_id: uuid.UUID, session: SessionDep, admin: AdminUser, request: Request
) -> ProviderOut:
    """Make a real, token-free call (listing models) and record the result."""
    provider = await _owned_provider(session, provider_id)
    await load_credentials(session)
    client = get_provider(provider.name)

    ok, error, models = False, None, []
    if not provider.is_enabled:
        error = "This provider is disabled"
    elif not client.is_configured():
        error = "No credential is configured"
    else:
        try:
            models = await asyncio.wait_for(client.verify(), timeout=30)
            ok = True
        except TimeoutError:
            error = "The provider did not answer within 30 seconds"
        except NotImplementedError as exc:
            error = str(exc)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]

    provider.verified_at = datetime.now(UTC)
    provider.verification_ok = ok
    provider.verification_error = error
    provider.verified_models = models[:500]
    audit.record(
        session,
        "provider.verify",
        user_id=admin.id,
        target_type="provider",
        target_id=provider.name,
        request=request,
        ok=ok,
    )
    await session.commit()
    return _provider_out(await _owned_provider(session, provider_id))


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: uuid.UUID, session: SessionDep, admin: AdminUser, request: Request
) -> None:
    provider = await session.get(Provider, provider_id)
    if provider is None:
        raise NotFoundError("Provider not found")
    audit.record(
        session,
        "provider.delete",
        user_id=admin.id,
        target_type="provider",
        target_id=provider.name,
        request=request,
    )
    await session.delete(provider)
    await session.commit()
    await load_credentials(session)


@router.get("/usage", response_model=UsageOut)
async def usage(
    session: SessionDep,
    user: CurrentUser,
    project_id: uuid.UUID | None = None,
    days: int = Query(30, ge=1, le=365),
) -> UsageOut:
    base = (
        select(Execution)
        .join(Workflow, Execution.workflow_id == Workflow.id)
        .join(Project, Workflow.project_id == Project.id)
        .where(Project.owner_id == user.id)
    )
    if project_id:
        base = base.where(Workflow.project_id == project_id)

    today = datetime.now(UTC).date()
    window_start = today - timedelta(days=days - 1)

    def day_of(row: Execution) -> date:
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
        return created.astimezone(UTC).date()

    # Every figure covers the same window, so the totals and the chart agree.
    rows = [r for r in await session.scalars(base) if day_of(r) >= window_start]

    by_model: dict[str, float] = defaultdict(float)
    by_provider: dict[str, float] = defaultdict(float)
    by_role: dict[str, float] = defaultdict(float)
    per_day: dict[str, list[float]] = defaultdict(lambda: [0.0, 0, 0])
    for row in rows:
        by_model[row.model_name] += row.estimated_cost
        by_provider[row.provider_name] += row.estimated_cost
        by_role[row.role] += row.estimated_cost
        bucket = per_day[day_of(row).isoformat()]
        bucket[0] += row.estimated_cost
        bucket[1] += row.total_tokens
        bucket[2] += 1

    return UsageOut(
        days=days,
        total_cost_usd=round(sum(r.estimated_cost for r in rows), 6),
        total_tokens=sum(r.total_tokens for r in rows),
        input_tokens=sum(r.input_tokens for r in rows),
        output_tokens=sum(r.output_tokens for r in rows),
        call_count=len(rows),
        by_model={k: round(v, 6) for k, v in by_model.items()},
        by_provider={k: round(v, 6) for k, v in by_provider.items()},
        by_role={k: round(v, 6) for k, v in by_role.items()},
        failed_calls=sum(1 for r in rows if not r.succeeded),
        daily=[
            UsageDay(day=day, cost_usd=round(v[0], 6), tokens=int(v[1]), calls=int(v[2]))
            for day, v in sorted(per_day.items())
        ],
        spent_today_usd=round(per_day.get(today.isoformat(), [0.0])[0], 6),
        daily_limit_usd=settings.max_daily_cost_usd,
    )


@router.get("/executions", response_model=list[ExecutionOut])
async def list_executions(
    session: SessionDep,
    user: CurrentUser,
    workflow_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[Execution]:
    base = (
        select(Execution)
        .join(Workflow, Execution.workflow_id == Workflow.id)
        .join(Project, Workflow.project_id == Project.id)
        .where(Project.owner_id == user.id)
    )
    if workflow_id:
        base = base.where(Execution.workflow_id == workflow_id)

    rows = await session.scalars(base.order_by(Execution.created_at.desc()).limit(limit))
    return list(rows)
