"""Shared FastAPI dependencies: auth, session, ownership checks."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, NotFoundError, PermissionError_
from app.core.security import TokenError, decode_token
from app.db.session import get_session
from app.models.entities import (
    ApiKey,
    Artifact,
    AuthSession,
    Project,
    Task,
    User,
    Workflow,
)
from app.models.enums import UserRole

SessionDep = Annotated[AsyncSession, Depends(get_session)]


# Personal API keys start with this, which is how they are told apart from
# session tokens (JWTs never do).
API_KEY_PREFIX = "evg_"
# last_used_at is written at most this often, not on every request.
_KEY_TOUCH_INTERVAL = timedelta(seconds=60)


def hash_api_key(key: str) -> str:
    """Keys are 256 random bits, so a fast hash is enough — and lets a key be
    looked up by its hash."""
    return hashlib.sha256(key.encode()).hexdigest()


async def _authenticate_key(session: AsyncSession, key: str) -> User:
    row = await session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(key)))
    if row is None or row.revoked_at is not None:
        raise AuthenticationError("This API key is not valid")
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User no longer exists or is disabled")
    now = datetime.now(UTC)
    last = row.last_used_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if last is None or now - last > _KEY_TOUCH_INTERVAL:
        row.last_used_at = now
        await session.commit()
    return user


async def _authenticate(
    session: AsyncSession, authorization: str | None
) -> tuple[User, uuid.UUID | None]:
    """The caller, and their session — None when they used an API key."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("Missing bearer token")

    credential = authorization.split(" ", 1)[1].strip()
    if credential.startswith(API_KEY_PREFIX):
        return await _authenticate_key(session, credential), None

    try:
        payload = decode_token(credential, "access")
    except TokenError as exc:
        raise AuthenticationError(str(exc)) from exc

    try:
        user_id = uuid.UUID(payload["sub"])
        session_id = uuid.UUID(payload["sid"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Token is not a session token") from exc

    # The session is checked on every request, so signing out takes effect at
    # once rather than when the access token happens to expire.
    auth_session = await session.get(AuthSession, session_id)
    if auth_session is None or auth_session.revoked_at is not None or auth_session.user_id != user_id:
        raise AuthenticationError("This session has ended — sign in again")

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User no longer exists or is disabled")
    return user, session_id


# What a read-only account may still do to its own session.
_VIEWER_WRITES = ("/auth/logout", "/auth/verify-email/request")


async def get_current_user(
    request: Request,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    user = (await _authenticate(session, authorization))[0]
    # Viewers can look at everything they own but change nothing — in
    # particular, they cannot start work that spends money.
    if (
        user.role == UserRole.VIEWER
        and request.method not in ("GET", "HEAD", "OPTIONS")
        and not request.url.path.endswith(_VIEWER_WRITES)
    ):
        raise PermissionError_("This account is read-only")
    return user


async def get_current_session(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> uuid.UUID:
    """The signed-in session. Routes that need one (signing out, managing API
    keys) refuse API keys: a leaked key must not be able to mint more."""
    session_id = (await _authenticate(session, authorization))[1]
    if session_id is None:
        raise PermissionError_("This needs a signed-in session; an API key cannot do it")
    return session_id


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentSession = Annotated[uuid.UUID, Depends(get_current_session)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise PermissionError_("This endpoint requires an admin role")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


async def get_owned_project(
    project_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> Project:
    """A project belonging to someone else is simply not found."""
    project = await session.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == user.id)
    )
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def get_owned_workflow(
    workflow_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> Workflow:
    workflow = await session.scalar(
        select(Workflow)
        .join(Project, Workflow.project_id == Project.id)
        .where(Workflow.id == workflow_id, Project.owner_id == user.id)
    )
    if workflow is None:
        raise NotFoundError("Workflow not found")
    return workflow


async def get_owned_task(
    task_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> Task:
    """A task reachable only through a workflow the caller owns."""
    task = await session.scalar(
        select(Task)
        .join(Workflow, Task.workflow_id == Workflow.id)
        .join(Project, Workflow.project_id == Project.id)
        .where(Task.id == task_id, Project.owner_id == user.id)
    )
    if task is None:
        raise NotFoundError("Task not found")
    return task


async def get_owned_artifact(
    artifact_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> Artifact:
    artifact = await session.scalar(
        select(Artifact)
        .join(Workflow, Artifact.workflow_id == Workflow.id)
        .join(Project, Workflow.project_id == Project.id)
        .where(Artifact.id == artifact_id, Project.owner_id == user.id)
    )
    if artifact is None:
        raise NotFoundError("Artifact not found")
    return artifact


OwnedProject = Annotated[Project, Depends(get_owned_project)]
OwnedWorkflow = Annotated[Workflow, Depends(get_owned_workflow)]
OwnedTask = Annotated[Task, Depends(get_owned_task)]
OwnedArtifact = Annotated[Artifact, Depends(get_owned_artifact)]
