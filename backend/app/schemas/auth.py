"""Auth payloads."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)
    full_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleSignIn(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    # Must equal the one the browser was sent to Google with.
    redirect_uri: str = Field(min_length=1, max_length=2048)


class AuthProviders(BaseModel):
    # Public by design: it is in every link to Google's sign-in page.
    google_client_id: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    role: str
    is_active: bool
    email_verified_at: datetime | None = None
    created_at: datetime
