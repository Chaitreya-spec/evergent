"""The workflow's files, exactly as every reader and writer sees them.

Agents write files two ways — in their final answer, and mid-turn through the
write_file tool. Both go through `write` here, so exclusive ownership is
enforced in one place: a task may write only the paths it was assigned, which
is what stops parallel agents silently overwriting each other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Artifact, Task


class UnsafePath(ValueError):
    pass


def normalise_path(path: str) -> str:
    """A clean workspace-relative path, or UnsafePath.

    Only a literal "./" prefix is removed. The previous version used
    lstrip("./"), which strips CHARACTERS — so ".env.example" became
    "env.example" and every dotfile write was refused as unowned.
    """
    cleaned = str(path).strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if not cleaned or cleaned.startswith("/"):
        raise UnsafePath(f"{path!r} is not a relative workspace path")
    parts = PurePosixPath(cleaned).parts
    if ".." in parts:
        raise UnsafePath(f"{path!r} escapes the workspace")
    return str(PurePosixPath(*parts))


def owned_paths(task: Task) -> set[str]:
    owned: set[str] = set()
    for raw in task.input_payload.get("owns") or []:
        try:
            owned.add(normalise_path(raw))
        except UnsafePath:
            continue
    return owned


@dataclass(slots=True)
class WriteOutcome:
    path: str | None = None
    error: str | None = None


class WorkflowWorkspace:
    def __init__(self, session: AsyncSession, workflow_id: uuid.UUID) -> None:
        self.session = session
        self.workflow_id = workflow_id

    async def listing(self) -> list[dict[str, object]]:
        rows = await self.session.execute(
            select(Artifact.path, func.length(Artifact.content), Artifact.version)
            .where(Artifact.workflow_id == self.workflow_id)
            .order_by(Artifact.path)
        )
        return [{"path": p, "chars": n, "version": v} for p, n, v in rows]

    async def read(self, path: str) -> str | None:
        return await self.session.scalar(
            select(Artifact.content).where(
                Artifact.workflow_id == self.workflow_id,
                Artifact.path == normalise_path(path),
            )
        )

    async def files(self) -> list[tuple[str, str]]:
        rows = await self.session.execute(
            select(Artifact.path, Artifact.content).where(
                Artifact.workflow_id == self.workflow_id
            )
        )
        return [(p, c) for p, c in rows]

    async def write(self, task: Task, path: str, content: str) -> WriteOutcome:
        """Write one file for `task`. Refused, not raised, when not allowed."""
        try:
            clean = normalise_path(path)
        except UnsafePath as exc:
            return WriteOutcome(error=str(exc))

        owned = owned_paths(task)
        if owned and clean not in owned:
            return WriteOutcome(
                error=f"{clean} is not owned by {task.key}. You may write: "
                + ", ".join(sorted(owned))
            )

        existing = await self.session.scalar(
            select(Artifact).where(
                Artifact.workflow_id == self.workflow_id, Artifact.path == clean
            )
        )
        if existing is None:
            self.session.add(
                Artifact(
                    workflow_id=self.workflow_id,
                    task_id=task.id,
                    path=clean,
                    content=content,
                )
            )
        else:
            existing.content = content
            existing.task_id = task.id
            existing.version += 1
        await self.session.commit()
        return WriteOutcome(path=clean)
