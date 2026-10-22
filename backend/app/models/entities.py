"""SQLAlchemy ORM models.

One module so relationships resolve without import-order gymnastics; the
schema is still grouped by concern below.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import GUID, Base, Embedding, TimestampMixin, UUIDMixin
from app.models.enums import (
    TaskStatus,
    UserRole,
    ValidationStatus,
    WorkflowStatus,
)

# JSONB on Postgres, plain JSON on SQLite so the test suite runs serverless.
JSONType = JSON().with_variant(JSONB(), "postgresql")


# --- Identity ---------------------------------------------------------------


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), default=UserRole.MEMBER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    projects: Mapped[list[Project]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ApiKey(UUIDMixin, TimestampMixin, Base):
    """A caller's key for this API — not a provider credential."""

    __tablename__ = "api_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    # Only the hash is stored; the key itself is shown once at creation.
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="api_keys")


class AuthSession(UUIDMixin, TimestampMixin, Base):
    """One signed-in device. Its id is in every token it issues, so revoking
    the row signs that device out — access tokens included, not just refresh.
    """

    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # The jti of the one refresh token this session will accept next. Rotated
    # on every refresh; presenting an older one means the token was copied.
    refresh_jti: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(64))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(UUIDMixin, Base):
    """Who did what to which resource. Written, never updated."""

    __tablename__ = "audit_logs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


# --- Workspace --------------------------------------------------------------


class Project(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # Per-project ceilings; fall back to the global settings when unset.
    max_cost_usd: Mapped[float | None] = mapped_column(Float)
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    owner: Mapped[User] = relationship(back_populates="projects")
    workflows: Mapped[list[Workflow]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    memories: Mapped[list[Memory]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Conversation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(300))

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# --- Orchestration ----------------------------------------------------------


class Workflow(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "workflows"

    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The thread this request was made in. Earlier turns of it are what make
    # "now add dark mode" mean something.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), default=WorkflowStatus.PENDING, index=True
    )
    routing_strategy: Mapped[str] = mapped_column(String(32), default="balanced")
    # The engineers chosen for this job. Null means the whole roster; a list
    # means only these may be given work, and specialities left off it are
    # not planned at all.
    team: Mapped[list[str] | None] = mapped_column(JSONType)

    # Commander output: objectives, constraints, complexity, stack.
    brief: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    final_result: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    # Budget, copied from project/global defaults at creation so changing a
    # default never retroactively alters a running workflow.
    max_cost_usd: Mapped[float] = mapped_column(Float, default=5.0)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2_000_000)
    max_seconds: Mapped[int] = mapped_column(Integer, default=1800)

    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Worst-case cost of model calls in flight. Calls reserve before they run
    # so parallel tasks cannot jointly overshoot the ceiling.
    reserved_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # Manual routing: this model for every task. Whether another may stand in
    # when it is unreachable is the owner's policy, not the router's call.
    pinned_model: Mapped[str | None] = mapped_column(String(120))
    allow_fallback: Mapped[bool] = mapped_column(Boolean, default=True)

    # Plan approval: when set, the engine stops after planning and waits.
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # A client-chosen key that makes creation safe to retry.
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Touched by the engine while it works. A running workflow whose heartbeat
    # has gone quiet was orphaned by a restart.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="workflows")
    tasks: Mapped[list[Task]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )
    executions: Mapped[list[Execution]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_workflows_project_created", "project_id", "created_at"),
        UniqueConstraint("project_id", "idempotency_key", name="uq_workflow_idempotency"),
    )


class Task(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tasks"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    # Planner-assigned stable handle ("t3"), referenced by dependencies before
    # database ids exist.
    key: Mapped[str] = mapped_column(String(32))

    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(String(32), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.PENDING, index=True)

    required_capabilities: Mapped[list[str]] = mapped_column(JSONType, default=list)
    agent_name: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("models.id", ondelete="SET NULL")
    )
    routing_reason: Mapped[str | None] = mapped_column(Text)

    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    execution_time_ms: Mapped[int] = mapped_column(Integer, default=0)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workflow: Mapped[Workflow] = relationship(back_populates="tasks")
    model: Mapped[Model | None] = relationship()
    reviews: Mapped[list[Review]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("workflow_id", "key", name="uq_task_workflow_key"),
        Index("ix_tasks_workflow_status", "workflow_id", "status"),
    )


class TaskDependency(UUIDMixin, Base):
    __tablename__ = "task_dependencies"

    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )

    __table_args__ = (
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),
    )


# --- Catalog ----------------------------------------------------------------


