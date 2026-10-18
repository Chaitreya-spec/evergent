"""Sessions, logout, refresh rotation, password reset and verification."""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.entities import AuditLog, AuthSession

PASSWORD = "a-sufficiently-long-password"


async def _register(client: AsyncClient, email: str = "sam@example.com") -> dict:
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    return response.json()


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def outbox(monkeypatch) -> list[dict]:
    """Emails the app tried to send, with SMTP treated as configured."""
    sent: list[dict] = []

    async def fake_send(to: str, subject: str, body: str) -> bool:
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    for module in ("app.api.v1.auth",):
        monkeypatch.setattr(f"{module}.send_email", fake_send)
        monkeypatch.setattr(f"{module}.email_configured", lambda: True)
    return sent


def _link_token(email: dict) -> str:
    return re.search(r"token=(\S+)", email["body"]).group(1)


def _sent(outbox: list[dict], subject_start: str) -> list[dict]:
    return [m for m in outbox if m["subject"].startswith(subject_start)]


class TestSessions:
    async def test_logout_ends_the_access_token_at_once(
        self, client: AsyncClient
    ) -> None:
        tokens = await _register(client)
        assert (
            await client.get("/api/v1/auth/me", headers=bearer(tokens["access_token"]))
        ).status_code == 200

        assert (
            await client.post(
                "/api/v1/auth/logout", headers=bearer(tokens["access_token"])
            )
        ).status_code == 204

        # Not just future refreshes: the access token itself stops working.
        assert (
            await client.get("/api/v1/auth/me", headers=bearer(tokens["access_token"]))
        ).status_code == 401
        refreshed = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert refreshed.status_code == 401

    async def test_logout_leaves_other_devices_signed_in(
        self, client: AsyncClient
    ) -> None:
        laptop = await _register(client)
        phone = (
            await client.post(
                "/api/v1/auth/login",
                json={"email": "sam@example.com", "password": PASSWORD},
            )
        ).json()
        await client.post("/api/v1/auth/logout", headers=bearer(laptop["access_token"]))
        assert (
            await client.get("/api/v1/auth/me", headers=bearer(phone["access_token"]))
        ).status_code == 200

    async def test_logout_everywhere(self, client: AsyncClient) -> None:
        laptop = await _register(client)
        phone = (
            await client.post(
                "/api/v1/auth/login",
                json={"email": "sam@example.com", "password": PASSWORD},
            )
        ).json()
        await client.post(
            "/api/v1/auth/logout",
            headers=bearer(laptop["access_token"]),
            json={"everywhere": True},
        )
        assert (
            await client.get("/api/v1/auth/me", headers=bearer(phone["access_token"]))
        ).status_code == 401

    async def test_refresh_rotates_and_a_replayed_token_ends_the_session(
        self, client: AsyncClient, session
    ) -> None:
        first = await _register(client)
        second = (
            await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
            )
        ).json()
        assert second["refresh_token"] != first["refresh_token"]
        assert (
            await client.get("/api/v1/auth/me", headers=bearer(second["access_token"]))
        ).status_code == 200

        # The first refresh token was already used. Using it again means
        # someone copied it: the whole session ends, the thief's and the user's.
        replay = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )
        assert replay.status_code == 401
        assert (
            await client.get("/api/v1/auth/me", headers=bearer(second["access_token"]))
        ).status_code == 401
        assert (
            await session.scalar(
                select(AuditLog).where(AuditLog.action == "auth.refresh_reused")
            )
        ) is not None

    async def test_an_access_token_is_not_a_refresh_token(
        self, client: AsyncClient
    ) -> None:
        tokens = await _register(client)
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]}
        )
        assert response.status_code == 401

    async def test_each_sign_in_is_a_session(self, client: AsyncClient, session) -> None:
        await _register(client)
        await client.post(
            "/api/v1/auth/login", json={"email": "sam@example.com", "password": PASSWORD}
        )
        assert len(list(await session.scalars(select(AuthSession)))) == 2


class TestAudit:
    async def test_failed_logins_are_recorded_without_the_password(
        self, client: AsyncClient, session
    ) -> None:
        await _register(client)
        await client.post(
            "/api/v1/auth/login",
            json={"email": "sam@example.com", "password": "wrong-password-here"},
        )
        entry = await session.scalar(
            select(AuditLog).where(AuditLog.action == "auth.login_failed")
        )
        assert entry.meta == {"email": "sam@example.com"}
        assert "wrong-password" not in str(entry.meta)


