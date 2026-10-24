"""Sign in with Google: the code exchange, account creation and linking."""

from __future__ import annotations

import time

import jwt
import pytest
from httpx import AsyncClient

from app.api.v1 import auth as auth_api
from app.core.config import settings

CLIENT_ID = "client-123.apps.googleusercontent.com"
REDIRECT = "http://localhost:3000/api/auth/google/callback"


def _id_token(**overrides) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": int(time.time()) + 600,
        "email": "ada@example.com",
        "email_verified": True,
        "name": "Ada Lovelace",
        **overrides,
    }
    return jwt.encode(claims, "google-signs-these-with-its-own-key-not-ours", algorithm="HS256")


class FakeGoogle:
    def __init__(self) -> None:
        self.id_token = _id_token()
        self.status = 200
        self.sent: list[dict] = []

    async def post(self, url: str, data: dict) -> object:
        self.sent.append({"url": url, **data})
        google = self

        class Response:
            status_code = google.status

            @staticmethod
            def json() -> dict:
                return {"id_token": google.id_token, "access_token": "unused"}

        return Response()


@pytest.fixture
def google(monkeypatch) -> FakeGoogle:
    fake = FakeGoogle()
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_client_secret", "shh")

    class Client:
        def __init__(self, **_: object) -> None: ...

        async def __aenter__(self) -> FakeGoogle:
            return fake

        async def __aexit__(self, *_: object) -> None: ...

    monkeypatch.setattr(auth_api.httpx, "AsyncClient", Client)
    return fake


async def _sign_in(client: AsyncClient):
    return await client.post("/api/v1/auth/google", json={"code": "abc", "redirect_uri": REDIRECT})


async def test_providers_hide_google_until_configured(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_client_id", None)
    assert (await client.get("/api/v1/auth/providers")).json() == {"google_client_id": None}
    assert (await _sign_in(client)).status_code == 404

    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_client_secret", "shh")
    assert (await client.get("/api/v1/auth/providers")).json() == {"google_client_id": CLIENT_ID}


async def test_first_sign_in_creates_a_verified_account(client: AsyncClient, google: FakeGoogle) -> None:
    response = await _sign_in(client)
    assert response.status_code == 200, response.text
    assert google.sent[0]["redirect_uri"] == REDIRECT
    assert google.sent[0]["client_secret"] == "shh"

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}
    )
    body = me.json()
    assert body["email"] == "ada@example.com"
    assert body["full_name"] == "Ada Lovelace"
    assert body["email_verified_at"] is not None


async def test_an_existing_password_account_is_the_same_person(
    client: AsyncClient, google: FakeGoogle
) -> None:
    registered = await client.post(
        "/api/v1/auth/register", json={"email": "Ada@Example.com", "password": "a-sufficiently-long-password"}
    )
    assert registered.status_code == 201
    first = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {registered.json()['access_token']}"}
    )

    response = await _sign_in(client)
    assert response.status_code == 200, response.text
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}
    )
    assert me.json()["id"] == first.json()["id"]
    # The password still works too.
    login = await client.post(
        "/api/v1/auth/login", json={"email": "Ada@Example.com", "password": "a-sufficiently-long-password"}
    )
    assert login.status_code == 200


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 10},
        {"email_verified": False},
        {"email": None},
    ],
)
async def test_untrustworthy_identities_are_refused(
    client: AsyncClient, google: FakeGoogle, claims: dict
) -> None:
    google.id_token = _id_token(**claims)
    assert (await _sign_in(client)).status_code == 401


async def test_a_code_google_rejects_signs_no_one_in(client: AsyncClient, google: FakeGoogle) -> None:
    google.status = 400
    assert (await _sign_in(client)).status_code == 401
