"""Built-in tools.

Adding a tool means writing a class and registering it here. Each agent's
allow-list then decides who may use it; no other code changes.
"""

from __future__ import annotations

import shlex
from typing import Any

from app.core.config import settings
from app.services.orchestrator.workspace import UnsafePath, normalise_path
from app.services.sandbox.docker import RunSpec, get_sandbox, tail
from app.services.tools.base import (
    Tool,
    ToolContext,
    ToolPermission,
    ToolResult,
    registry,
)
from app.services.tools.web import BlockedURL, SafeFetcher, get_search_backend

MAX_READ_CHARS = 20_000


# --- files ------------------------------------------------------------------


class ListFilesTool(Tool):
    name = "list_files"
    description = "List every file in the workspace so far, with sizes."
    permission = ToolPermission.READ
    input_schema = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(ok=True, output=await context.workspace.listing())


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a workspace file — another agent's actual output, not a summary of it."
    )
    permission = ToolPermission.READ
    input_schema = {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "description": "1-based, inclusive"},
            "end_line": {"type": "integer", "description": "inclusive"},
        },
    }

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            content = await context.workspace.read(arguments["path"])
        except UnsafePath as exc:
            return ToolResult(ok=False, error=str(exc))
        if content is None:
            return ToolResult(
                ok=False,
                error=f"No file {arguments['path']!r}. Use list_files to see what exists.",
            )
        lines = content.splitlines()
        start = max(1, int(arguments.get("start_line") or 1))
        end = min(len(lines), int(arguments.get("end_line") or len(lines)))
        selected = "\n".join(lines[start - 1 : end])
        note = ""
        if len(selected) > MAX_READ_CHARS:
            selected = selected[:MAX_READ_CHARS]
            note = f"\n[cut at {MAX_READ_CHARS} characters — read a line range for more]"
        return ToolResult(
            ok=True,
            output=f"{arguments['path']} (lines {start}-{end} of {len(lines)})\n{selected}{note}",
        )


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Write a whole file you own. It is saved immediately, so run_code and "
        "run_sql see it. You need not repeat it in your final answer."
    )
    permission = ToolPermission.WRITE
    input_schema = {
        "type": "object",
        "required": ["path", "content"],
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
    }

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        outcome = await context.workspace.write(
            context.task, arguments["path"], arguments["content"]
        )
        if outcome.path is None:
            return ToolResult(ok=False, error=outcome.error)
        context.written.add(outcome.path)
        return ToolResult(
            ok=True, output={"written": outcome.path, "chars": len(arguments["content"])}
        )


# --- web ----------------------------------------------------------------------


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web. Returns titles, URLs and snippets; fetch_url reads a result."
    )
    permission = ToolPermission.NETWORK
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "description": "1-8, default 5"},
        },
    }

    async def available(self) -> tuple[bool, str]:
        backend = get_search_backend()
        if backend is None:
            return False, "no search key: set TAVILY_API_KEY or BRAVE_SEARCH_API_KEY"
        return True, f"via {backend.name}"

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        backend = get_search_backend()
        if backend is None:
            return ToolResult(ok=False, error="web search is not configured")
        count = min(8, max(1, int(arguments.get("max_results") or 5)))
        hits = await backend.search(arguments["query"], count)
        return ToolResult(
            ok=True,
            output=[
                {"title": h.title, "url": h.url, "snippet": h.snippet[:500]} for h in hits
            ],
        )


class FetchUrlTool(Tool):
    name = "fetch_url"
    description = (
        "Read a public web page or API response as text — documentation, a "
        "changelog, a spec. Internal and private addresses are refused."
    )
    permission = ToolPermission.NETWORK
    input_schema = {
        "type": "object",
        "required": ["url"],
        "properties": {"url": {"type": "string"}},
    }

    def __init__(self, fetcher: SafeFetcher | None = None) -> None:
        self.fetcher = fetcher

    async def available(self) -> tuple[bool, str]:
        if not settings.web_fetch_enabled:
            return False, "disabled (WEB_FETCH_ENABLED=false)"
        return True, "ready"

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            page = await (self.fetcher or SafeFetcher()).fetch(arguments["url"])
        except BlockedURL as exc:
            return ToolResult(ok=False, error=f"refused: {exc}")
        return ToolResult(
            ok=200 <= page.status < 400,
            output={
                "url": page.url,
                "status": page.status,
                "title": page.title,
                "text": page.text[: settings.tool_result_chars],
                "truncated": page.truncated
                or len(page.text) > settings.tool_result_chars,
            },
            error=None if page.status < 400 else f"HTTP {page.status}",
        )


