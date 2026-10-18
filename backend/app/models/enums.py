"""Domain enumerations shared by ORM models and API schemas."""

from __future__ import annotations

from enum import StrEnum


class WorkflowStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    # Planned, and waiting for a person to approve the plan before any work.
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    VALIDATING = "validating"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


TERMINAL_WORKFLOW_STATUSES = frozenset(
    {
        WorkflowStatus.COMPLETED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    }
)


class TaskStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class TaskType(StrEnum):
    REQUIREMENTS = "requirements"
    RESEARCH = "research"
    CODING = "coding"
    ARCHITECTURE = "architecture"
    DESIGN = "design"
    FRONTEND = "frontend"
    BACKEND = "backend"
    DATABASE = "database"
    AUTHENTICATION = "authentication"
    INTEGRATION = "integration"
    TESTING = "testing"
    DEBUGGING = "debugging"
    SECURITY = "security"
    DOCUMENTATION = "documentation"
    REVIEW = "review"
    SYNTHESIS = "synthesis"


class Capability(StrEnum):
    """What a model can do. The router matches these against task needs."""

    REASONING = "reasoning"
    CODING = "coding"
    FRONTEND = "frontend"
    BACKEND = "backend"
    DEBUGGING = "debugging"
    MATHEMATICS = "mathematics"
    RESEARCH = "research"
    WRITING = "writing"
    VISION = "vision"
    LONG_CONTEXT = "long_context"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_USE = "tool_use"


class RoutingStrategy(StrEnum):
    CHEAPEST = "cheapest"
    FASTEST = "fastest"
    HIGHEST_QUALITY = "highest_quality"
    BALANCED = "balanced"
    CUSTOM = "custom"
    # A person chose the model; the router only checks it can run.
    MANUAL = "manual"


class ValidationStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    NEEDS_REVISION = "needs_revision"


class UserRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class ExecutionEvent(StrEnum):
    """Events streamed over SSE while a workflow runs."""

    WORKFLOW_CREATED = "workflow_created"
    WORKFLOW_STARTED = "workflow_started"
    PLANNING_STARTED = "planning_started"
    PLAN_READY = "plan_ready"
    PLAN_APPROVED = "plan_approved"
    TASK_CREATED = "task_created"
    TASK_QUEUED = "task_queued"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    MODEL_SELECTED = "model_selected"
    RETRY_STARTED = "retry_started"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TESTS_STARTED = "tests_started"
    TESTS_COMPLETED = "tests_completed"
    TESTS_SKIPPED = "tests_skipped"
    VALIDATION_STARTED = "validation_started"
    REVIEW_COMPLETED = "review_completed"
    ARTIFACT_CREATED = "artifact_created"
    SYNTHESIS_STARTED = "synthesis_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    WORKFLOW_PAUSED = "workflow_paused"
    WORKFLOW_CANCELLED = "workflow_cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"
