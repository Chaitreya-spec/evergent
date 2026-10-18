"""Final synthesis."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Artifact, Model, Task
from app.models.enums import TaskStatus
from app.services.agents.registry import get_agent
from app.services.context.manager import summarise
from app.services.orchestrator.llm import call_model
from app.services.providers.base import ChatMessage

# Budget for the synthesis prompt. Generous but bounded: the synthesizer sees
# more than any other agent, and is the easiest place to blow a context window.
_MAX_TASK_CHARS = 2500
_MAX_ARTIFACT_CHARS = 3000


async def synthesize(
    session: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    goal: str,
    brief: dict[str, Any],
    tasks: list[Task],
    artifacts: list[Artifact],
    unresolved: list[str],
    model: Model,
    team: list[str] | None = None,
) -> str:
    agent = get_agent("synthesizer")

    completed = [t for t in tasks if t.status == TaskStatus.COMPLETED]
    failed = [t for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.SKIPPED)]

    parts: list[str] = [
        f"## ORIGINAL REQUEST\n{goal}",
        "## BRIEF\n" + "\n".join(f"- {k}: {v}" for k, v in brief.items() if v),
        "## PLAN\n"
        + "\n".join(
            f"- {t.key} ({t.task_type}, {t.status}): {t.title}" for t in tasks
        ),
    ]

    if team:
        parts.append(
            "## TEAM\n"
            f"The user limited this job to: {', '.join(team)}. Work outside their "
            "specialities was left out on purpose. Where that leaves a requirement "
            "unmet, say so plainly as a consequence of the team chosen, not as a "
            "failure of the work."
        )

    if completed:
        parts.append(
            "## AGENT OUTPUTS\n"
            + "\n\n".join(
                f"### {t.key} — {t.title}\n{summarise(t.output or '', _MAX_TASK_CHARS)}"
                for t in completed
            )
        )

    if artifacts:
        parts.append(
            "## ARTIFACTS PRODUCED\n"
            + "\n\n".join(
                f"--- {a.path} ---\n{summarise(a.content, _MAX_ARTIFACT_CHARS)}"
                for a in artifacts
            )
        )

    if failed:
        parts.append(
            "## TASKS THAT DID NOT COMPLETE\n"
            + "\n".join(f"- {t.key}: {t.title} — {t.error or t.status}" for t in failed)
        )

    if unresolved:
        parts.append("## UNRESOLVED ISSUES\n" + "\n".join(f"- {u}" for u in unresolved))

    result = await call_model(
        session,
        workflow_id=workflow_id,
        model=model,
        role="synthesizer",
        system=agent.system_prompt,
        messages=[ChatMessage(role="user", content="\n\n".join(parts))],
        max_tokens=8000,
    )
    return result.text
