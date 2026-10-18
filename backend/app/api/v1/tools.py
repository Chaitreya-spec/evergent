"""What agents can use right now, and why anything is missing."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentUser, SessionDep
from app.services.agents.roster import load_roster
from app.services.tools import builtin as _builtin  # noqa: F401  (registers tools)
from app.services.tools.base import registry

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolOut(BaseModel):
    name: str
    description: str
    permission: str
    # Whether it can work right now — a search key set, Docker running.
    available: bool
    status: str
    input_schema: dict
    # Agents allowed to use it.
    agents: list[str]


@router.get("", response_model=list[ToolOut])
async def list_tools(session: SessionDep, _: CurrentUser) -> list[ToolOut]:
    roster = list(await load_roster(session))
    tools = []
    for tool in registry.all():
        available, status = await tool.available()
        tools.append(
            ToolOut(
                name=tool.name,
                description=tool.description,
                permission=tool.permission.value,
                available=available,
                status=status,
                input_schema=tool.input_schema,
                agents=[a.name for a in roster if tool.name in a.allowed_tools],
            )
        )
    return tools