class Provider(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "providers"

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    # Encrypted at rest; never serialised to the API.
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(String(500))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    # Configured means a credential exists; verified means a call worked.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_ok: Mapped[bool | None] = mapped_column(Boolean)
    verification_error: Mapped[str | None] = mapped_column(Text)
    verified_models: Mapped[list[str]] = mapped_column(JSONType, default=list)

    models: Mapped[list[Model]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )


class Model(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "models"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("providers.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    # Slug sent to the provider, which may differ from `name`.
    remote_id: Mapped[str] = mapped_column(String(200))

    context_window: Mapped[int] = mapped_column(Integer, default=128_000)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=8192)
    cost_per_input_token: Mapped[float] = mapped_column(Float, default=0.0)
    cost_per_output_token: Mapped[float] = mapped_column(Float, default=0.0)

    # capability -> 0..10 score. The router reads this; a benchmark job can
    # rewrite it later without touching routing code.
    capabilities: Mapped[dict[str, float]] = mapped_column(JSONType, default=dict)
    avg_latency_ms: Mapped[int] = mapped_column(Integer, default=3000)
    reliability: Mapped[float] = mapped_column(Float, default=1.0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    provider: Mapped[Provider] = relationship(back_populates="models")

    __table_args__ = (
        UniqueConstraint("provider_id", "name", name="uq_model_provider_name"),
    )


class Agent(UUIDMixin, TimestampMixin, Base):
    """A configurable specialist. Rows override the built-in defaults."""

    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    system_prompt: Mapped[str] = mapped_column(Text)
    capabilities: Mapped[list[str]] = mapped_column(JSONType, default=list)
    allowed_tools: Mapped[list[str]] = mapped_column(JSONType, default=list)
    preferred_models: Mapped[list[str]] = mapped_column(JSONType, default=list)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


# --- Telemetry --------------------------------------------------------------


class Execution(UUIDMixin, TimestampMixin, Base):
    """One model call. The single source of truth for spend and latency."""

    __tablename__ = "executions"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    provider_name: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(32))

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)

    succeeded: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)

    workflow: Mapped[Workflow] = relationship(back_populates="executions")


class ToolCall(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tool_calls"

    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class Artifact(UUIDMixin, TimestampMixin, Base):
    """A file produced by the workflow, owned by exactly one task."""

    __tablename__ = "artifacts"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    path: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(100), default="text/plain")
    version: Mapped[int] = mapped_column(Integer, default=1)

    workflow: Mapped[Workflow] = relationship(back_populates="artifacts")

    __table_args__ = (
        UniqueConstraint("workflow_id", "path", name="uq_artifact_workflow_path"),
    )


class Review(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "reviews"

    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    reviewer_agent: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default=ValidationStatus.VALID)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    feedback: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    summary: Mapped[str | None] = mapped_column(Text)

    task: Mapped[Task] = relationship(back_populates="reviews")


class Memory(UUIDMixin, TimestampMixin, Base):
    """Project-scoped long-term memory, searchable by meaning via pgvector."""

    __tablename__ = "memories"

    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(32), default="project", index=True)
    key: Mapped[str] = mapped_column(String(200), index=True)
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    # Null when no embedding provider was configured at write time; such rows
    # are still found by keyword search, and gain a vector when reindexed.
    embedding: Mapped[list[float] | None] = mapped_column(Embedding())
    embedding_model: Mapped[str | None] = mapped_column(String(120))

    project: Mapped[Project] = relationship(back_populates="memories")

    __table_args__ = (
        Index(
            "ix_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class WorkflowEvent(Base):
    """A workflow event, kept so a client that reconnects can catch up.

    The integer sequence orders events across concurrent writers and is the
    cursor a client sends back (SSE Last-Event-ID). The UUID is the event's
    identity, which lets a client drop a copy delivered twice.
    """

    __tablename__ = "workflow_events"

    seq: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    event_id: Mapped[str] = mapped_column(String(32), unique=True)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(48))
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    __table_args__ = (Index("ix_workflow_events_workflow_seq", "workflow_id", "seq"),)


class Attachment(UUIDMixin, TimestampMixin, Base):
    """A file a user handed the team. The bytes live in object storage."""

    __tablename__ = "attachments"

    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("workflows.id", ondelete="SET NULL"), index=True
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)


class StoredObject(TimestampMixin, Base):
    """The bytes behind a storage key, for STORAGE_BACKEND=database.

    Kept apart from Attachment, like a file on disk or an object in a bucket,
    so the storage backends stay interchangeable.
    """

    __tablename__ = "stored_objects"

    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(100))
    data: Mapped[bytes] = mapped_column(LargeBinary)
