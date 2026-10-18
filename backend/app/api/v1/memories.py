"""A project's long-term memory: what it has built and what it should keep doing.

Memories are written automatically when a workflow completes, and can be added
by hand — a standing preference like "always use Tailwind". The Commander
recalls the most relevant ones whenever a new request is planned.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import OwnedProject, SessionDep
from app.core.errors import NotFoundError
from app.models.entities import Memory
from app.schemas.workspace import MemoryCreate, MemoryHit, MemoryOut
from app.services.memory.store import MemoryScope, MemoryStore

router = APIRouter(prefix="/projects/{project_id}/memories", tags=["memory"])


def _out(memory: Memory) -> MemoryOut:
    out = MemoryOut.model_validate(memory)
    out.embedded = memory.embedding is not None
    return out


@router.get("", response_model=list[MemoryOut])
async def list_memories(project: OwnedProject, session: SessionDep) -> list[MemoryOut]:
    rows = await session.scalars(
        select(Memory)
        .where(Memory.project_id == project.id)
        .order_by(Memory.updated_at.desc())
    )
    return [_out(m) for m in rows]


@router.get("/search", response_model=list[MemoryHit])
async def search_memories(
    project: OwnedProject,
    session: SessionDep,
    q: str = Query(min_length=1, max_length=2000),
    limit: int = Query(default=5, ge=1, le=50),
) -> list[MemoryHit]:
    """Find memories by meaning (or by words, when no embedder is set up)."""
    hits = await MemoryStore(session, project.id).search(q, limit=limit)
    return [
        MemoryHit(**_out(h.memory).model_dump(), score=h.score, method=h.method)
        for h in hits
    ]


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def add_memory(
    project: OwnedProject, payload: MemoryCreate, session: SessionDep
) -> MemoryOut:
    memory = await MemoryStore(session, project.id).remember(
        MemoryScope(payload.scope), f"manual:{uuid.uuid4().hex[:12]}", payload.content
    )
    return _out(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_memory(
    project: OwnedProject, memory_id: uuid.UUID, session: SessionDep
) -> None:
    if not await MemoryStore(session, project.id).forget(memory_id):
        raise NotFoundError("Memory not found")


@router.post("/reindex")
async def reindex_memories(
    project: OwnedProject, session: SessionDep
) -> dict[str, int | str]:
    """Embed memories that have no vector from the current embedder."""
    store = MemoryStore(session, project.id)
    if store.embedder is None:
        return {"reindexed": 0, "reason": "no embedding provider is configured"}
    return {"reindexed": await store.reindex(), "embedder": store.embedder.name}
