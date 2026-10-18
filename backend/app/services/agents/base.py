"""Agent definitions.

An agent is configuration, not code: a prompt, a set of capabilities, the
tools it may touch and the models it prefers. Adding one means adding a
definition — the execution engine needs no change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.enums import Capability, TaskType
from app.services.tools.names import FILE_TOOLS

# The engine's own machinery rather than staff: the engine calls these by name
# and parses their output against fixed contracts, so they can be neither
# replaced nor assigned work by the planner.
RESERVED_AGENTS = frozenset({"planner", "reviewer", "synthesizer"})


@dataclass(slots=True)
class AgentDefinition:
    name: str
    display_name: str
    description: str
    system_prompt: str
    capabilities: list[Capability] = field(default_factory=list)
    # Tool allow-list. An agent may never call a tool outside this set, which
    # is what keeps a code-writing agent away from, say, database deletion.
    allowed_tools: list[str] = field(default_factory=list)
    preferred_models: list[str] = field(default_factory=list)
    handles: list[TaskType] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    # Whether a ReviewerAgent should check this agent's output.
    requires_review: bool = False
    # The part a person writes. For workers, system_prompt is this plus the
    # output contract, which is appended rather than trusted to the author.
    role_prompt: str = ""
    # "builtin", "override" (a database row replacing a built-in) or "custom".
    source: str = "builtin"
    is_enabled: bool = True


WORKER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["artifacts", "summary"],
    "properties": {
        "artifacts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
        "summary": {"type": "string"},
    },
}

_WORKER_RULES = """
Return a single JSON object:
{
  "artifacts": [{ "path": "exact path you were assigned", "content": "the complete file" }],
  "summary": "what you produced and anything the reviewer must know"
}

Hard rules:
- Write ONLY the files assigned to you. Anything else is discarded.
- Write each file COMPLETE, first line to last. Never abbreviate with "..." or
  "rest unchanged" — nothing downstream can fill a gap.
- Honour the contract in your task description exactly. Names, signatures and
  data shapes stated there are what other agents are building against.
- Other agents work in parallel and cannot see your work in progress. Match the
  dependency outputs you are given rather than assuming what they contain.
- No placeholders, no TODOs, no lorem ipsum.
""".strip()


# Appended for any agent that handles testing, including an override of the
# built-in Tester, so a re-instructed Tester still gets its tests executed.
_TEST_RUN_RULES = """
Your tests are executed, so also include "run" in the JSON object:
"run": {
  "runtime": "python" | "node",
  "install": "shell command that installs dependencies, or null",
  "command": "shell command that runs the tests and exits non-zero on failure"
}

- Everything runs in /workspace, which contains every file produced so far.
- "install" may use the network; "command" runs offline.
- Python: dependencies install into the workspace automatically, so
  "pip install -r requirements.txt" or "pip install pytest" just works. Run
  tests with "python -m pytest -q".
- Node: use "npm install" to install, and "node --test" or "npx jest" to run.
- If you are told your tests failed: fix a test that is wrong. Never weaken a
  correct test to make it pass — if the code under test is wrong, keep the
  test and name the faulty file and the bug in your summary.
""".strip()


def make_worker(
    name: str,
    display_name: str,
    description: str,
    role_prompt: str,
    capabilities: list[Capability],
    handles: list[TaskType] | None = None,
    *,
    tools: list[str] | None = None,
    requires_review: bool = True,
    source: str = "builtin",
    is_enabled: bool = True,
) -> AgentDefinition:
    """A specialist that produces files.

    The output contract is appended here, never left to whoever wrote the role
    prompt: the engine parses worker output as JSON artifacts, so an agent
    whose prompt forgot to ask for that format would silently produce nothing.
    """
    return AgentDefinition(
        name=name,
        display_name=display_name,
        description=description,
        system_prompt="\n\n".join(
            [role_prompt.strip(), _WORKER_RULES]
            + ([_TEST_RUN_RULES] if TaskType.TESTING in (handles or []) else [])
        ),
        capabilities=capabilities,
        # File tools always; anything that reaches the web or runs code is
        # granted per agent.
        allowed_tools=list(dict.fromkeys([*FILE_TOOLS, *(tools or [])])),
        handles=list(handles or []),
        output_schema=WORKER_OUTPUT_SCHEMA,
        requires_review=requires_review,
        role_prompt=role_prompt.strip(),
        source=source,
        is_enabled=is_enabled,
    )
