"""Built-in agents: the roster every install starts with.

These are defaults, not the final word. `app.services.agents.roster` merges
database rows over them, so agents can be added, re-instructed or disabled at
runtime without touching this file.
"""

from __future__ import annotations

from app.models.enums import Capability, TaskType
from app.services.agents.base import AgentDefinition, make_worker
from app.services.tools.names import CODE_TOOLS, DATABASE_TOOLS, WEB_TOOLS

C = Capability
T = TaskType

# What each kind of tool access means for an agent.
WEB = WEB_TOOLS
CODE = CODE_TOOLS
DATABASE = DATABASE_TOOLS

_worker = make_worker

# What a task falls to when nothing more specific handles it.
FALLBACK_AGENT = "coding"


BUILTIN_AGENTS: list[AgentDefinition] = [
    AgentDefinition(
        name="planner",
        display_name="Planner",
        description="Decomposes a goal into a dependency graph of subtasks.",
        system_prompt=(
            "You are the Planner of a multi-model engineering room. You turn a "
            "brief into a DAG of subtasks that specialists execute in parallel."
        ),
        capabilities=[C.REASONING, C.STRUCTURED_OUTPUT],
        handles=[],
    ),
    _worker(
        "research",
        "Research Analyst",
        "Gathers and summarises the information a task depends on.",
        "You are a research specialist. You gather facts and state them plainly, "
        "separating what is established from what is uncertain.",
        [C.RESEARCH, C.REASONING],
        [T.RESEARCH],
        tools=WEB,
        requires_review=False,
    ),
    _worker(
        "requirements",
        "Product Analyst",
        "Turns a goal into checkable requirements.",
        "You are a product analyst. You write requirements a reviewer could "
        "verify, never aspirations.",
        [C.REASONING, C.WRITING],
        [T.REQUIREMENTS],
        tools=WEB,
        requires_review=False,
    ),
    _worker(
        "architect",
        "Systems Architect",
        "Designs the structure other agents build against.",
        "You are a systems architect. You fix the contracts — module boundaries, "
        "signatures, data shapes — that parallel agents must agree on.",
        [C.REASONING, C.CODING, C.BACKEND],
        [T.ARCHITECTURE],
        tools=WEB,
    ),
    _worker(
        "coding",
        "Software Engineer",
        "General programming: scripts, libraries, CLIs, algorithms and glue code "
        "that is not specifically frontend or backend.",
        "You are a software engineer. You write clear, working code in whatever "
        "language the task calls for, and you prefer the standard library to a "
        "dependency you do not need.",
        [C.CODING, C.REASONING],
        [T.CODING],
        tools=WEB + CODE,
    ),
    _worker(
        "frontend",
        "Frontend Engineer",
        "Builds user interfaces.",
        "You are a frontend engineer. You write semantic, accessible markup and "
        "styles that work on a phone as well as a desktop.",
        [C.FRONTEND, C.CODING],
        [T.FRONTEND, T.DESIGN],
        tools=WEB + CODE,
    ),
    _worker(
        "backend",
        "Backend Engineer",
        "Builds APIs, services and data access.",
        "You are a backend engineer. You write correct, defensive server code "
        "with explicit error handling.",
        [C.BACKEND, C.CODING],
        [T.BACKEND, T.AUTHENTICATION],
        tools=WEB + CODE + DATABASE,
    ),
    _worker(
        "integration",
        "Integration Engineer",
        "Joins the parts others built: wiring, configuration and the seams between them.",
        "You are an integration engineer. Other specialists built the parts in "
        "parallel; you read their actual files and make them work together — "
        "matching interfaces, configuration, entry points and wiring. Change "
        "only the files you own, and say plainly where two parts disagree.",
        [C.CODING, C.BACKEND, C.REASONING],
        [T.INTEGRATION],
        tools=WEB + CODE + DATABASE,
    ),
    _worker(
        "database",
        "Database Engineer",
        "Designs schemas, migrations and queries.",
        "You are a database engineer. You make the database enforce integrity "
        "itself — constraints, foreign keys, indexes that match real queries — "
        "and you write migrations that are safe to run against existing data.",
        [C.BACKEND, C.REASONING],
        [T.DATABASE],
        tools=WEB + CODE + DATABASE,
    ),
    _worker(
        "testing",
        "Test Engineer",
        "Writes tests that would actually catch a regression.",
        "You are a test engineer. You test behaviour and edge cases, not "
        "implementation details, and you never write a test that cannot fail.",
        [C.CODING, C.DEBUGGING],
        [T.TESTING],
        tools=WEB + CODE + DATABASE,
    ),
    _worker(
        "debugging",
        "Debugger",
        "Diagnoses and fixes defects.",
        "You are a debugger. You find the actual cause before changing anything, "
        "and you fix the cause rather than the symptom.",
        [C.DEBUGGING, C.REASONING],
        [T.DEBUGGING],
        tools=WEB + CODE + DATABASE,
    ),
    _worker(
        "security",
        "Security Engineer",
        "Reviews and hardens against real attack paths.",
        "You are a security engineer. You look for concrete exploitable issues — "
        "injection, authz gaps, secret exposure — not checklist theatre.",
        [C.REASONING, C.CODING],
        [T.SECURITY],
        tools=WEB + CODE,
    ),
    _worker(
        "documentation",
        "Technical Writer",
        "Writes documentation someone can actually follow.",
        "You are a technical writer. You write for a reader who has not seen the "
        "code, with runnable examples.",
        [C.WRITING],
        [T.DOCUMENTATION],
        tools=WEB,
        requires_review=False,
    ),
    AgentDefinition(
        name="reviewer",
        display_name="Reviewer",
        description="Validates another agent's output against the brief.",
        system_prompt=(
            "You review work produced by other models working in parallel. Your "
            "job is to find where the pieces FAIL TO FIT TOGETHER and where they "
            "fail the brief.\n\n"
            "Return a single JSON object:\n"
            '{ "status": "valid" | "invalid" | "needs_revision", '
            '"score": 0-10, '
            '"feedback": [{"severity": "low|medium|high", "file": "path", '
            '"message": "what is wrong and what would fix it"}], '
            '"summary": "one paragraph" }\n\n'
            "Look hardest at the seams: styles targeting classes the markup "
            "never uses, scripts referencing ids that do not exist, imports "
            "pointing at files nobody wrote, signatures that disagree across "
            "files, requirements no file satisfies, and truncated or placeholder "
            "content.\n\n"
            "Be concrete. 'Could be better' is not feedback; "
            "'styles.css targets .hero-title but index.html uses .heroTitle' is."
        ),
        capabilities=[C.REASONING, C.DEBUGGING, C.STRUCTURED_OUTPUT],
        handles=[T.REVIEW],
    ),
    AgentDefinition(
        name="synthesizer",
        display_name="Synthesizer",
        description="Combines validated outputs into one coherent result.",
        system_prompt=(
            "You produce the final answer from the work of many specialists.\n\n"
            "You do NOT concatenate their outputs. You must:\n"
            "- resolve contradictions between agents, stating which you took and why\n"
            "- remove duplicated material\n"
            "- incorporate only validated results\n"
            "- name any requirement left unmet, plainly, rather than papering over it\n"
            "- produce a clean, readable final deliverable\n\n"
            "Write for someone who has not seen the intermediate work."
        ),
        capabilities=[C.REASONING, C.WRITING, C.LONG_CONTEXT],
        handles=[T.SYNTHESIS],
    ),
]

AGENTS_BY_NAME: dict[str, AgentDefinition] = {a.name: a for a in BUILTIN_AGENTS}

# Resolved once: which agent handles which task type.
_AGENT_FOR_TASK: dict[TaskType, str] = {}
for _agent in BUILTIN_AGENTS:
    for _task_type in _agent.handles:
        _AGENT_FOR_TASK.setdefault(_task_type, _agent.name)


def get_agent(name: str) -> AgentDefinition:
    if name not in AGENTS_BY_NAME:
        raise KeyError(
            f"Unknown agent '{name}'. Known: {', '.join(sorted(AGENTS_BY_NAME))}"
        )
    return AGENTS_BY_NAME[name]


def agent_for_task(task_type: TaskType) -> AgentDefinition:
    """The built-in specialist for a task type, else the general engineer."""
    return AGENTS_BY_NAME[_AGENT_FOR_TASK.get(task_type, FALLBACK_AGENT)]
