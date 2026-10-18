"""Files a user hands the team with a request.

Text only — specs, sample data, existing code — because the agents read them
as text. Size and type are checked here, the bytes go to object storage, and
the planner sees them when the workflow they are attached to is planned.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import PurePosixPath

from fastapi import APIRouter, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select

from app.api.deps import CurrentUser, OwnedProject, SessionDep
from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.models.entities import Attachment, Project
from app.schemas.workspace import AttachmentOut
from app.services import audit
from app.services.storage import get_storage

router = APIRouter(tags=["attachments"])

ALLOWED_SUFFIXES = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".css",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".sql", ".sh", ".go", ".rs", ".java", ".rb",
}  # fmt: skip


async def _owned(
    session: SessionDep, user: CurrentUser, attachment_id: uuid.UUID
) -> Attachment:
    attachment = await session.scalar(
        select(Attachment)
        .join(Project, Attachment.project_id == Project.id)
        .where(Attachment.id == attachment_id, Project.owner_id == user.id)
    )
    if attachment is None:
        raise NotFoundError("Attachment not found")
    return attachment


@router.post(
    "/projects/{project_id}/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload(
    project: OwnedProject,
    file: UploadFile,
    session: SessionDep,
    user: CurrentUser,
    request: Request,
) -> Attachment:
    name = PurePosixPath(file.filename or "").name
    if not name or PurePosixPath(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValidationError(
            f"Only text files can be attached ({', '.join(sorted(ALLOWED_SUFFIXES))})"
        )
    data = await file.read(settings.max_attachment_bytes + 1)
    if len(data) > settings.max_attachment_bytes:
        raise ValidationError(
            f"Attachments are limited to {settings.max_attachment_bytes // 1000} KB"
        )
    if not data:
        raise ValidationError("The file is empty")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("The file is not UTF-8 text") from exc

    attachment_id = uuid.uuid4()
    key = f"projects/{project.id}/attachments/{attachment_id}"
    await get_storage().put(key, data, "text/plain; charset=utf-8")

    attachment = Attachment(
        id=attachment_id,
        project_id=project.id,
        uploaded_by=user.id,
        filename=name[:255],
        content_type="text/plain",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=key,
    )
    session.add(attachment)
    audit.record(
        session,
        "attachment.upload",
        user_id=user.id,
        target_type="attachment",
        target_id=attachment_id,
        request=request,
    )
    await session.commit()
    await session.refresh(attachment)
    return attachment


@router.get("/projects/{project_id}/attachments", response_model=list[AttachmentOut])
async def list_attachments(
    project: OwnedProject, session: SessionDep
) -> list[Attachment]:
    rows = await session.scalars(
        select(Attachment)
        .where(Attachment.project_id == project.id)
        .order_by(Attachment.created_at.desc())
    )
    return list(rows)


@router.get("/attachments/{attachment_id}", response_model=AttachmentOut)
async def get_attachment(
    attachment_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> Attachment:
    return await _owned(session, user, attachment_id)


@router.get("/attachments/{attachment_id}/download")
async def download(
    attachment_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> Response:
    attachment = await _owned(session, user, attachment_id)
    data = await get_storage().get(attachment.storage_key)
    safe = attachment.filename.replace('"', "")
    return Response(
        data,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    attachment_id: uuid.UUID, session: SessionDep, user: CurrentUser, request: Request
) -> None:
    attachment = await _owned(session, user, attachment_id)
    await get_storage().delete(attachment.storage_key)
    audit.record(
        session,
        "attachment.delete",
        user_id=user.id,
        target_type="attachment",
        target_id=attachment_id,
        request=request,
    )
    await session.delete(attachment)
    await session.commit()
