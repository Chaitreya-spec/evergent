"""Password hashing, JWTs, and encryption of provider credentials."""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

# "reset" and "verify" are single-purpose links sent by email; none of them
# is ever accepted where another type is expected.
TokenType = Literal["access", "refresh", "reset", "verify"]

# bcrypt truncates silently at 72 bytes. Pre-hashing with SHA-256 means a long
# passphrase keeps its full entropy instead of being cut off.
_BCRYPT_MAX_BYTES = 72


def _prepare(password: str) -> bytes:
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        return base64.b64encode(hashlib.sha256(raw).digest())
    return raw


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(password), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # A malformed hash must read as "wrong password", never as an error
        # that leaks which accounts exist.
        return False


def _create_token(
    subject: str, token_type: TokenType, expires_delta: timedelta, **claims: Any
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        # Unique per token so refresh tokens can be revoked individually.
        "jti": secrets.token_urlsafe(16),
        **claims,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(subject: str, **claims: Any) -> str:
    return _create_token(
        subject,
        "access",
        timedelta(minutes=settings.access_token_expire_minutes),
        **claims,
    )


def create_refresh_token(subject: str, **claims: Any) -> str:
    return _create_token(
        subject,
        "refresh",
        timedelta(days=settings.refresh_token_expire_days),
        **claims,
    )


def create_link_token(subject: str, purpose: Literal["reset", "verify"], minutes: int, **claims: Any) -> str:
    return _create_token(subject, purpose, timedelta(minutes=minutes), **claims)


def password_fingerprint(hashed_password: str) -> str:
    """Changes whenever the password does, which is what makes a reset link
    single-use: after it is used the fingerprint no longer matches."""
    return hashlib.sha256(hashed_password.encode("utf-8")).hexdigest()[:16]


class TokenError(Exception):
    """Raised when a token is missing, expired, malformed or the wrong type."""


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token is invalid") from exc

    # An access token must never be accepted where a refresh token is required,
    # or the short expiry of access tokens would be meaningless.
    if payload.get("type") != expected_type:
        raise TokenError(f"Expected a {expected_type} token")

    return payload


# --- Provider credential encryption ----------------------------------------


def _fernet() -> Fernet:
    key = settings.encryption_key
    if not key:
        if settings.is_production:
            raise RuntimeError(
                "ENCRYPTION_KEY must be set in production — provider API keys "
                "cannot be stored without it."
            )
        # Development convenience: derive a stable key from the secret so a
        # restart can still read what it wrote.
        digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest).decode("utf-8")
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Stored credential could not be decrypted — the encryption key has "
            "probably changed."
        ) from exc


def mask_secret(value: str) -> str:
    """What the API returns instead of a credential. Never the real value."""
    if len(value) <= 8:
        return "••••"
    return f"{value[:3]}••••{value[-4:]}"


def generate_encryption_key() -> str:
    """Convenience for operators setting ENCRYPTION_KEY for the first time."""
    return Fernet.generate_key().decode("utf-8")
