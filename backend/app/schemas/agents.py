"""Agent payloads."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import Capability

# A stable identifier the planner writes into plans, so it is kept plain.
AGENT_NAME_PATTERN = r"^[a-z][a-z0-9-]{1,39}$"


class AgentCreate(BaseModel):
    name: str = Field(pattern=AGENT_NAME_PATTERN, examples=["mobile", "devops"])
    display_name: str = Field(min_length=1, max_length=120)
    # The planner reads this to decide what to hand the agent, so it should
    # say what the agent is FOR, not how good it is.
    description: str = Field(min_length=1, max_length=500)
    # The role only. The output format the engine needs is appended to it
    # automatically, so it never has to be written here.
    instructions: str = Field(min_length=1, max_length=20_000)
    # What the agent needs from a model. The router staffs its desk with the
    # model that scores best on these.
    capabilities: list[Capability] = Field(min_length=1)
    # Tools beyond the file tools every agent has: web_search, fetch_url,
    # run_code, run_sql. Omitted, a new agent gets the two web tools.
    tools: list[str] | None = None
    is_enabled: bool = True


class AgentUpdate(BaseModel):
    """Every field optional: a PATCH changes only what it names."""

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    instructions: str | None = Field(default=None, min_length=1, max_length=20_000)
    capabilities: list[Capability] | None = Field(default=None, min_length=1)
    tools: list[str] | None = None
    is_enabled: bool | None = None


class AgentOut(BaseModel):
    name: str
    display_name: str
    description: str
    instructions: str
    capabilities: list[str]
    allowed_tools: list[str]
    handles: list[str]
    requires_review: bool
    # "builtin", "override" (a built-in re-instructed at runtime) or "custom".
    source: str
    is_enabled: bool
    # Whether the planner can give it work. False for the engine's own roles
    # and for disabled agents.
    assignable: bool
