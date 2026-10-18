"""Password hashing, tokens and credential encryption."""

from __future__ import annotations

import pytest

from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    mask_secret,
    verify_password,
)


class TestPasswords:
    def test_round_trip(self) -> None:
        hashed = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", hashed)
        assert not verify_password("wrong password entirely", hashed)

    def test_hash_is_salted(self) -> None:
        a = hash_password("same password")
        b = hash_password("same password")
        assert a != b, "identical passwords must not produce identical hashes"

    def test_long_password_keeps_full_entropy(self) -> None:
        # bcrypt truncates at 72 bytes. Two passwords sharing a 72-byte prefix
        # must still be distinguishable, or the tail is security theatre.
        base = "x" * 72
        hashed = hash_password(base + "AAAA")
        assert verify_password(base + "AAAA", hashed)
        assert not verify_password(base + "BBBB", hashed)

    def test_malformed_hash_reads_as_wrong_password(self) -> None:
        assert verify_password("anything", "not-a-bcrypt-hash") is False


class TestTokens:
    def test_access_round_trip(self) -> None:
        token = create_access_token("user-123", role="admin")
        claims = decode_token(token, "access")
        assert claims["sub"] == "user-123"
        assert claims["role"] == "admin"

    def test_refresh_is_not_accepted_as_access(self) -> None:
        # Otherwise a long-lived refresh token would work as an access token
        # and the short access expiry would be meaningless.
        token = create_refresh_token("user-123")
        with pytest.raises(TokenError, match="access"):
            decode_token(token, "access")

    def test_tampered_token_rejected(self) -> None:
        token = create_access_token("user-123")
        with pytest.raises(TokenError):
            decode_token(token + "x", "access")

    def test_tokens_are_unique(self) -> None:
        a = decode_token(create_access_token("u"), "access")
        b = decode_token(create_access_token("u"), "access")
        assert a["jti"] != b["jti"]


class TestEncryption:
    def test_round_trip(self) -> None:
        secret = "sk-ant-super-secret-value"
        assert decrypt_secret(encrypt_secret(secret)) == secret

    def test_ciphertext_hides_plaintext(self) -> None:
        secret = "sk-ant-super-secret-value"
        assert secret not in encrypt_secret(secret)

    def test_mask_never_reveals_middle(self) -> None:
        masked = mask_secret("sk-ant-1234567890abcdef")
        assert "567890" not in masked
        assert masked.endswith("cdef")

    def test_short_secret_fully_masked(self) -> None:
        assert mask_secret("abc") == "••••"
