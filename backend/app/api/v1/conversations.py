"""Conversation threads: each request, and what came back.

A workflow is one turn. Starting a workflow with a conversation_id continues
that thread, and the Commander sees its earlier turns when planning.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CurrentUser, OwnedProject, SessionDep
from app.core.errors import NotFoundError
from app.models.entities import Conversation, Message, Project
from app.schemas.workspace import ConversationDetail, ConversationOut, MessageOut

router = APIRouter(tags=["conversations"])


async def _owned(
    session: SessionDep, user: CurrentUser, conversation_id: uuid.UUID
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation)
        .join(Project, Conversation.project_id == Project.id)
        .where(Conversation.id == conversation_id, Project.owner_id == user.id)
    )
    if conversation is None:
        raise NotFoundError("Conversation not found")
    return conversation


@router.get("/projects/{project_id}/conversations", response_model=list[ConversationOut])
async def list_conversations(
    project: OwnedProject, session: SessionDep
) -> list[Conversation]:
    rows = await session.scalars(
        select(Conversation)
        .where(Conversation.project_id == project.id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(rows)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> ConversationDetail:
    conversation = await _owned(session, user, conversation_id)
    messages = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
    )
    # Built from the plain fields, not model_validate(conversation): that would
    # read the unloaded `messages` relationship and lazy-load from async code.
    return ConversationDetail(
        **ConversationOut.model_validate(conversation).model_dump(),
        messages=[MessageOut.model_validate(m) for m in messages],
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> None:
    """Delete the transcript. Its workflows and their files are kept."""
    await session.delete(await _owned(session, user, conversation_id))
    await session.commit()
