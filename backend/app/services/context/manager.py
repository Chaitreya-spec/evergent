"""Context management.

Each subtask gets only what it needs. Sending the whole conversation to every
model is both expensive and actively harmful — irrelevant context degrades the
answer as much as it inflates the bill.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.entities import Artifact, Task

# Rough bytes-per-token used for budgeting. Deliberately conservative: it is
# better to under-fill the window than to be rejected for overflowing it.
CHARS_PER_TOKEN = 4


@dataclass(slots=True)
class ContextBudget:
    context_window: int
    max_output_tokens: int
    # Fraction of the remaining window context may occupy.
    fill_ratio: float = 0.6

    @property
    def max_context_chars(self) -> int:
        usable = max(0, self.context_window - self.max_output_tokens)
        return int(usable * self.fill_ratio * CHARS_PER_TOKEN)


def summarise(text: str, limit: int) -> str:
    """Head-and-tail excerpt. Beginnings and endings carry the most meaning."""
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head - 40
    return f"{text[:head]}\n\n…[{len(text) - limit} characters omitted]…\n\n{text[-tail:]}"


class ContextManager:
    """Assembles the prompt context for one task."""

    def __init__(self, budget: ContextBudget):
        self.budget = budget

    def build(
        self,
        *,
        brief: dict,
        task: Task,
        dependency_outputs: list[tuple[str, str]],
        dependency_artifacts: list[Artifact],
        review_feedback: str | None = None,
    ) -> str:
        budget = self.budget.max_context_chars
        sections: list[str] = []

        # The task's own instructions are never trimmed — everything else is
        # negotiable, this is not.
        sections.append(
            f"## YOUR TASK ({task.key}, type: {task.task_type})\n"
            f"{task.title}\n\n{task.description}"
        )

        owns = task.input_payload.get("owns") or []
        if owns:
            sections.append(
                "## FILES YOU OWN (write exactly these)\n"
                + "\n".join(f"- {p}" for p in owns)
            )

        if review_feedback:
            sections.append(
                "## REVIEW FEEDBACK\n"
                "A previous attempt was rejected. Fix these and return the "
                f"complete files again:\n{review_feedback}"
            )

        fixed = sum(len(s) for s in sections)
        remaining = max(0, budget - fixed)

        # Brief next: cheap and orients everything else.
        brief_text = (
            "## PROJECT BRIEF\n"
            + "\n".join(f"- {k}: {v}" for k, v in brief.items() if v)
        )
        brief_text = summarise(brief_text, min(len(brief_text), remaining // 4))
        sections.insert(0, brief_text)
        remaining -= len(brief_text)

        # Artifacts a dependency actually produced are worth more than prose
        # about them, so they are given the larger share.
        if dependency_artifacts:
            share = remaining // max(1, len(dependency_artifacts))
            rendered = [
                f"--- {a.path} ---\n{summarise(a.content, share)}"
                for a in dependency_artifacts
            ]
            block = "## FILES FROM YOUR DEPENDENCIES (match these exactly)\n" + "\n\n".join(
                rendered
            )
            sections.append(block)
            remaining -= len(block)

        if dependency_outputs and remaining > 200:
            share = remaining // max(1, len(dependency_outputs))
            rendered = [
                f"### {key}\n{summarise(text, share)}" for key, text in dependency_outputs
            ]
            sections.append("## WHAT YOUR DEPENDENCIES REPORTED\n" + "\n\n".join(rendered))

        return "\n\n".join(sections)
