"""Long-term memory: what a project has learned, findable by meaning.

Memories are written with an embedding when an embedder is configured, and
searched by cosine similarity — in Postgres through pgvector's index, in SQLite
(tests) in Python. Without an embedder, or for rows written before one existed,
search falls back to keyword overlap. Either way the caller gets ranked results
and does not need to know which path answered.

Embedding failures never fail a write: a memory without a vector is still a
memory, and `reindex` can give it one later.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Float, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.entities import Memory
from app.services.memory.embeddings import Embedder, cosine, get_embedder

log = get_logger(__name__)

# Words too common to say anything about what a memory is about.
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "by",
        "from",
        "at",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "this",
        "that",
        "it",
        "its",
        "into",
        "than",
        "then",
        "also",
        "can",
        "will",
        "should",
        "would",
        "make",
        "add",
        "use",
        "using",
        "build",
        "built",
        "please",
        "want",
        "need",
    ]
)

_UNSET = object()


class MemoryScope(StrEnum):
    CONVERSATION = "conversation"
    TASK = "task"
    PROJECT = "project"
    PREFERENCE = "preference"


@dataclass(slots=True)
class Recalled:
    memory: Memory
    # 0..1. Cosine similarity for semantic hits, keyword overlap otherwise.
    score: float
    method: str  # "semantic" or "keyword"


def keywords(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for word in re.findall(r"[a-z0-9][a-z0-9+#.-]{2,}", text.lower()):
        word = word.strip(".-")
        if len(word) >= 3 and word not in _STOPWORDS:
            seen.setdefault(word, None)
    return list(seen)


class MemoryStore:
    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: Embedder | object | None = _UNSET,
    ):
        self.session = session
        self.project_id = project_id
        self.embedder: Embedder | None = (
            get_embedder() if embedder is _UNSET else embedder  # type: ignore[assignment]
        )

    async def _embed(self, text: str, purpose: str) -> list[float] | None:
        if self.embedder is None:
            return None
        try:
            return (await self.embedder.embed([text], purpose=purpose))[0]  # type: ignore[arg-type]
        except Exception as exc:
            log.warning("embedding_failed", error=str(exc)[:300])
            return None

    async def remember(
        self, scope: MemoryScope, key: str, content: str, **meta: object
    ) -> Memory:
        """Upsert by (scope, key) so repeated writes do not accumulate."""
        memory = await self.session.scalar(
            select(Memory).where(
                Memory.project_id == self.project_id,
                Memory.scope == scope.value,
                Memory.key == key,
            )
        )
        if memory is None:
            memory = Memory(project_id=self.project_id, scope=scope.value, key=key)
            self.session.add(memory)

        memory.content = content
        memory.meta = dict(meta)
        vector = await self._embed(content, "document")
        memory.embedding = vector
        memory.embedding_model = self.embedder.name if vector and self.embedder else None
        await self.session.commit()
        return memory

    async def recall(self, scope: MemoryScope, key: str) -> str | None:
        memory = await self.session.scalar(
            select(Memory).where(
                Memory.project_id == self.project_id,
                Memory.scope == scope.value,
                Memory.key == key,
            )
        )
        return memory.content if memory else None

    def _scoped(self, scope: MemoryScope | None):  # type: ignore[no-untyped-def]
        query = select(Memory).where(Memory.project_id == self.project_id)
        return query.where(Memory.scope == scope.value) if scope else query

    async def search(
        self, query: str, *, scope: MemoryScope | None = None, limit: int = 5
    ) -> list[Recalled]:
        """The memories most relevant to `query`, best first."""
        results: list[Recalled] = []
        semantic_ids: set[uuid.UUID] = set()

        vector = await self._embed(query, "query")
        if vector is not None and self.embedder is not None:
            comparable = self._scoped(scope).where(
                Memory.embedding_model == self.embedder.name
            )
            if self.session.bind.dialect.name == "postgresql":
                # pgvector's cosine distance operator; the HNSW index serves it.
                distance = Memory.embedding.op("<=>", return_type=Float)(vector)
                rows = await self.session.execute(
                    comparable.add_columns(distance.label("distance"))
                    .order_by(distance)
                    .limit(limit)
                )
                hits = [(row.Memory, 1.0 - float(row.distance)) for row in rows]
            else:
                hits = [
                    (m, cosine(m.embedding, vector))
                    for m in await self.session.scalars(comparable)
                    if m.embedding
                ]
                hits.sort(key=lambda hit: hit[1], reverse=True)
                hits = hits[:limit]
            for memory, score in hits:
                semantic_ids.add(memory.id)
                results.append(Recalled(memory, round(score, 4), "semantic"))

        if len(results) < limit:
            # Rows with no comparable vector are still findable by their words.
            for hit in await self._keyword(query, scope, exclude=semantic_ids):
                if len(results) >= limit:
                    break
                results.append(hit)
        return results

    async def _keyword(
        self, query: str, scope: MemoryScope | None, *, exclude: set[uuid.UUID]
    ) -> list[Recalled]:
        """Rank by how many of the query's words each memory contains.

        Matching the whole query as one substring — what this used to do —
        almost never hits for a real sentence.
        """
        words = keywords(query)
        if not words:
            return []
        candidates = await self.session.scalars(
            self._scoped(scope).where(
                or_(*(Memory.content.ilike(f"%{word}%") for word in words))
            )
        )
        scored = []
        for memory in candidates:
            if memory.id in exclude:
                continue
            text = memory.content.lower()
            matched = sum(1 for word in words if word in text)
            scored.append(Recalled(memory, round(matched / len(words), 4), "keyword"))
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored

    async def forget(self, memory_id: uuid.UUID) -> bool:
        memory = await self.session.scalar(
            self._scoped(None).where(Memory.id == memory_id)
        )
        if memory is None:
            return False
        await self.session.delete(memory)
        await self.session.commit()
        return True

    async def reindex(self, batch_size: int = 32) -> int:
        """Embed every memory that has no vector from the current embedder.

        Needed after first configuring an embedder, or after switching to a
        different one, whose vectors are not comparable with the old ones.
        """
        if self.embedder is None:
            return 0
        stale = list(
            await self.session.scalars(
                self._scoped(None).where(
                    or_(
                        Memory.embedding_model.is_(None),
                        Memory.embedding_model != self.embedder.name,
                    )
                )
            )
        )
        for start in range(0, len(stale), batch_size):
            batch = stale[start : start + batch_size]
            vectors = await self.embedder.embed(
                [m.content for m in batch], purpose="document"
            )
            for memory, vector in zip(batch, vectors, strict=True):
                memory.embedding = vector
                memory.embedding_model = self.embedder.name
            await self.session.commit()
        return len(stale)
