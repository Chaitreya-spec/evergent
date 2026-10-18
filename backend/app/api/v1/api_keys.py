"""Personal API keys: programmatic access for scripts and CI.

A key acts as its owner, with the owner's role (a viewer's key is read-only).
It is shown once; only its hash is stored. Managing keys needs a signed-in
session, so a leaked key cannot create more or keep itself alive.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import (
    API_KEY_PREFIX,
    CurrentSession,
    CurrentUser,
    SessionDep,
    hash_api_key,
)
from app.core.errors import ConflictError, NotFoundError
from app.models.entities import ApiKey
from app.schemas.common import ORMModel
from app.services import audit

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

MAX_ACTIVE_KEYS = 20


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120, examples=["CI pipeline"])


class ApiKeyOut(ORMModel):
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    # The only time the key is ever returned.
    key: str


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(user: CurrentUser, session: SessionDep) -> list[ApiKey]:
    rows = await session.scalars(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    )
    return list(rows)


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(
    payload: ApiKeyCreate,
    user: CurrentUser,
    _: CurrentSession,
    session: SessionDep,
    request: Request,
) -> ApiKeyCreated:
    active = await session.scalar(
        select(func.count())
        .select_from(ApiKey)
        .where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
    )
    if (active or 0) >= MAX_ACTIVE_KEYS:
        raise ConflictError(f"At most {MAX_ACTIVE_KEYS} active keys; revoke one first")

    public = secrets.token_hex(4)
    key = f"{API_KEY_PREFIX}{public}_{secrets.token_urlsafe(32)}"
    row = ApiKey(
        user_id=user.id,
        name=payload.name,
        prefix=f"{API_KEY_PREFIX}{public}",
        key_hash=hash_api_key(key),
    )
    session.add(row)
    await session.flush()
    audit.record(
        session, "api_key.create", user_id=user.id, target_type="api_key", target_id=row.id,
        request=request, name=payload.name, prefix=row.prefix,
    )
    await session.commit()
    await session.refresh(row)
    return ApiKeyCreated(**ApiKeyOut.model_validate(row).model_dump(), key=key)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: uuid.UUID,
    user: CurrentUser,
    _: CurrentSession,
    session: SessionDep,
    request: Request,
) -> None:
    row = await session.get(ApiKey, key_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("API key not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        audit.record(
            session, "api_key.revoke", user_id=user.id, target_type="api_key", target_id=row.id,
            request=request, prefix=row.prefix,
        )
        await session.commit()
