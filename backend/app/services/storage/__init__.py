"""Where uploaded files live.

Local disk in development, any S3-compatible bucket (AWS S3, Cloudflare R2,
MinIO, ...) in production, or the application's own database on hosts that
offer neither. Callers see three operations and never a path or a bucket, so
swapping backends changes configuration, not code.
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.entities import StoredObject

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,299}$")


class StorageError(Exception):
    pass


def check_key(key: str) -> str:
    """Keys are generated server-side; this refuses anything that could
    escape the storage root even so."""
    if not _SAFE_KEY.match(key) or ".." in key.split("/"):
        raise StorageError(f"Unsafe storage key: {key!r}")
    return key


class Storage(ABC):
    name: str

    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    async def get(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...


class LocalStorage(Storage):
    name = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / check_key(key)).resolve()
        if self.root not in path.parents:
            raise StorageError(f"Unsafe storage key: {key!r}")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise StorageError(f"No stored object {key!r}") from exc

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)


class S3Storage(Storage):
    name = "s3"

    def __init__(self, bucket: str, client, encryption: str | None = "AES256") -> None:  # type: ignore[no-untyped-def]
        self.bucket = bucket
        self.client = client
        self.encryption = encryption or None

    @classmethod
    def from_settings(cls) -> S3Storage:
        import boto3

        if not settings.s3_bucket:
            raise StorageError("STORAGE_BACKEND=s3 needs S3_BUCKET")
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            # Unset means boto3's own chain: environment, profile, or the
            # instance's role — which is better than a key in configuration.
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )
        return cls(settings.s3_bucket, client, settings.s3_server_side_encryption)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=check_key(key),
            Body=data,
            ContentType=content_type,
            **({"ServerSideEncryption": self.encryption} if self.encryption else {}),
        )

    async def get(self, key: str) -> bytes:
        def read() -> bytes:
            response = self.client.get_object(Bucket=self.bucket, Key=check_key(key))
            return response["Body"].read()

        try:
            return await asyncio.to_thread(read)
        except Exception as exc:
            raise StorageError(f"Could not read {key!r}: {exc}") from exc

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self.client.delete_object, Bucket=self.bucket, Key=check_key(key)
        )


class DatabaseStorage(Storage):
    """Objects kept in the application's own database.

    For platforms where the container's disk is wiped on every deploy and no
    bucket is set up. Attachments are small text (MAX_ATTACHMENT_BYTES), so
    they sit comfortably in Postgres — and database backups then include them.
    """

    name = "database"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        async with self.session_factory() as session:
            await session.merge(StoredObject(key=check_key(key), content_type=content_type, data=data))
            await session.commit()

    async def get(self, key: str) -> bytes:
        async with self.session_factory() as session:
            stored = await session.get(StoredObject, check_key(key))
        if stored is None:
            raise StorageError(f"No stored object {key!r}")
        return stored.data

    async def delete(self, key: str) -> None:
        async with self.session_factory() as session:
            await session.execute(delete(StoredObject).where(StoredObject.key == check_key(key)))
            await session.commit()


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    if settings.storage_backend == "s3":
        return S3Storage.from_settings()
    if settings.storage_backend == "database":
        from app.db.session import SessionFactory

        return DatabaseStorage(SessionFactory)
    return LocalStorage(settings.storage_local_root)