class TestPasswordReset:
    async def test_the_answer_never_reveals_whether_an_account_exists(
        self, client: AsyncClient, outbox
    ) -> None:
        await _register(client)
        known = await client.post(
            "/api/v1/auth/password-reset/request", json={"email": "sam@example.com"}
        )
        unknown = await client.post(
            "/api/v1/auth/password-reset/request", json={"email": "nobody@example.com"}
        )
        assert known.status_code == unknown.status_code == 202
        assert known.json() == unknown.json()
        assert [m["to"] for m in _sent(outbox, "Reset")] == ["sam@example.com"]

    async def test_reset_works_once_and_signs_out_everywhere(
        self, client: AsyncClient, outbox
    ) -> None:
        tokens = await _register(client)
        await client.post(
            "/api/v1/auth/password-reset/request", json={"email": "sam@example.com"}
        )
        token = _link_token(_sent(outbox, "Reset")[0])

        body = {"token": token, "new_password": "a-brand-new-password"}
        assert (
            await client.post("/api/v1/auth/password-reset/confirm", json=body)
        ).status_code == 204
        # Single use: the password changed, so the link's fingerprint no longer matches.
        again = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={**body, "new_password": "yet-another-pass"},
        )
        assert again.status_code == 401

        assert (
            await client.get("/api/v1/auth/me", headers=bearer(tokens["access_token"]))
        ).status_code == 401
        old = await client.post(
            "/api/v1/auth/login", json={"email": "sam@example.com", "password": PASSWORD}
        )
        new = await client.post(
            "/api/v1/auth/login",
            json={"email": "sam@example.com", "password": "a-brand-new-password"},
        )
        assert old.status_code == 401 and new.status_code == 200

    async def test_other_tokens_are_not_reset_tokens(self, client: AsyncClient) -> None:
        tokens = await _register(client)
        response = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={
                "token": tokens["access_token"],
                "new_password": "a-brand-new-password",
            },
        )
        assert response.status_code == 401


class TestEmailIsSentAfterTheAnswer:
    async def test_the_reset_answer_does_not_wait_for_the_mail_server(
        self, client: AsyncClient, session, monkeypatch
    ) -> None:
        """Sending inside the request would make the answer slower for real
        accounts than for unknown ones — revealing which addresses exist."""
        from fastapi import BackgroundTasks
        from starlette.requests import Request

        from app.api.v1 import auth as auth_api

        await _register(client)
        sent: list[str] = []

        async def record(to: str, subject: str, body: str) -> bool:
            sent.append(to)
            return True

        monkeypatch.setattr(auth_api, "send_email", record)
        background = BackgroundTasks()
        request = Request({"type": "http", "client": ("127.0.0.1", 1), "headers": [], "method": "POST", "path": "/"})

        answer = await auth_api.request_password_reset(
            auth_api.ResetRequest(email="sam@example.com"), session, request, background
        )
        assert answer.message.startswith("If an account uses that address")
        assert sent == [] and len(background.tasks) == 1  # queued, not sent

        await background()
        assert sent == ["sam@example.com"]


class TestVerification:
    async def test_the_link_confirms_the_address(
        self, client: AsyncClient, outbox
    ) -> None:
        tokens = await _register(client)
        assert len(_sent(outbox, "Confirm")) == 1
        me = (
            await client.get("/api/v1/auth/me", headers=bearer(tokens["access_token"]))
        ).json()
        assert me["email_verified_at"] is None

        confirmed = await client.post(
            "/api/v1/auth/verify-email/confirm", json={"token": _link_token(_sent(outbox, "Confirm")[0])}
        )
        assert (
            confirmed.status_code == 200
            and confirmed.json()["email_verified_at"] is not None
        )

    async def test_without_email_delivery_nothing_is_claimed(
        self, client: AsyncClient
    ) -> None:
        tokens = await _register(client)
        response = await client.post(
            "/api/v1/auth/verify-email/request", headers=bearer(tokens["access_token"])
        )
        assert response.status_code == 202
        assert response.json()["email_delivery"] is False


class TestViewerRole:
    async def test_a_viewer_can_look_but_not_change_anything(
        self, client: AsyncClient, session
    ) -> None:
        from app.models.entities import User
        from app.models.enums import UserRole

        tokens = await _register(client, "viewer@example.com")
        user = await session.scalar(select(User).where(User.email == "viewer@example.com"))
        user.role = UserRole.VIEWER
        await session.commit()
        headers = bearer(tokens["access_token"])

        assert (await client.get("/api/v1/projects", headers=headers)).status_code == 200
        refused = await client.post("/api/v1/projects", headers=headers, json={"name": "Nope"})
        assert refused.status_code == 403
        assert "read-only" in refused.json()["message"]
        # Ending its own session is still allowed.
        assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
