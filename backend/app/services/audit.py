"""The audit trail: who did what, to what, from where.

Entries are added to the caller's session and committed with the change they
describe, so an action and its record succeed or fail together.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.net import client_ip
from app.models.entities import AuditLog

# Never written into an audit entry, whatever a caller passes.
_SECRET_KEYS = {"password", "token", "api_key", "secret", "refresh_token", "access_token"}


def record(
    session: AsyncSession,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: object = None,
    request: Request | None = None,
    **meta: Any,
) -> None:
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            ip=client_ip(request) if request else None,
            user_agent=(request.headers.get("user-agent") or "")[:300] if request else None,
            meta={k: v for k, v in meta.items() if k.lower() not in _SECRET_KEYS},
        )
    )
