"""STORAGE_BACKEND=database: attachments kept in the application's database."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.storage import DatabaseStorage, StorageError


@pytest.fixture
def storage(engine) -> DatabaseStorage:
    return DatabaseStorage(async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))


class TestDatabaseStorage:
    async def test_what_is_put_comes_back(self, storage) -> None:
        await storage.put("projects/p1/notes.txt", b"hello", "text/plain; charset=utf-8")
        assert await storage.get("projects/p1/notes.txt") == b"hello"

    async def test_putting_a_key_again_replaces_it(self, storage) -> None:
        await storage.put("a.txt", b"first", "text/plain")
        await storage.put("a.txt", b"second", "text/plain")
        assert await storage.get("a.txt") == b"second"

    async def test_a_missing_key_is_a_storage_error(self, storage) -> None:
        with pytest.raises(StorageError, match="No stored object"):
            await storage.get("never-stored.txt")

    async def test_delete_removes_it(self, storage) -> None:
        await storage.put("gone.txt", b"x", "text/plain")
        await storage.delete("gone.txt")
        with pytest.raises(StorageError):
            await storage.get("gone.txt")

    async def test_deleting_a_missing_key_is_not_an_error(self, storage) -> None:
        """Matches the other backends, so a retried delete never fails."""
        await storage.delete("never-stored.txt")

    async def test_unsafe_keys_are_refused_as_in_every_backend(self, storage) -> None:
        with pytest.raises(StorageError, match="Unsafe"):
            await storage.put("../escape.txt", b"x", "text/plain")

    async def test_bytes_survive_unchanged(self, storage) -> None:
        data = bytes(range(256)) * 64
        await storage.put("binary.bin", data, "application/octet-stream")
        assert await storage.get("binary.bin") == data
