"""Long-term memory: semantic search, keyword fallback, and what feeds it."""

from __future__ import annotations

import os
import zlib

import pytest
from sqlalchemy import select

from app.db.base import EMBEDDING_DIMENSIONS
from app.models.entities import Memory, Project, User
from app.services.memory.embeddings import Embedder, cosine, normalise
from app.services.memory.store import MemoryScope, MemoryStore, keywords


class FakeEmbedder(Embedder):
    """Deterministic bag-of-words vectors: similar wording, similar vector.

    Not semantic — that needs a real model — but it exercises every part of
    the vector path: widths, normalisation, ranking, model bookkeeping.
    """

    def __init__(self, name: str = "fake@768", *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.calls: list[tuple[int, str]] = []

    async def embed(self, texts, *, purpose):
        self.calls.append((len(texts), purpose))
        if self.fail:
            raise RuntimeError("embedding service down")
        vectors = []
        for text in texts:
            vector = [0.0] * EMBEDDING_DIMENSIONS
            for word in keywords(text):
                vector[zlib.crc32(word.encode()) % EMBEDDING_DIMENSIONS] += 1.0
            vectors.append(normalise(vector))
        return vectors


@pytest.fixture
async def project(session) -> Project:
    user = User(email="memory@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(owner_id=user.id, name="Memory")
    session.add(project)
    await session.commit()
    return project


FACTS = [
    ("auth", "Login uses JWT access tokens stored in an httpOnly cookie"),
    ("styling", "Styling is Tailwind CSS with a dark theme toggle in the header"),
    ("db", "Bookings live in a Postgres table with a unique index on slot time"),
]


async def _seed(store: MemoryStore) -> None:
    for key, content in FACTS:
        await store.remember(MemoryScope.PROJECT, key, content)


class TestSemantic:
    async def test_vectors_are_stored_with_their_model(self, session, project) -> None:
        store = MemoryStore(session, project.id, FakeEmbedder())
        memory = await store.remember(MemoryScope.PROJECT, "k", "Tailwind dark theme")

        assert len(memory.embedding) == EMBEDDING_DIMENSIONS
        assert memory.embedding_model == "fake@768"
        assert abs(cosine(memory.embedding, memory.embedding) - 1.0) < 1e-9

    async def test_the_closest_memory_ranks_first(self, session, project) -> None:
        store = MemoryStore(session, project.id, FakeEmbedder())
        await _seed(store)

        hits = await store.search("make the dark theme toggle remember its state", limit=3)

        assert hits[0].memory.key == "styling"
        assert hits[0].method == "semantic"
        assert hits[0].score > hits[1].score

    async def test_queries_and_documents_are_embedded_differently(
        self, session, project
    ) -> None:
        embedder = FakeEmbedder()
        store = MemoryStore(session, project.id, embedder)
        await store.remember(MemoryScope.PROJECT, "k", "something")
        await store.search("something")
        assert [purpose for _, purpose in embedder.calls] == ["document", "query"]

    async def test_vectors_from_another_model_are_never_compared(
        self, session, project
    ) -> None:
        await _seed(MemoryStore(session, project.id, FakeEmbedder("old-model@768")))

        hits = await MemoryStore(session, project.id, FakeEmbedder("new-model@768")).search(
            "dark theme toggle"
        )
        # Found, but by its words: comparing across models would be noise.
        assert hits and all(h.method == "keyword" for h in hits)
        assert hits[0].memory.key == "styling"

    async def test_reindex_moves_old_memories_onto_the_current_model(
        self, session, project
    ) -> None:
        await _seed(MemoryStore(session, project.id, embedder=None))
        store = MemoryStore(session, project.id, FakeEmbedder())

        assert await store.reindex() == 3
        assert await store.reindex() == 0
        hits = await store.search("dark theme toggle")
        assert hits[0].method == "semantic"

    async def test_an_embedding_outage_does_not_lose_the_memory(
        self, session, project
    ) -> None:
        store = MemoryStore(session, project.id, FakeEmbedder(fail=True))
        memory = await store.remember(MemoryScope.PROJECT, "k", "Tailwind dark theme")

        assert memory.embedding is None and memory.embedding_model is None
        # And it is still found.
        assert (await store.search("tailwind theme"))[0].memory.id == memory.id


class TestKeyword:
    async def test_a_sentence_query_finds_matches(self, session, project) -> None:
        """The old search matched the whole query as one substring, so any
        real sentence found nothing."""
        store = MemoryStore(session, project.id, embedder=None)
        await _seed(store)

        hits = await store.search("how do bookings stop double booking a slot?")

        assert hits[0].memory.key == "db"
        assert hits[0].method == "keyword"

    async def test_more_overlap_ranks_higher(self, session, project) -> None:
        store = MemoryStore(session, project.id, embedder=None)
        await _seed(store)
        hits = await store.search("tailwind dark theme header")
        assert hits[0].memory.key == "styling" and hits[0].score == 1.0

    async def test_stopwords_alone_match_nothing(self, session, project) -> None:
        store = MemoryStore(session, project.id, embedder=None)
        await _seed(store)
        assert await store.search("the and with please") == []

    async def test_memories_stay_in_their_project(self, session, project) -> None:
        other = Project(owner_id=project.owner_id, name="Other")
        session.add(other)
        await session.commit()
        await MemoryStore(session, other.id, embedder=None).remember(
            MemoryScope.PROJECT, "x", "Tailwind dark theme"
        )
        assert await MemoryStore(session, project.id, embedder=None).search("tailwind") == []


class TestHousekeeping:
    async def test_remember_upserts_by_key(self, session, project) -> None:
        store = MemoryStore(session, project.id, embedder=None)
        await store.remember(MemoryScope.PROJECT, "k", "first")
        await store.remember(MemoryScope.PROJECT, "k", "second")
        rows = list(await session.scalars(select(Memory)))
        assert [m.content for m in rows] == ["second"]

    async def test_forget(self, session, project) -> None:
        store = MemoryStore(session, project.id, embedder=None)
        memory = await store.remember(MemoryScope.PROJECT, "k", "x")
        assert await store.forget(memory.id)
        assert not await store.forget(memory.id)


@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="set TEST_POSTGRES_URL to run against a real Postgres with pgvector",
)
class TestPgvector:
    """The same search, answered by pgvector's <=> operator and HNSW index."""

    async def test_cosine_search_in_postgres(self) -> None:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        from app.db.base import Base

        engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                user = User(email="pg@example.com", hashed_password="x")
                session.add(user)
                await session.flush()
                project = Project(owner_id=user.id, name="pg")
                session.add(project)
                await session.commit()

                store = MemoryStore(session, project.id, FakeEmbedder())
                await _seed(store)
                hits = await store.search("dark theme toggle in the header", limit=3)

                assert hits[0].memory.key == "styling"
                assert hits[0].method == "semantic"
                assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)

                column = await session.scalar(
                    text(
                        "select format_type(atttypid, atttypmod) from pg_attribute "
                        "where attrelid = 'memories'::regclass and attname = 'embedding'"
                    )
                )
                assert column == f"vector({EMBEDDING_DIMENSIONS})"
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        finally:
            await engine.dispose()