# --- execution ------------------------------------------------------------------


class _SandboxTool(Tool):
    permission = ToolPermission.EXECUTE

    async def available(self) -> tuple[bool, str]:
        return await get_sandbox().available()


class RunCodeTool(_SandboxTool):
    name = "run_code"
    description = (
        "Run a shell command against the workspace in a throwaway container: "
        "try code, run tests, check output. No network while the command runs."
    )
    input_schema = {
        "type": "object",
        "required": ["runtime", "command"],
        "properties": {
            "runtime": {"type": "string", "description": '"python" or "node"'},
            "command": {"type": "string", "description": "e.g. python app.py"},
            "install": {
                "type": "string",
                "description": "e.g. pip install requests (may use the network)",
            },
        },
    }

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        runtime = str(arguments["runtime"]).strip().lower()
        if runtime not in ("python", "node"):
            return ToolResult(ok=False, error='runtime must be "python" or "node"')
        result = await get_sandbox().run(
            await context.workspace.files(),
            RunSpec(
                runtime=runtime,
                command=arguments["command"],
                install=arguments.get("install"),
            ),
            timeout=settings.tool_exec_timeout_seconds,
        )
        if result.infrastructure_failed:
            return ToolResult(ok=False, error=f"could not run: {result.output}")
        return ToolResult(
            ok=result.passed,
            output={
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "stage": result.stage,
                "output": tail(result.output, settings.tool_result_chars),
            },
            error=None if result.passed else "command failed",
        )


# The query is written into the workspace copy, never interpolated into the
# shell, so SQL text cannot break out into commands.
QUERY_FILE = ".evergent/query.sql"
PSQL = "psql -h /tmp -U postgres -X -v ON_ERROR_STOP=1"


def sql_script(files: list[str]) -> str:
    setup = (
        "set -e; "
        "initdb -D /tmp/pg -U postgres --auth=trust >/dev/null; "
        "pg_ctl -D /tmp/pg -w -l /tmp/pg.log "
        "-o \"-c listen_addresses='' -c unix_socket_directories=/tmp -c fsync=off\" "
        "start >/dev/null"
    )
    loads = "".join(
        f"; {PSQL} -q -f {shlex.quote(f'/workspace/{path}')}" for path in files
    )
    return f"{setup}{loads}; {PSQL} -f /workspace/{QUERY_FILE}"


class RunSqlTool(_SandboxTool):
    name = "run_sql"
    description = (
        "Run SQL against a fresh, empty PostgreSQL 17 (with pgvector) that is "
        'destroyed afterwards. Load your schema files first via "files", then '
        "your SQL runs. Verifies migrations and queries for real."
    )
    input_schema = {
        "type": "object",
        "required": ["sql"],
        "properties": {
            "sql": {"type": "string"},
            "files": {
                "type": "array",
                "description": "workspace .sql files to run first, in order",
            },
        },
    }

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            files = [normalise_path(str(p)) for p in arguments.get("files") or []]
        except UnsafePath as exc:
            return ToolResult(ok=False, error=str(exc))
        workspace = await context.workspace.files()
        missing = sorted(set(files) - {path for path, _ in workspace})
        if missing:
            return ToolResult(
                ok=False, error=f"not in the workspace: {', '.join(missing)}"
            )

        result = await get_sandbox().run(
            [*workspace, (QUERY_FILE, arguments["sql"])],
            RunSpec(runtime="postgres", command=sql_script(files), label="run_sql"),
            timeout=settings.tool_exec_timeout_seconds,
        )
        if result.infrastructure_failed:
            return ToolResult(ok=False, error=f"could not run: {result.output}")
        return ToolResult(
            ok=result.passed,
            output={
                "exit_code": result.exit_code,
                "output": tail(result.output, settings.tool_result_chars),
            },
            error=None if result.passed else "SQL failed",
        )


for _tool in (
    ListFilesTool(),
    ReadFileTool(),
    WriteFileTool(),
    WebSearchTool(),
    FetchUrlTool(),
    RunCodeTool(),
    RunSqlTool(),
):
    registry.register(_tool)
