"""Artifact management.

Artifacts are the files a workflow produced. They are addressable on their own
so a client can fetch or download one without pulling the whole workflow.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, OwnedArtifact, SessionDep
from app.models.entities import Artifact, Project, Workflow
from app.schemas.workspace import ArtifactOut, ArtifactSummary

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("", response_model=list[ArtifactSummary])
async def list_artifacts(
    session: SessionDep,
    user: CurrentUser,
    workflow_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> list[ArtifactSummary]:
    """Files across your builds, newest first — without their contents."""
    query = (
        select(Artifact, Workflow.goal, Workflow.project_id)
        .join(Workflow, Artifact.workflow_id == Workflow.id)
        .join(Project, Workflow.project_id == Project.id)
        .where(Project.owner_id == user.id)
    )
    if workflow_id:
        query = query.where(Artifact.workflow_id == workflow_id)
    if project_id:
        query = query.where(Workflow.project_id == project_id)
    rows = await session.execute(query.order_by(Artifact.updated_at.desc()).limit(limit))
    return [
        ArtifactSummary(
            id=a.id,
            workflow_id=a.workflow_id,
            task_id=a.task_id,
            project_id=pid,
            workflow_goal=goal,
            path=a.path,
            version=a.version,
            size=len(a.content),
            updated_at=a.updated_at,
        )
        for a, goal, pid in rows
    ]


@router.get("/{artifact_id}", response_model=ArtifactOut)
async def get_artifact(artifact: OwnedArtifact) -> Artifact:
    return artifact


@router.get("/{artifact_id}/download", response_class=PlainTextResponse)
async def download_artifact(artifact: OwnedArtifact) -> PlainTextResponse:
    """The raw file as a download, rather than wrapped in a JSON envelope."""
    filename = artifact.path.rsplit("/", 1)[-1]
    return PlainTextResponse(
        artifact.content,
        media_type=artifact.content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(artifact: OwnedArtifact, session: SessionDep) -> None:
    await session.delete(artifact)
    await session.commit()
