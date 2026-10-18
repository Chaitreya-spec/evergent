"""Project, workflow and task payloads."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import RoutingStrategy, TaskType
from app.schemas.common import ORMModel


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    max_cost_usd: float | None = Field(default=None, gt=0, le=1000)
    max_tokens: int | None = Field(default=None, gt=0)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    max_cost_usd: float | None = Field(default=None, gt=0, le=1000)
    max_tokens: int | None = Field(default=None, gt=0)


class ProjectOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    max_cost_usd: float | None
    max_tokens: int | None
    created_at: datetime


class WorkflowCreate(BaseModel):
    project_id: uuid.UUID
    goal: str = Field(min_length=10, max_length=8000)
    routing_strategy: RoutingStrategy = RoutingStrategy.BALANCED
    max_cost_usd: float | None = Field(default=None, gt=0, le=1000)
    # Start executing immediately, or create it paused for review first.
    autostart: bool = True
    # Continue an existing thread, so this request is planned in light of the
    # earlier ones. Omitted, the request starts a new conversation.
    conversation_id: uuid.UUID | None = None
    # The engineers to put on this job, by agent name. Omitted, the whole
    # roster is available. Given, only these get work, and specialities left
    # off are not planned at all.
    team: list[str] | None = Field(default=None, min_length=1, max_length=40)
    # Plan first and wait for approval before any task runs.
    require_approval: bool = False
    # Manual routing: one catalog model (by name) for every task.
    model: str | None = Field(default=None, max_length=120)
    # Whether another model may stand in when that one is unreachable.
    allow_fallback: bool = True
    # Files uploaded to the project, handed to the planner as context.
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)


class WorkflowOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    conversation_id: uuid.UUID | None
    team: list[str] | None
    goal: str
    status: str
    routing_strategy: str
    brief: dict[str, Any] | None
    final_result: str | None
    error: str | None
    total_cost_usd: float
    total_tokens: int
    max_cost_usd: float
    requires_approval: bool
    approved_at: datetime | None
    pinned_model: str | None = None
    allow_fallback: bool = True
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class WorkflowEventOut(BaseModel):
    id: str
    seq: int
    event: str
    workflow_id: str
    timestamp: str
    data: dict[str, Any]


class TaskOut(ORMModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    key: str
    title: str
    description: str
    task_type: str
    status: str
    priority: int
    agent_name: str | None
    routing_reason: str | None
    output: str | None
    error: str | None
    retry_count: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    execution_time_ms: int
    started_at: datetime | None
    completed_at: datetime | None


class TaskDetail(TaskOut):
    depends_on: list[str] = Field(default_factory=list)
    owns: list[str] = Field(default_factory=list)
    model_name: str | None = None


class TaskCreate(BaseModel):
    workflow_id: uuid.UUID
    key: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1)
    task_type: TaskType
    # Which agent's desk it goes to. Omitted, whoever handles the type takes it.
    agent: str | None = None
    priority: int = Field(default=5, ge=1, le=10)
    depends_on: list[str] = Field(default_factory=list)
    owns: list[str] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    """Every field optional: a PATCH changes only what it names."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, min_length=1)
    priority: int | None = Field(default=None, ge=1, le=10)
    owns: list[str] | None = None
    # Move the task to a different agent's desk.
    agent: str | None = None
    # Pin one catalog model (by name) for this task; "" removes the pin.
    model: str | None = None


class ArtifactOut(ORMModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    task_id: uuid.UUID | None
    path: str
    content: str
    content_type: str
    version: int
    updated_at: datetime


class ArtifactSummary(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    task_id: uuid.UUID | None
    project_id: uuid.UUID
    workflow_goal: str
    path: str
    version: int
    size: int
    updated_at: datetime


class ReviewOut(ORMModel):
    id: uuid.UUID
    reviewer_agent: str
    status: str
    score: float
    feedback: list[dict[str, Any]]
    summary: str | None


class WorkflowReviewOut(ReviewOut):
    task_id: uuid.UUID
    task_key: str
    created_at: datetime


class ToolCallOut(ORMModel):
    id: uuid.UUID
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    succeeded: bool
    error: str | None
    duration_ms: int
    created_at: datetime


class MessageOut(ORMModel):
    id: uuid.UUID
    role: str
    content: str
    meta: dict[str, Any]
    created_at: datetime


class ConversationOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = Field(default_factory=list)


class MemoryCreate(BaseModel):
    # A standing instruction ("always use Tailwind") or a fact about the
    # project. Either is recalled when later requests are planned.
    content: str = Field(min_length=1, max_length=8000)
    scope: Literal["project", "preference"] = "preference"


class MemoryOut(ORMModel):
    id: uuid.UUID
    scope: str
    key: str
    content: str
    meta: dict[str, Any]
    embedded: bool = False
    created_at: datetime
    updated_at: datetime


class MemoryHit(MemoryOut):
    score: float
    method: str


class AttachmentOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    workflow_id: uuid.UUID | None
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime


class WorkflowToolCallOut(ToolCallOut):
    task_id: uuid.UUID
    task_key: str
