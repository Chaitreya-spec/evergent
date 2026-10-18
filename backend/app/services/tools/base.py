"""Tool framework.

A tool is a declared capability with a schema and a permission level. An agent
may only call tools on its own allow-list — that is what stops a code-writing
agent from reaching a database tool just because it exists.

Tools are offered to an agent only when they can actually work right now: web
search needs a search key, code execution needs a container runtime. An agent
is never shown a tool that would only fail.
"""

from __future__ import annotations

import abc
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.entities import Task
    from app.services.orchestrator.workspace import WorkflowWorkspace


class ToolPermission(StrEnum):
    """Ordered by blast radius."""

    READ = "read"  # observe only
    WRITE = "write"  # mutate workspace state
    NETWORK = "network"  # reach outside the system
    EXECUTE = "execute"  # run code


@dataclass(slots=True)
class ToolResult:
    ok: bool
    output: Any = None
    error: str | None = None
    duration_ms: int = 0


@dataclass(slots=True)
class ToolContext:
    """Everything a tool is allowed to know about its caller."""

    workflow_id: uuid.UUID
    task: Task
    agent_name: str
    workspace: WorkflowWorkspace
    # Paths written through write_file during this turn, so the engine knows
    # the task produced them even if its final answer does not repeat them.
    written: set[str] = field(default_factory=set)


_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


class Tool(abc.ABC):
    name: str
    description: str
    permission: ToolPermission = ToolPermission.READ
    input_schema: dict[str, Any] = {}

    async def available(self) -> tuple[bool, str]:
        """Whether this tool can work right now, and if not, why."""
        return True, "ready"

    @abc.abstractmethod
    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        """Execute. Must not raise for ordinary failure — return ok=False."""

    def problems(self, arguments: Any) -> list[str]:
        """What is wrong with these arguments, in words a model can act on."""
        if not isinstance(arguments, dict):
            return ["arguments must be a JSON object"]
        found: list[str] = []
        properties = self.input_schema.get("properties", {})
        for key in self.input_schema.get("required", []):
            if key not in arguments:
                found.append(f"missing required argument '{key}'")
        for key, value in arguments.items():
            expected = properties.get(key, {}).get("type")
            if key not in properties:
                found.append(f"unknown argument '{key}'")
            elif expected and not isinstance(value, _JSON_TYPES[expected]):
                found.append(f"'{key}' must be {expected}")
            elif expected == "integer" and isinstance(value, bool):
                found.append(f"'{key}' must be integer")
        return found

    def signature(self) -> str:
        """One line per argument, for the agent's tool list."""
        properties = self.input_schema.get("properties", {})
        required = set(self.input_schema.get("required", []))
        lines = [
            f'    "{key}": {spec.get("type", "any")}'
            + ("" if key in required else ", optional")
            + (f" — {spec['description']}" if spec.get("description") else "")
            for key, spec in properties.items()
        ]
        return "\n".join(lines) or "    (no arguments)"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    async def invoke(
        self,
        name: str,
        arguments: Any,
        context: ToolContext,
        *,
        allowed: set[str],
    ) -> ToolResult:
        """The only way a tool runs.

        Every refusal comes back as a result rather than an exception: it goes
        to the model as feedback, and a model that called a tool wrongly can
        correct itself on the next round.
        """
        tool = self._tools.get(name)
        if tool is None or name not in allowed:
            offered = ", ".join(sorted(allowed)) or "none"
            return ToolResult(
                ok=False, error=f"'{name}' is not one of your tools. You have: {offered}"
            )
        problems = tool.problems(arguments)
        if problems:
            return ToolResult(ok=False, error="; ".join(problems))

        started = time.perf_counter()
        try:
            result = await tool.run(arguments, context)
        except Exception as exc:
            result = ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result


registry = ToolRegistry()
