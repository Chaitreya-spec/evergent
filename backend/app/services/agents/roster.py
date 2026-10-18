"""The live roster: built-in agents with database rows merged over them.

This is the door for adding agents. A row in the `agents` table either

- shares a name with a built-in specialist and replaces it — new
  instructions, different capabilities, or disabled — or
- has a new name and adds a specialist the planner can assign work to.

Deleting an override restores the built-in, so no edit is irreversible.

The roster is loaded once per workflow run and held for its duration: editing
an agent never changes the instructions of work already in flight.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.entities import Agent
from app.models.enums import Capability, TaskType
from app.services.agents.base import RESERVED_AGENTS, AgentDefinition, make_worker
from app.services.agents.registry import AGENTS_BY_NAME, BUILTIN_AGENTS, FALLBACK_AGENT
from app.services.tools.names import OPTIONAL_TOOLS, WEB_TOOLS

log = get_logger(__name__)


class Roster:
    def __init__(
        self, agents: dict[str, AgentDefinition], team: frozenset[str] | None = None
    ) -> None:
        self._agents = agents
        # The engineers chosen for one workflow; None means everyone.
        self.team = team

    def for_team(self, names: list[str] | None) -> Roster:
        """This roster limited to the engineers chosen for one workflow."""
        return Roster(self._agents, frozenset(names) if names else None)

    def __iter__(self) -> Iterator[AgentDefinition]:
        return iter(self._agents.values())

    def find(self, name: str | None) -> AgentDefinition | None:
        return self._agents.get(name) if name else None

    def get(self, name: str) -> AgentDefinition:
        agent = self.find(name)
        if agent is None:
            raise KeyError(
                f"Unknown agent '{name}'. Known: {', '.join(sorted(self._agents))}"
            )
        return agent

    def assignable(self) -> list[AgentDefinition]:
        """Specialists the planner may give work to."""
        return [
            a
            for a in self
            if a.is_enabled
            and a.name not in RESERVED_AGENTS
            and (self.team is None or a.name in self.team)
        ]

    def specialist_for(self, task_type: TaskType) -> AgentDefinition | None:
        """Who on the roster (or team) does this kind of work, if anyone."""
        assignable = self.assignable()
        for agent in assignable:
            if task_type in agent.handles:
                return agent
        general = next((a for a in assignable if a.name == FALLBACK_AGENT), None)
        return general

    def for_task_type(self, task_type: TaskType) -> AgentDefinition:
        found = self.specialist_for(task_type)
        if found is not None:
            return found
        if self.team:
            # A resumed task whose agent left the team stays with the team.
            team = self.assignable()
            if team:
                return team[0]
        # The general engineer is the last resort, and is returned even when
        # disabled: falling back must always resolve to someone.
        return self.find(FALLBACK_AGENT) or AGENTS_BY_NAME[FALLBACK_AGENT]

    def resolve(self, name: str | None, task_type: TaskType) -> AgentDefinition:
        """The agent for a task that was already assigned.

        A resumed workflow can name an agent deleted since it was planned. That
        must not strand the task, so it falls to whoever now handles its type.
        """
        return self.find(name) or self.for_task_type(task_type)


def builtin_roster() -> Roster:
    return Roster({a.name: a for a in BUILTIN_AGENTS})


def agent_from_row(row: Agent) -> AgentDefinition:
    builtin = AGENTS_BY_NAME.get(row.name)

    capabilities: list[Capability] = []
    for value in row.capabilities or []:
        try:
            capabilities.append(Capability(value))
        except ValueError:
            # The API validates these, so this is a row written some other way.
            # Dropping one capability beats refusing to load the whole roster.
            log.warning("unknown_agent_capability", agent=row.name, value=value)

    # Stored lists always include the file tools, so an empty one means a row
    # written before tools were configurable: it keeps its built-in's grant,
    # and a custom agent gets the web.
    if row.allowed_tools:
        tools = [t for t in row.allowed_tools if t in OPTIONAL_TOOLS]
    elif builtin is not None:
        tools = [t for t in builtin.allowed_tools if t in OPTIONAL_TOOLS]
    else:
        tools = list(WEB_TOOLS)

    return make_worker(
        row.name,
        row.display_name,
        row.description,
        # The column holds only the role; make_worker appends the contract.
        row.system_prompt,
        capabilities or (builtin.capabilities if builtin else []),
        # An override keeps the task types its built-in handled, so work of
        # that type still reaches it without the planner having to name it.
        builtin.handles if builtin else [],
        tools=tools,
        requires_review=builtin.requires_review if builtin else True,
        source="override" if builtin else "custom",
        is_enabled=row.is_enabled,
    )


async def load_roster(session: AsyncSession) -> Roster:
    agents = {a.name: a for a in BUILTIN_AGENTS}
    for row in await session.scalars(select(Agent).order_by(Agent.name)):
        if row.name in RESERVED_AGENTS:
            # The API refuses these. A row that got in some other way is
            # ignored, not allowed to replace a contract the engine parses.
            log.warning("reserved_agent_row_ignored", agent=row.name)
            continue
        agents[row.name] = agent_from_row(row)
    return Roster(agents)
