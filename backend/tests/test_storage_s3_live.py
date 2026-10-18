"""S3 storage against a real S3-compatible server.

    TEST_S3_ENDPOINT=http://127.0.0.1:8333 pytest tests/test_storage_s3_live.py

(`docker compose -f docker-compose.dev.yml --profile s3 up -d s3` starts one.)
The rest of the suite covers S3Storage with a stand-in client; this proves
the real requests are accepted by a real server.
"""

from __future__ import annotations

import io
import os
import uuid

import pytest
from httpx import AsyncClient

from app.services.storage import S3Storage, StorageError

ENDPOINT = os.environ.get("TEST_S3_ENDPOINT")
pytestmark = pytest.mark.skipif(not ENDPOINT, reason="set TEST_S3_ENDPOINT to an S3-compatible server")


@pytest.fixture
def s3_storage(monkeypatch):

    from app.core.config import settings

    bucket = f"evergent-test-{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(settings, "s3_bucket", bucket)
    monkeypatch.setattr(settings, "s3_endpoint_url", ENDPOINT)
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_access_key_id", os.environ.get("TEST_S3_KEY", "dev"))
    monkeypatch.setattr(settings, "s3_secret_access_key", os.environ.get("TEST_S3_SECRET", "dev"))
    storage = S3Storage.from_settings()
    storage.client.create_bucket(Bucket=bucket)
    for target in ("app.api.v1.attachments", "app.api.v1.projects", "app.services.orchestrator.engine"):
        monkeypatch.setattr(f"{target}.get_storage", lambda: storage)
    yield storage
    listed = storage.client.list_objects_v2(Bucket=bucket).get("Contents", [])
    for item in listed:
        storage.client.delete_object(Bucket=bucket, Key=item["Key"])
    storage.client.delete_bucket(Bucket=bucket)


async def test_round_trip_on_a_real_server(s3_storage: S3Storage) -> None:
    await s3_storage.put("projects/p/attachments/a", b"hello s3", "text/plain")
    assert await s3_storage.get("projects/p/attachments/a") == b"hello s3"
    await s3_storage.delete("projects/p/attachments/a")
    with pytest.raises(StorageError):
        await s3_storage.get("projects/p/attachments/a")


async def test_attachments_live_in_the_bucket(
    s3_storage: S3Storage, auth_client: AsyncClient
) -> None:
    project_id = (await auth_client.post("/api/v1/projects", json={"name": "S3"})).json()["id"]
    uploaded = await auth_client.post(
        f"/api/v1/projects/{project_id}/attachments",
        files={"file": ("brief.md", io.BytesIO(b"# The brief"), "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment_id = uploaded.json()["id"]
    key = f"projects/{project_id}/attachments/{attachment_id}"
    assert await s3_storage.get(key) == b"# The brief"

    downloaded = await auth_client.get(f"/api/v1/attachments/{attachment_id}/download")
    assert downloaded.content == b"# The brief"

    # Deleting the project removes the object from the bucket too.
    assert (await auth_client.delete(f"/api/v1/projects/{project_id}")).status_code == 204
    with pytest.raises(StorageError):
        await s3_storage.get(key)
