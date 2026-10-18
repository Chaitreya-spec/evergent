"""The workflow state machine.

Every status change goes through `transition`, so an impossible move — running
a cancelled workflow, approving one that was never planned — is refused in one
place instead of being prevented by scattered checks.
"""

from __future__ import annotations

from app.core.errors import ConflictError
from app.models.entities import Workflow
from app.models.enums import WorkflowStatus as S

# Failure is reachable from every state that has work in flight, which is why
# it appears in each live state's set rather than being special-cased.
ALLOWED: dict[S, frozenset[S]] = {
    S.PENDING: frozenset({S.PLANNING, S.RUNNING, S.PAUSED, S.CANCELLED, S.FAILED}),
    S.PLANNING: frozenset({S.AWAITING_APPROVAL, S.RUNNING, S.PAUSED, S.CANCELLED, S.FAILED}),
    S.AWAITING_APPROVAL: frozenset({S.PENDING, S.PLANNING, S.CANCELLED}),
    S.RUNNING: frozenset({S.VALIDATING, S.SYNTHESIZING, S.PAUSED, S.CANCELLED, S.FAILED}),
    S.VALIDATING: frozenset({S.RUNNING, S.SYNTHESIZING, S.PAUSED, S.CANCELLED, S.FAILED}),
    S.SYNTHESIZING: frozenset({S.COMPLETED, S.PAUSED, S.CANCELLED, S.FAILED}),
    S.PAUSED: frozenset({S.PENDING, S.CANCELLED, S.FAILED}),
    # Finished workflows can be queued again: that is how retried and
    # hand-added tasks get run.
    S.FAILED: frozenset({S.PENDING}),
    S.CANCELLED: frozenset({S.PENDING}),
    S.COMPLETED: frozenset({S.PENDING}),
}


class InvalidTransition(ConflictError):
    pass


def can_transition(current: str, target: S) -> bool:
    return target == S(current) or target in ALLOWED[S(current)]


def transition(workflow: Workflow, target: S) -> None:
    """Move `workflow` to `target`, or refuse with a 409 that says why."""
    current = S(workflow.status)
    if not can_transition(current, target):
        raise InvalidTransition(
            f"A {current.value.replace('_', ' ')} workflow cannot become "
            f"{target.value.replace('_', ' ')}"
        )
    workflow.status = target
