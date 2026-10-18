"""The audit trail: your own actions, or everyone's for an admin."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models.entities import AuditLog
from app.models.enums import UserRole

router = APIRouter(prefix="/audit-logs", tags=["audit"])


class AuditLogOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    meta: dict[str, Any]
    created_at: datetime


@router.get("", response_model=list[AuditLogOut])
async def list_audit_logs(
    session: SessionDep,
    user: CurrentUser,
    action: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[AuditLog]:
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if user.role != UserRole.ADMIN:
        query = query.where(AuditLog.user_id == user.id)
    if action:
        query = query.where(AuditLog.action == action)
    return list(await session.scalars(query))
