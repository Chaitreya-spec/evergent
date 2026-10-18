"""Task understanding and decomposition."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.models.entities import Model
from app.models.enums import TaskType
from app.services.agents.roster import Roster, builtin_roster
from app.services.orchestrator.llm import call_model
from app.services.providers.base import ChatMessage, extract_json

log = get_logger(__name__)

COMMANDER_PROMPT = """You are the Commander of a multi-model engineering room.

Your job is UNDERSTANDING, not solving. You never write code or plans. You read
the user's goal and restate it as a precise specification.

Return a single JSON object:
{
  "objective": "one clear sentence restating what is being built",
  "deliverable_type": "web_application" | "static_site" | "api" | "script" | "library" | "analysis" | "document" | "other",
  "complexity": "low" | "medium" | "high",
  "requirements": ["concrete, checkable requirements"],
  "constraints": ["anything that limits the solution — stack, platform, scale"],
  "stack": ["technologies this should use"]
}

Rules:
- Requirements must be verifiable by a reviewer, not aspirations.
  Good: "dark mode persists across reloads". Bad: "looks modern".
- Infer a sensible stack when none is named, and say so in "stack".
- Judge complexity by the number of interacting parts, not by ambition.
- Do not invent requirements the user neither asked for nor clearly implied.
- The request may be followed by earlier turns of the conversation and by what
  this project already built. Use them to resolve references ("it", "the
  site", "add dark mode") and to stay consistent with what exists — but specify
  what THIS request asks for, not a rebuild of everything before it."""

PLANNER_PROMPT = """You are the Planner of a multi-model engineering room.

You turn a brief into a DAG of subtasks. Different specialist models execute
different subtasks IN PARALLEL, so your decomposition decides whether the result
is coherent or a pile of mismatched fragments.

Return a single JSON object:
{
  "tasks": [
    {
      "key": "t1",
      "title": "short imperative title",
      "description": "exactly what this task must produce, and any contract it must honour (exact names, signatures, ids, data shapes) so parallel tasks fit together",
      "task_type": one of TASK_TYPES,
      "agent": the specialist who does it — one name from AGENTS below,
      "priority": 1-10 (1 is most important),
      "depends_on": ["keys of tasks that must finish first"],
      "owns": ["exact file paths this task alone may write"]
    }
  ]
}

AGENTS — assign each task to the one whose speciality fits it best:
AGENT_LIST

Hard rules:
- FILE OWNERSHIP IS EXCLUSIVE. Two tasks must never list the same path in
  "owns". If two pieces of work touch one file, merge them or split the file.
- Every path a task must produce has to appear in its own "owns".
- "depends_on" must reference keys defined in this plan, and the graph must be
  acyclic. Depend on a task only when you genuinely need its output.
- Maximise parallelism: independent files should be independent tasks.
- Because tasks run concurrently and cannot see each other's work, the
  description must state the shared contract explicitly. If t2 writes CSS for
  markup produced by t1, t1's description must fix the class names and t2 must
  depend on t1.
- Between 3 and MAX_TASKS tasks. Prefer fewer, meatier tasks over many trivial ones.
- Do not create tasks for running commands, installing packages or deploying."""


TEAM_RULES = """

These are the ONLY engineers on this job: the user chose them. Every task must
name one of them. Plan only work they can do. If the brief implies work for a
speciality nobody here has — tests with no tester, a security review with no
security engineer — leave it out rather than giving it to someone else."""


@dataclass(slots=True)
class PlannedTask:
    key: str
    title: str
    description: str
    task_type: TaskType
    # Who does it. None means the planner did not say, and the task falls to
    # whichever agent handles its type.
    agent: str | None = None
    priority: int = 5
    depends_on: list[str] = field(default_factory=list)
    owns: list[str] = field(default_factory=list)


def _coerce_task_type(value: str) -> TaskType:
    try:
        return TaskType(value)
    except ValueError:
        # An unknown type should not sink a whole plan; general programming is
        # the honest bucket for work nobody classified.
        log.warning("unknown_task_type", value=value, fallback="coding")
        return TaskType.CODING


def parse_plan(payload: Any) -> list[PlannedTask]:
    if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
        raise ValidationError("Planner did not return a 'tasks' array")

    tasks: list[PlannedTask] = []
    for raw in payload["tasks"]:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        title = str(raw.get("title") or "").strip()
        description = str(raw.get("description") or "").strip()
        if not (key and title and description):
            raise ValidationError(f"Task {key or '<missing key>'} is incomplete")

        tasks.append(
            PlannedTask(
                key=key,
                title=title,
                description=description,
                task_type=_coerce_task_type(str(raw.get("task_type") or "coding")),
                agent=str(raw.get("agent") or "").strip() or None,
                priority=int(raw.get("priority") or 5),
                depends_on=[str(d) for d in raw.get("depends_on") or []],
                owns=[str(p) for p in raw.get("owns") or []],
            )
        )

    if not tasks:
        raise ValidationError("Planner returned an empty plan")
    return tasks


def validate_plan(
    tasks: list[PlannedTask],
    agents: set[str] | None = None,
    *,
    require_agent: bool = False,
) -> list[str]:
    """Structural checks a schema cannot express.

    These run before anything is persisted: a plan with a dependency cycle
    would deadlock the engine, and two tasks owning one file would silently
    lose work. When `agents` is given, every assignment must name one of them.
    """
    problems: list[str] = []
    keys = {t.key for t in tasks}

    if len(keys) != len(tasks):
        problems.append("duplicate task keys")

    if agents is not None:
        for task in tasks:
            if task.agent and task.agent not in agents:
                problems.append(
                    f'task "{task.key}" is assigned to unknown agent "{task.agent}"'
                )
            elif require_agent and not task.agent:
                problems.append(f'task "{task.key}" names no agent')

    owners: dict[str, str] = {}
    for task in tasks:
        for dep in task.depends_on:
            if dep == task.key:
                problems.append(f'task "{task.key}" depends on itself')
            elif dep not in keys:
                problems.append(f'task "{task.key}" depends on unknown task "{dep}"')
        for path in task.owns:
            if path in owners:
                problems.append(
                    f'file "{path}" is claimed by both "{owners[path]}" and "{task.key}"'
                )
            else:
                owners[path] = task.key

    # Depth-first cycle detection.
    by_key = {t.key: t for t in tasks}
    state: dict[str, str] = {}

    def walk(key: str, trail: list[str]) -> None:
        current = state.get(key)
        if current == "done":
            return
        if current == "visiting":
            problems.append(f"dependency cycle: {' -> '.join([*trail, key])}")
            return
        state[key] = "visiting"
        for dep in by_key[key].depends_on:
            if dep in by_key:
                walk(dep, [*trail, key])
        state[key] = "done"

    for task in tasks:
        walk(task.key, [])

    return problems


class TaskPlanner:
    def __init__(self, session: AsyncSession, workflow_id: uuid.UUID):
        self.session = session
        self.workflow_id = workflow_id

    async def understand(
        self, goal: str, model: Model, *, context: str | None = None
    ) -> dict[str, Any]:
        """Goal -> structured brief."""
        content = f"## REQUEST\n{goal}" + (f"\n\n{context}" if context else "")
        result = await call_model(
            self.session,
            workflow_id=self.workflow_id,
            model=model,
            role="commander",
            system=COMMANDER_PROMPT,
            messages=[ChatMessage(role="user", content=content)],
            max_tokens=2000,
            json_mode=True,
        )
        brief = extract_json(result.text)
        if not isinstance(brief, dict):
            raise ValidationError("Commander did not return an object")
        return brief

    async def decompose(
        self,
        brief: dict[str, Any],
        model: Model,
        *,
        max_tasks: int,
        roster: Roster | None = None,
    ) -> list[PlannedTask]:
        """Brief -> validated DAG. One retry, with the failures fed back."""
        roster = roster or builtin_roster()
        staff = roster.assignable()
        prompt = (
            PLANNER_PROMPT.replace("TASK_TYPES", " | ".join(t.value for t in TaskType))
            .replace("MAX_TASKS", str(max_tasks))
            .replace(
                "AGENT_LIST",
                "\n".join(f"- {a.name}: {a.description}" for a in staff)
                + (TEAM_RULES if roster.team else ""),
            )
        )
        agent_names = {a.name for a in staff}

        brief_json = "\n".join(f"- {k}: {v}" for k, v in brief.items())
        problems: list[str] = []

        for attempt in range(2):
            content = f"Brief:\n{brief_json}"
            if attempt and problems:
                content += (
                    "\n\nYour previous plan was rejected:\n"
                    + "\n".join(f"- {p}" for p in problems)
                    + "\n\nReturn a corrected plan."
                )

            result = await call_model(
                self.session,
                workflow_id=self.workflow_id,
                model=model,
                role="planner",
                system=prompt,
                messages=[ChatMessage(role="user", content=content)],
                max_tokens=6000,
                json_mode=True,
            )

            tasks = parse_plan(extract_json(result.text))
            if len(tasks) > max_tasks:
                problems = [f"plan has {len(tasks)} tasks, the limit is {max_tasks}"]
                continue

            problems = validate_plan(tasks, agent_names, require_agent=bool(roster.team))
            if not problems:
                return tasks

            log.warning("plan_rejected", attempt=attempt + 1, problems=problems)

        raise ValidationError(
            "Planner could not produce a valid DAG", details={"problems": problems}
        )
