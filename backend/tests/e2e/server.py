"""The real backend with scripted model replies — for browser end-to-end tests only.

Everything is the production code path (API, auth, engine, router, events,
storage) except the model provider, which answers from a script so the tests
need no API key and are deterministic. It uses a throwaway SQLite database
and never touches a real one.

    .venv/bin/python -m tests.e2e.server            # listens on 127.0.0.1:8100

Never deploy this: it exists so a browser can watch a full run end to end.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix="evergent-e2e-"))
os.environ.update(
    {
        "ENVIRONMENT": "test",
        # Never read the developer's own .env.
        "EVERGENT_ENV_FILE": "",
        "DATABASE_URL": f"sqlite+aiosqlite:///{_root}/e2e.db",
        "SECRET_KEY": "e2e-secret-key-not-used-anywhere-real-000000",
        # Nothing listens here: the event bus uses its in-process fallback.
        "REDIS_URL": os.environ.get("E2E_REDIS_URL", "redis://127.0.0.1:6390/0"),
        "RATE_LIMIT_PER_MINUTE": "100000",
        "AUTH_RATE_LIMIT_PER_MINUTE": "100000",
        "SANDBOX_ENABLED": "false",
        "STORAGE_LOCAL_ROOT": str(_root / "storage"),
        "CORS_ORIGINS": '["http://127.0.0.1:3100"]',
        # Not a credential: it only makes one provider count as configured,
        # so the UI offers to run. Every call is answered by the script below.
        "ANTHROPIC_API_KEY": "e2e-placeholder-not-a-real-key",
    }
)

import uvicorn  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.seed import seed_catalog  # noqa: E402
from app.db.session import SessionFactory, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import entities  # noqa: E402,F401
from app.services.orchestrator import llm  # noqa: E402
from app.services.providers import registry  # noqa: E402
from app.services.providers.base import (  # noqa: E402
    AIProvider,
    CompletionRequest,
    CompletionResult,
)
from app.services.router import model_router  # noqa: E402

# Seconds each scripted call takes, so a browser can see work in progress.
PACE = float(os.environ.get("E2E_PACE", "0.6"))

PLAN = [
    {"key": "t1", "title": "Write the specification", "description": "Write SPEC.md for a tiny notes API.",
     "task_type": "requirements", "agent": "requirements", "priority": 1, "depends_on": [], "owns": ["SPEC.md"]},
    {"key": "t2", "title": "Build the notes API", "description": "Write app.py with GET and POST /notes.",
     "task_type": "backend", "agent": "backend", "priority": 2, "depends_on": ["t1"], "owns": ["app.py"]},
    {"key": "t3", "title": "Design the notes table", "description": "Write schema.sql.",
     "task_type": "database", "agent": "database", "priority": 2, "depends_on": ["t1"], "owns": ["schema.sql"]},
    {"key": "t4", "title": "Document it", "description": "Write README.md.",
     "task_type": "documentation", "agent": "documentation", "priority": 3, "depends_on": ["t2", "t3"], "owns": ["README.md"]},
]

CONTENT = {
    "SPEC.md": "# Notes API\n\n- GET /notes lists notes\n- POST /notes adds one\n",
    "app.py": "NOTES: list[str] = []\n\n\ndef list_notes() -> list[str]:\n    return NOTES\n",
    "schema.sql": "create table notes (id serial primary key, body text not null);\n",
    "README.md": "# Notes\n\nRun `python app.py`.\n",
}


class ScriptedProvider(AIProvider):
    name = "scripted"

    def __init__(self) -> None:
        super().__init__(api_key="scripted")

    def is_configured(self) -> bool:
        return True

    async def verify(self) -> list[str]:
        return ["scripted"]

    async def generate(self, request: CompletionRequest) -> CompletionResult:
        await asyncio.sleep(PACE)
        system = request.system or ""
        first = request.messages[0].content
        if "You are the Commander" in system:
            payload = {
                "objective": "A tiny notes API with a schema and docs",
                "deliverable_type": "web_application",
                "complexity": "low",
                "requirements": ["list notes", "add a note"],
                "constraints": ["no external services"],
                "stack": ["Python", "SQL"],
            }
        elif "You are the Planner" in system:
            payload = {"tasks": PLAN}
        elif "You review work produced by other models" in system:
            payload = {"status": "valid", "score": 8.5, "feedback": [], "summary": "Meets the brief."}
        elif "You produce the final answer" in system:
            return CompletionResult(
                text="A tiny notes API: a spec, the API, its table and a README.",
                input_tokens=1200,
                output_tokens=200,
            )
        else:
            section = first.split("## FILES YOU OWN", 1)[-1].split("\n\n", 1)[0]
            owned = re.findall(r"^- (\S+)$", section, flags=re.MULTILINE)
            payload = {
                "artifacts": [{"path": p, "content": CONTENT.get(p, f"# {p}\n")} for p in owned],
                "summary": f"Wrote {', '.join(owned)}",
            }
        return CompletionResult(text=json.dumps(payload), input_tokens=800, output_tokens=300)

    async def stream(self, request: CompletionRequest):
        yield (await self.generate(request)).text


scripted = ScriptedProvider()
model_router.get_provider = lambda _name: scripted
llm.get_provider = lambda _name: scripted
registry.get_provider = lambda _name: scripted


async def prepare() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionFactory() as session:
        await seed_catalog(session)


if __name__ == "__main__":
    asyncio.run(prepare())
    port = int(os.environ.get("E2E_PORT", "8100"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
