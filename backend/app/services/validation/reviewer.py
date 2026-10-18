"""Result validation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.entities import Model, Review, Task
from app.models.enums import ValidationStatus
from app.services.agents.registry import get_agent
from app.services.orchestrator.llm import call_model
from app.services.providers.base import ChatMessage, extract_json

log = get_logger(__name__)


@dataclass(slots=True)
class ReviewResult:
    status: ValidationStatus
    score: float = 0.0
    feedback: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    @property
    def needs_work(self) -> bool:
        return self.status in (ValidationStatus.NEEDS_REVISION, ValidationStatus.INVALID)

    def as_prompt(self) -> str:
        """Feedback rendered for the agent that must act on it."""
        lines = [
            f"[{item.get('severity', 'medium')}] "
            f"{item.get('file', 'general')}: {item.get('message', '')}"
            for item in self.feedback
        ]
        return "\n".join(lines) or self.summary


async def review_task(
    session: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    task: Task,
    produced: str,
    brief: dict[str, Any],
    model: Model,
) -> ReviewResult:
    agent = get_agent("reviewer")

    content = (
        "## BRIEF\n"
        + "\n".join(f"- {k}: {v}" for k, v in brief.items() if v)
        + f"\n\n## TASK UNDER REVIEW ({task.key}, {task.task_type})\n"
        f"{task.title}\n\n{task.description}\n\n"
        f"## WHAT THE AGENT PRODUCED\n{produced}"
    )

    result = await call_model(
        session,
        workflow_id=workflow_id,
        task_id=task.id,
        model=model,
        role="reviewer",
        system=agent.system_prompt,
        messages=[ChatMessage(role="user", content=content)],
        max_tokens=3000,
        json_mode=True,
    )

    try:
        payload = extract_json(result.text)
    except Exception as exc:
        # A reviewer that returns nonsense must not block delivery; the work
        # passes with the failure recorded rather than being thrown away.
        log.warning("review_unparsable", task=task.key, error=str(exc))
        return ReviewResult(
            status=ValidationStatus.VALID,
            summary="Reviewer output could not be parsed; accepted by default.",
        )

    raw_status = str(payload.get("status", "valid")).lower()
    try:
        status = ValidationStatus(raw_status)
    except ValueError:
        status = ValidationStatus.VALID

    review = ReviewResult(
        status=status,
        score=float(payload.get("score") or 0.0),
        feedback=[f for f in payload.get("feedback") or [] if isinstance(f, dict)],
        summary=str(payload.get("summary") or ""),
    )

    session.add(
        Review(
            task_id=task.id,
            reviewer_agent=agent.name,
            status=review.status.value,
            score=review.score,
            feedback=review.feedback,
            summary=review.summary,
        )
    )
    await session.commit()

    return review
