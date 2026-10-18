"""Registration, sign-in, sessions, password reset and email verification.

Every sign-in creates an AuthSession. Both tokens carry its id, so revoking
the session — logout, a password reset, a stolen refresh token being replayed
— cuts off the access token too, not just future refreshes.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update

from app.api.deps import CurrentSession, CurrentUser, SessionDep
from app.core.config import settings
from app.core.errors import AuthenticationError, ConflictError
from app.core.net import client_ip
from app.core.security import (
    TokenError,
    create_access_token,
    create_link_token,
    create_refresh_token,
    decode_token,
    hash_password,
    password_fingerprint,
    verify_password,
)
from app.models.entities import AuthSession, User
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)
from app.services import audit
from app.services.email import email_configured, send_email

router = APIRouter(prefix="/auth", tags=["auth"])

RESET_MINUTES = 30
VERIFY_MINUTES = 60 * 48


def _client(request: Request) -> tuple[str | None, str | None]:
    ip = client_ip(request)
    return ip, (request.headers.get("user-agent") or "")[:300] or None


async def _open_session(session: SessionDep, user: User, request: Request) -> TokenPair:
    ip, agent = _client(request)
    auth_session = AuthSession(
        user_id=user.id,
        refresh_jti=secrets.token_urlsafe(16),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
        user_agent=agent,
        ip=ip,
        last_used_at=datetime.now(UTC),
    )
    session.add(auth_session)
    await session.flush()
    return _issue(user, auth_session)


def _issue(user: User, auth_session: AuthSession) -> TokenPair:
    subject = str(user.id)
    sid = str(auth_session.id)
    return TokenPair(
        access_token=create_access_token(subject, role=user.role, sid=sid),
        refresh_token=create_refresh_token(subject, sid=sid, jti=auth_session.refresh_jti),
        expires_in=settings.access_token_expire_minutes * 60,
    )


# Emails are sent after the response, never while the caller waits: a slow
# mail server must not slow sign-up, and a password-reset answer must take
# the same time whether or not the account exists.


async def _send_verification(user_id: uuid.UUID, email: str) -> None:
    token = create_link_token(str(user_id), "verify", VERIFY_MINUTES, email=email)
    await send_email(
        email,
        "Confirm your email for Evergent",
        f"Confirm this address by opening:\n\n{settings.frontend_url}/verify-email?token={token}\n\n"
        "The link works for 48 hours.",
    )


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest, session: SessionDep, request: Request, background: BackgroundTasks
) -> TokenPair:
    existing = await session.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise ConflictError("An account with that email already exists")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
    )
    session.add(user)
    await session.flush()
    tokens = await _open_session(session, user, request)
    audit.record(session, "auth.register", user_id=user.id, target_type="user", target_id=user.id, request=request)
    await session.commit()
    if email_configured():
        background.add_task(_send_verification, user.id, user.email)
    return tokens


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, session: SessionDep, request: Request) -> TokenPair:
    user = await session.scalar(select(User).where(User.email == payload.email))

    # Same error whether the account is missing, disabled or the password is
    # wrong, so the endpoint cannot be used to enumerate accounts.
    if user is None or not user.is_active or not verify_password(payload.password, user.hashed_password):
        audit.record(session, "auth.login_failed", request=request, email=payload.email)
        await session.commit()
        raise AuthenticationError("Incorrect email or password")
    if settings.require_email_verification and email_configured() and user.email_verified_at is None:
        raise AuthenticationError("Confirm your email address first — check your inbox for the link")

    tokens = await _open_session(session, user, request)
    audit.record(session, "auth.login", user_id=user.id, request=request)
    await session.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, session: SessionDep, request: Request) -> TokenPair:
    try:
        claims = decode_token(payload.refresh_token, "refresh")
        sid = uuid.UUID(claims["sid"])
    except (TokenError, KeyError, ValueError) as exc:
        raise AuthenticationError("Refresh token is invalid") from exc

    auth_session = await session.get(AuthSession, sid)
    now = datetime.now(UTC)
    if auth_session is None or auth_session.revoked_at is not None:
        raise AuthenticationError("This session has ended — sign in again")
    expires = auth_session.expires_at
    if (expires if expires.tzinfo else expires.replace(tzinfo=UTC)) <= now:
        raise AuthenticationError("This session has expired — sign in again")

    if claims.get("jti") != auth_session.refresh_jti:
        # A refresh token is used once. Seeing an old one again means it was
        # copied: end the session so neither copy works any more.
        auth_session.revoked_at = now
        audit.record(session, "auth.refresh_reused", user_id=auth_session.user_id, target_id=sid, request=request)
        await session.commit()
        raise AuthenticationError("This session has ended — sign in again")

    user = await session.get(User, auth_session.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User no longer exists or is disabled")

    auth_session.refresh_jti = secrets.token_urlsafe(16)
    auth_session.last_used_at = now
    tokens = _issue(user, auth_session)
    await session.commit()
    return tokens


class LogoutRequest(BaseModel):
    # Sign out every device, not just this one.
    everywhere: bool = False


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    user: CurrentUser,
    current: CurrentSession,
    session: SessionDep,
    request: Request,
    payload: LogoutRequest | None = None,
) -> None:
    now = datetime.now(UTC)
    if payload and payload.everywhere:
        await session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    else:
        await session.execute(update(AuthSession).where(AuthSession.id == current).values(revoked_at=now))
    audit.record(
        session, "auth.logout", user_id=user.id, request=request, everywhere=bool(payload and payload.everywhere)
    )
    await session.commit()


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user


# --- Password reset ----------------------------------------------------------


class ResetRequest(BaseModel):
    email: EmailStr


class ResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=10, max_length=256)


class Accepted(BaseModel):
    message: str
    email_delivery: bool


@router.post("/password-reset/request", response_model=Accepted, status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    payload: ResetRequest, session: SessionDep, request: Request, background: BackgroundTasks
) -> Accepted:
    """Always the same answer, so it cannot reveal whether an account exists."""
    user = await session.scalar(select(User).where(User.email == payload.email))
    if user is not None and user.is_active:
        token = create_link_token(
            str(user.id), "reset", RESET_MINUTES, fp=password_fingerprint(user.hashed_password)
        )
        background.add_task(
            send_email,
            user.email,
            "Reset your Evergent password",
            f"Choose a new password here:\n\n{settings.frontend_url}/reset-password?token={token}\n\n"
            f"The link works once, for {RESET_MINUTES} minutes. If you did not ask for this, ignore it.",
        )
        audit.record(session, "auth.password_reset_requested", user_id=user.id, request=request)
        await session.commit()
    return Accepted(
        message="If an account uses that address, a reset link is on its way.",
        email_delivery=email_configured(),
    )


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(payload: ResetConfirm, session: SessionDep, request: Request) -> None:
    try:
        claims = decode_token(payload.token, "reset")
        user = await session.get(User, uuid.UUID(claims["sub"]))
    except (TokenError, KeyError, ValueError) as exc:
        raise AuthenticationError("This reset link is invalid or has expired") from exc
    if user is None or claims.get("fp") != password_fingerprint(user.hashed_password):
        raise AuthenticationError("This reset link has already been used")

    user.hashed_password = hash_password(payload.new_password)
    # Whoever knew the old password is signed out everywhere.
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    audit.record(session, "auth.password_reset", user_id=user.id, request=request)
    await session.commit()


# --- Email verification --------------------------------------------------------


class VerifyConfirm(BaseModel):
    token: str


@router.post("/verify-email/request", response_model=Accepted, status_code=status.HTTP_202_ACCEPTED)
async def request_verification(user: CurrentUser, background: BackgroundTasks) -> Accepted:
    if user.email_verified_at is None:
        background.add_task(_send_verification, user.id, user.email)
    return Accepted(message="A confirmation link is on its way.", email_delivery=email_configured())


@router.post("/verify-email/confirm", response_model=UserOut)
async def confirm_verification(payload: VerifyConfirm, session: SessionDep, request: Request) -> User:
    try:
        claims = decode_token(payload.token, "verify")
        user = await session.get(User, uuid.UUID(claims["sub"]))
    except (TokenError, KeyError, ValueError) as exc:
        raise AuthenticationError("This confirmation link is invalid or has expired") from exc
    if user is None or claims.get("email") != user.email:
        raise AuthenticationError("This confirmation link is for a different address")
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
        audit.record(session, "auth.email_verified", user_id=user.id, request=request)
        await session.commit()
        await session.refresh(user)
    return user
