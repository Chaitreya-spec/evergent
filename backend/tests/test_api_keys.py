"""Personal API keys."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select

from app.models.entities import ApiKey, AuditLog, User
from app.models.enums import UserRole


def key_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


async def _create(client: AsyncClient, name: str = "CI") -> dict:
    response = await client.post("/api/v1/api-keys", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def test_a_key_acts_as_its_owner(auth_client: AsyncClient, client: AsyncClient, session) -> None:
    created = await _create(auth_client)
    key = created["key"]
    assert key.startswith("evg_") and created["prefix"] == key[: len(created["prefix"])]

    # A separate client with no session at all.
    me = await client.get("/api/v1/auth/me", headers=key_headers(key))
    assert me.status_code == 200
    made = await client.post("/api/v1/projects", headers=key_headers(key), json={"name": "Via key"})
    assert made.status_code == 201
    listed = await auth_client.get("/api/v1/projects")
    assert "Via key" in [p["name"] for p in listed.json()["items"]]

    row = await session.scalar(select(ApiKey))
    assert row.last_used_at is not None
    assert row.key_hash != key and key not in str(row.__dict__)  # only the hash is stored


async def test_the_key_is_shown_once(auth_client: AsyncClient) -> None:
    await _create(auth_client)
    listed = (await auth_client.get("/api/v1/api-keys")).json()
    assert len(listed) == 1 and "key" not in listed[0]


async def test_a_revoked_key_stops_working(auth_client: AsyncClient, client: AsyncClient, session) -> None:
    created = await _create(auth_client)
    assert (await client.get("/api/v1/auth/me", headers=key_headers(created["key"]))).status_code == 200
    assert (await auth_client.delete(f"/api/v1/api-keys/{created['id']}")).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=key_headers(created["key"]))).status_code == 401
    actions = {a.action for a in await session.scalars(select(AuditLog))}
    assert {"api_key.create", "api_key.revoke"} <= actions


async def test_a_key_cannot_mint_keys_or_sign_out(auth_client: AsyncClient, client: AsyncClient) -> None:
    key = (await _create(auth_client))["key"]
    minted = await client.post("/api/v1/api-keys", headers=key_headers(key), json={"name": "more"})
    assert minted.status_code == 403
    assert (await client.post("/api/v1/auth/logout", headers=key_headers(key))).status_code == 403


async def test_unknown_and_malformed_keys_are_refused(client: AsyncClient) -> None:
    for bad in ("evg_deadbeef_nope", "evg_", "evg_x" * 50):
        assert (await client.get("/api/v1/auth/me", headers=key_headers(bad))).status_code == 401


async def test_a_viewers_key_is_read_only(auth_client: AsyncClient, client: AsyncClient, session) -> None:
    key = (await _create(auth_client))["key"]
    user = await session.scalar(select(User))
    user.role = UserRole.VIEWER
    await session.commit()
    assert (await client.get("/api/v1/projects", headers=key_headers(key))).status_code == 200
    refused = await client.post("/api/v1/projects", headers=key_headers(key), json={"name": "x"})
    assert refused.status_code == 403


async def test_keys_are_private(auth_client: AsyncClient, client: AsyncClient) -> None:
    created = await _create(auth_client)
    other = await client.post(
        "/api/v1/auth/register", json={"email": "other@example.com", "password": "long-enough-password"}
    )
    headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    assert (await client.get("/api/v1/api-keys", headers=headers)).json() == []
    assert (await client.delete(f"/api/v1/api-keys/{created['id']}", headers=headers)).status_code == 404
