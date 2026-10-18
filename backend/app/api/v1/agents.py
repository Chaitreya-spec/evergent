"""The agent roster, and the door for changing it.

Built-in agents ship in code. Everything here writes `agents` rows, which the
roster merges over the built-ins at the start of every workflow run:

- POST a new name to add a specialist the planner can assign work to.
- PATCH a built-in to re-instruct it, change what it needs from a model, or
  disable it. That writes an override; the shipped default is untouched.
- DELETE an override to restore the built-in, or a custom agent to remove it.

Reading is open to any user. Writing is admin-only: every user's planner sees
the same roster, so an agent's instructions reach everyone's workflows.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.entities import Agent
from app.schemas.agents import AgentCreate, AgentOut, AgentUpdate
from app.services.agents.base import RESERVED_AGENTS, AgentDefinition
from app.services.agents.registry import AGENTS_BY_NAME
from app.services.agents.roster import load_roster
from app.services.tools.names import ALL_TOOLS, FILE_TOOLS, OPTIONAL_TOOLS, WEB_TOOLS

router = APIRouter(prefix="/agents", tags=["agents"])


def _out(agent: AgentDefinition) -> AgentOut:
    return AgentOut(
        name=agent.name,
        display_name=agent.display_name,
        description=agent.description,
        # The engine's own roles have no separate role prompt; show the whole.
        instructions=agent.role_prompt or agent.system_prompt,
        capabilities=[c.value for c in agent.capabilities],
        allowed_tools=agent.allowed_tools,
        handles=[t.value for t in agent.handles],
        requires_review=agent.requires_review,
        source=agent.source,
        is_enabled=agent.is_enabled,
        assignable=agent.is_enabled and agent.name not in RESERVED_AGENTS,
    )


async def _current(session: SessionDep, name: str) -> AgentOut:
    agent = (await load_roster(session)).find(name)
    if agent is None:
        raise NotFoundError(f"No agent named '{name}'")
    return _out(agent)


def _tool_list(requested: list[str]) -> list[str]:
    """What is stored: the file tools every agent has, plus valid extras."""
    unknown = sorted(set(requested) - set(ALL_TOOLS))
    if unknown:
        raise ValidationError(
            f"Unknown tools: {', '.join(unknown)}. Available: {', '.join(OPTIONAL_TOOLS)}"
        )
    return list(dict.fromkeys([*FILE_TOOLS, *requested]))


def _refuse_reserved(name: str) -> None:
    if name in RESERVED_AGENTS:
        raise ConflictError(
            f"'{name}' is part of the engine, not staff: its output is parsed "
            "against a fixed contract, so it cannot be changed."
        )


@router.get("", response_model=list[AgentOut])
async def list_agents(session: SessionDep, _: CurrentUser) -> list[AgentOut]:
    return [_out(a) for a in await load_roster(session)]


@router.get("/{name}", response_model=AgentOut)
async def get_agent(name: str, session: SessionDep, _: CurrentUser) -> AgentOut:
    return await _current(session, name)


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate, session: SessionDep, _: AdminUser
) -> AgentOut:
    """Add a specialist. The planner can assign it work from the next run."""
    _refuse_reserved(payload.name)
    if payload.name in AGENTS_BY_NAME:
        raise ConflictError(
            f"'{payload.name}' is a built-in agent. PATCH it to change it."
        )
    if await session.scalar(select(Agent).where(Agent.name == payload.name)):
        raise ConflictError(f"An agent named '{payload.name}' already exists")

    session.add(
        Agent(
            name=payload.name,
            display_name=payload.display_name,
            description=payload.description,
            system_prompt=payload.instructions,
            capabilities=[c.value for c in payload.capabilities],
            allowed_tools=_tool_list(
                payload.tools if payload.tools is not None else WEB_TOOLS
            ),
            is_enabled=payload.is_enabled,
        )
    )
    await session.commit()
    return await _current(session, payload.name)


@router.patch("/{name}", response_model=AgentOut)
async def update_agent(
    name: str, payload: AgentUpdate, session: SessionDep, _: AdminUser
) -> AgentOut:
    """Change an agent. For a built-in, this writes an override."""
    _refuse_reserved(name)

    row = await session.scalar(select(Agent).where(Agent.name == name))
    if row is None:
        builtin = AGENTS_BY_NAME.get(name)
        if builtin is None:
            raise NotFoundError(f"No agent named '{name}'")
        # First change to a built-in: start the override from its defaults,
        # so a PATCH naming one field leaves the rest as they were.
        row = Agent(
            name=name,
            display_name=builtin.display_name,
            description=builtin.description,
            system_prompt=builtin.role_prompt,
            capabilities=[c.value for c in builtin.capabilities],
            allowed_tools=list(builtin.allowed_tools),
            is_enabled=True,
        )
        session.add(row)

    if payload.display_name is not None:
        row.display_name = payload.display_name
    if payload.description is not None:
        row.description = payload.description
    if payload.instructions is not None:
        row.system_prompt = payload.instructions
    if payload.capabilities is not None:
        row.capabilities = [c.value for c in payload.capabilities]
    if payload.tools is not None:
        row.allowed_tools = _tool_list(payload.tools)
    if payload.is_enabled is not None:
        row.is_enabled = payload.is_enabled

    await session.commit()
    return await _current(session, name)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(name: str, session: SessionDep, _: AdminUser) -> None:
    """Remove a custom agent, or drop an override to restore the built-in.

    Tasks already assigned to a removed agent are not stranded: they fall to
    whichever agent now handles their type.
    """
    _refuse_reserved(name)

    row = await session.scalar(select(Agent).where(Agent.name == name))
    if row is None:
        if name in AGENTS_BY_NAME:
            raise ConflictError(
                f"'{name}' is a built-in agent and cannot be deleted. "
                "PATCH it with is_enabled=false to stop it receiving work."
            )
        raise NotFoundError(f"No agent named '{name}'")

    await session.delete(row)
    await session.commit()
