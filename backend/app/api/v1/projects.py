"""Projects."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, OwnedProject, SessionDep
from app.core.config import settings
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.models.entities import Attachment, Project, Workflow
from app.models.enums import WorkflowStatus
from app.schemas.common import Page
from app.schemas.workspace import ProjectCreate, ProjectOut, ProjectUpdate
from app.services import audit
from app.services.storage import get_storage

log = get_logger(__name__)

# A project cannot be deleted from under a workflow that is still working.
BUSY = (
    WorkflowStatus.PLANNING,
    WorkflowStatus.RUNNING,
    WorkflowStatus.VALIDATING,
    WorkflowStatus.SYNTHESIZING,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, session: SessionDep, user: CurrentUser
) -> Project:
    project = Project(
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
        max_cost_usd=payload.max_cost_usd or settings.max_workflow_cost_usd,
        max_tokens=payload.max_tokens or settings.max_workflow_tokens,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


@router.get("", response_model=Page[ProjectOut])
async def list_projects(
    session: SessionDep,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[ProjectOut]:
    total = await session.scalar(
        select(func.count()).select_from(Project).where(Project.owner_id == user.id)
    )
    rows = await session.scalars(
        select(Project)
        .where(Project.owner_id == user.id)
        .order_by(Project.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[ProjectOut.model_validate(p) for p in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project: OwnedProject) -> Project:
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(project: OwnedProject, payload: ProjectUpdate, session: SessionDep) -> Project:
    """Change what is given. Budgets apply to workflows created afterwards."""
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "name" and not value:
            continue
        setattr(project, field, value)
    await session.commit()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project: OwnedProject, session: SessionDep, user: CurrentUser, request: Request
) -> None:
    busy = await session.scalar(
        select(func.count())
        .select_from(Workflow)
        .where(Workflow.project_id == project.id, Workflow.status.in_(BUSY))
    )
    if busy:
        raise ConflictError("A workflow in this project is still running; cancel it first")

    # The rows cascade with the project; the stored files must be removed too.
    keys = list(
        await session.scalars(
            select(Attachment.storage_key).where(Attachment.project_id == project.id)
        )
    )
    audit.record(
        session,
        "project.delete",
        user_id=user.id,
        target_type="project",
        target_id=project.id,
        request=request,
        name=project.name,
    )
    await session.delete(project)
    await session.commit()

    storage = get_storage()
    for key in keys:
        try:
            await storage.delete(key)
        except Exception as exc:  # the row is gone; a leftover blob is only logged
            log.warning("attachment_blob_delete_failed", key=key, error=str(exc)[:200])
