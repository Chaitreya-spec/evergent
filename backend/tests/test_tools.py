"""Agent tools: the workspace they touch, the web they reach, the code they run."""

from __future__ import annotations

import json
import subprocess
import uuid
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.models.entities import Artifact, Project, Task, User, Workflow
from app.services.orchestrator.workspace import (
    UnsafePath,
    WorkflowWorkspace,
    normalise_path,
)
from app.services.tools import builtin
from app.services.tools.base import (
    Tool,
    ToolContext,
    ToolPermission,
    ToolResult,
    registry,
)
from app.services.tools.web import (
    BlockedURL,
    BraveSearch,
    SafeFetcher,
    TavilySearch,
    check_public,
    html_to_text,
)

ALL = set(registry.names())


def _docker_running() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "version"], capture_output=True, timeout=10
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


needs_docker = pytest.mark.skipif(not _docker_running(), reason="needs a running Docker")


@pytest.fixture
async def workspace_task(session):
    user = User(email="tools@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(owner_id=user.id, name="Tools")
    session.add(project)
    await session.flush()
    workflow = Workflow(project_id=project.id, goal="g")
    session.add(workflow)
    await session.flush()
    task = Task(
        workflow_id=workflow.id, key="t1", title="t", description="d", task_type="coding",
        input_payload={"owns": [".env.example", "src/app.py", "schema.sql"]},
    )  # fmt: skip
    session.add(task)
    await session.commit()
    return WorkflowWorkspace(session, workflow.id), task


def _context(workspace, task) -> ToolContext:
    return ToolContext(
        workflow_id=workspace.workflow_id,
        task=task,
        agent_name="coding",
        workspace=workspace,
    )


class TestPaths:
    @pytest.mark.parametrize(
        ("raw", "clean"),
        [
            (".env.example", ".env.example"),
            (".github/workflows/ci.yml", ".github/workflows/ci.yml"),
            ("./src/app.py", "src/app.py"),
            ("src//app.py", "src/app.py"),
            ("src/./app.py", "src/app.py"),
        ],
    )
    def test_normalise_keeps_dotfiles(self, raw, clean) -> None:
        """Regression: lstrip("./") stripped characters, turning ".env.example"
        into "env.example", so every dotfile write was refused as unowned."""
        assert normalise_path(raw) == clean

    @pytest.mark.parametrize("raw", ["/etc/passwd", "../x", "a/../../x", "", "   "])
    def test_unsafe_paths_are_refused(self, raw) -> None:
        with pytest.raises(UnsafePath):
            normalise_path(raw)

    async def test_a_task_can_write_the_dotfile_it_owns(self, workspace_task) -> None:
        workspace, task = workspace_task
        outcome = await workspace.write(task, ".env.example", "KEY=")
        assert outcome.path == ".env.example" and outcome.error is None

    async def test_writing_someone_elses_file_is_refused_with_the_reason(
        self, workspace_task
    ) -> None:
        workspace, task = workspace_task
        outcome = await workspace.write(task, "README.md", "x")
        assert outcome.path is None
        assert "not owned by t1" in outcome.error and "src/app.py" in outcome.error


class TestFileTools:
    async def test_write_list_read(self, workspace_task) -> None:
        workspace, task = workspace_task
        ctx = _context(workspace, task)

        wrote = await registry.invoke(
            "write_file", {"path": "src/app.py", "content": "a\nb\nc"}, ctx, allowed=ALL
        )
        listed = await registry.invoke("list_files", {}, ctx, allowed=ALL)
        read = await registry.invoke(
            "read_file",
            {"path": "src/app.py", "start_line": 2, "end_line": 3},
            ctx,
            allowed=ALL,
        )

        assert wrote.ok and ctx.written == {"src/app.py"}
        assert listed.output == [{"path": "src/app.py", "chars": 5, "version": 1}]
        assert read.output.endswith("b\nc") and "(lines 2-3 of 3)" in read.output

    async def test_reading_a_missing_file_says_how_to_look(self, workspace_task) -> None:
        workspace, task = workspace_task
        result = await registry.invoke(
            "read_file", {"path": "nope.py"}, _context(workspace, task), allowed=ALL
        )
        assert not result.ok and "list_files" in result.error


class _Explodes(Tool):
    name = "explodes"
    description = "raises"
    permission = ToolPermission.READ
    input_schema = {
        "type": "object",
        "required": ["n"],
        "properties": {"n": {"type": "integer"}},
    }

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        raise ValueError("kaboom")


class TestFramework:
    async def test_a_crashing_tool_becomes_an_error_result(self) -> None:
        registry.register(_Explodes())
        try:
            result = await registry.invoke(
                "explodes", {"n": 1}, None, allowed={"explodes"}
            )  # type: ignore[arg-type]
        finally:
            registry._tools.pop("explodes")
        assert not result.ok and result.error == "ValueError: kaboom"

    @pytest.mark.parametrize(
        ("arguments", "problem"),
        [
            ({}, "missing required argument 'n'"),
            ({"n": "1"}, "'n' must be integer"),
            ({"n": True}, "'n' must be integer"),
            ({"n": 1, "extra": 2}, "unknown argument 'extra'"),
            ("not an object", "arguments must be a JSON object"),
        ],
    )
    def test_argument_problems_are_described(self, arguments, problem) -> None:
        assert problem in _Explodes().problems(arguments)

    async def test_ungranted_tools_are_refused(self) -> None:
        result = await registry.invoke(
            "run_sql", {"sql": "x"}, None, allowed={"read_file"}
        )  # type: ignore[arg-type]
        assert not result.ok and "not one of your tools" in result.error


# --- web ------------------------------------------------------------------------


def _resolver(mapping: dict[str, list[str]]):
    async def resolve(host: str, port: int) -> list[str]:
        return mapping[host]

    return resolve


class TestReachability:
    @pytest.mark.parametrize(
        "address",
        ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
         "100.64.0.1", "0.0.0.0", "::1", "fd00::1", "::ffff:127.0.0.1", "224.0.0.1"],
    )  # fmt: skip
    async def test_internal_addresses_are_refused(self, address) -> None:
        with pytest.raises(BlockedURL):
            await check_public(address, 80, _resolver({}))

    async def test_a_name_that_resolves_inside_is_refused(self) -> None:
        with pytest.raises(BlockedURL, match="non-public"):
            await check_public(
                "intranet.example", 80, _resolver({"intranet.example": ["10.0.0.5"]})
            )

    async def test_one_private_record_among_public_ones_is_refused(self) -> None:
        # How a DNS rebinding attack is staged.
        records = {"mixed.example": ["93.184.215.14", "127.0.0.1"]}
        with pytest.raises(BlockedURL):
            await check_public("mixed.example", 80, _resolver(records))

    async def test_public_addresses_pass(self) -> None:
        await check_public(
            "example.com", 443, _resolver({"example.com": ["93.184.215.14"]})
        )


PUBLIC = _resolver({"docs.example": ["93.184.215.14"], "evil.example": ["93.184.215.15"],
                    "inside.example": ["10.0.0.9"]})  # fmt: skip


def _fetcher(handler, **kwargs) -> SafeFetcher:
    return SafeFetcher(resolver=PUBLIC, transport=httpx.MockTransport(handler), **kwargs)


class TestFetching:
    async def test_html_becomes_readable_text(self) -> None:
        html = (
            "<html><head><title>API Guide</title><style>p{color:red}</style></head>"
            "<body><script>steal()</script><h1>Install</h1><p>Run <b>pip install x</b>.</p></body></html>"
        )
        page = await _fetcher(lambda r: httpx.Response(200, html=html)).fetch(
            "https://docs.example/guide"
        )
        assert page.title == "API Guide"
        assert "Install" in page.text and "pip install x" in page.text
        assert "steal()" not in page.text and "color:red" not in page.text

    async def test_a_redirect_into_the_network_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302, headers={"location": "http://inside.example/admin"}
            )

        with pytest.raises(BlockedURL, match="non-public"):
            await _fetcher(handler).fetch("https://evil.example/")

    async def test_a_redirect_to_a_metadata_ip_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                301, headers={"location": "http://169.254.169.254/latest/"}
            )

        with pytest.raises(BlockedURL):
            await _fetcher(handler).fetch("https://evil.example/")

    async def test_endless_redirects_stop(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://docs.example/again"})

        with pytest.raises(BlockedURL, match="redirects"):
            await _fetcher(handler).fetch("https://docs.example/")

    async def test_safe_redirects_are_followed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(301, headers={"location": "/new"})
            return httpx.Response(
                200, text="moved here", headers={"content-type": "text/plain"}
            )

        page = await _fetcher(handler).fetch("https://docs.example/old")
        assert page.url == "https://docs.example/new" and page.text == "moved here"

    async def test_binary_content_is_not_read(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"\x89PNG", headers={"content-type": "image/png"}
            )

        page = await _fetcher(handler).fetch("https://docs.example/logo.png")
        assert "not text" in page.text

    async def test_large_pages_are_capped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text="x" * 5000, headers={"content-type": "text/plain"}
            )

        page = await _fetcher(handler, max_bytes=1000).fetch("https://docs.example/big")
        assert page.truncated and len(page.text) == 1000

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://docs.example/x",
            "https://user:pw@docs.example/",
            "https:///nohost",
        ],
    )
    async def test_only_plain_web_urls(self, url) -> None:
        with pytest.raises(BlockedURL):
            await _fetcher(lambda r: httpx.Response(200)).fetch(url)

    def test_text_extraction_keeps_structure(self) -> None:
        title, text = html_to_text("<title>T</title><ul><li>one</li><li>two</li></ul>")
        assert title == "T" and text.splitlines() == ["- one", "- two"]


class TestSearch:
    async def test_tavily(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers["authorization"]
            seen["body"] = request.content
            return httpx.Response(200, json={"results": [
                {"title": "pgvector", "url": "https://github.com/pgvector/pgvector", "content": "Open-source vector search"},
            ]})  # fmt: skip

        hits = await TavilySearch("tvly-key", httpx.MockTransport(handler)).search(
            "pgvector", 3
        )
        assert seen["auth"] == "Bearer tvly-key"
        assert json.loads(seen["body"]) == {
            "query": "pgvector",
            "max_results": 3,
            "search_depth": "basic",
        }
        assert hits[0].url == "https://github.com/pgvector/pgvector"

    async def test_brave(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-subscription-token"] == "brave-key"
            assert (
                request.url.params["q"] == "pgvector"
                and request.url.params["count"] == "3"
            )
            return httpx.Response(200, json={"web": {"results": [
                {"title": "<strong>pgvector</strong>", "url": "https://x.example", "description": "Vector <strong>search</strong>"},
            ]}})  # fmt: skip

        hits = await BraveSearch("brave-key", httpx.MockTransport(handler)).search(
            "pgvector", 3
        )
        assert hits[0].title == "pgvector" and hits[0].snippet == "Vector search"

    async def test_search_is_offered_only_with_a_key(self, monkeypatch) -> None:
        tool = builtin.WebSearchTool()
        monkeypatch.setattr(settings, "tavily_api_key", None)
        monkeypatch.setattr(settings, "brave_search_api_key", None)
        ok, reason = await tool.available()
        assert not ok and "TAVILY_API_KEY" in reason

        monkeypatch.setattr(settings, "brave_search_api_key", "k")
        assert await tool.available() == (True, "via brave")


# --- execution ---------------------------------------------------------------------


class TestSqlTool:
    def test_files_load_first_and_the_query_is_never_in_the_shell(self) -> None:
        script = builtin.sql_script(["db/schema.sql", "seed data.sql"])
        assert script.index("/workspace/db/schema.sql") < script.index(
            "'/workspace/seed data.sql'"
        )
        assert script.endswith(f"-f /workspace/{builtin.QUERY_FILE}")
        assert "ON_ERROR_STOP=1" in script

    async def test_unknown_files_are_named(self, workspace_task) -> None:
        workspace, task = workspace_task
        result = await registry.invoke(
            "run_sql",
            {"sql": "select 1", "files": ["missing.sql"]},
            _context(workspace, task),
            allowed=ALL,
        )
        assert not result.ok and "missing.sql" in result.error


@needs_docker
class TestRealContainers:
    """Against the real Docker runtime. Skipped where none is running."""

    async def test_sql_runs_against_a_real_postgres(self, workspace_task) -> None:
        workspace, task = workspace_task
        ctx = _context(workspace, task)
        await workspace.write(
            task,
            "schema.sql",
            "create table t (id int primary key, email text check (email like '%@%'));",
        )

        ok = await registry.invoke(
            "run_sql",
            {
                "files": ["schema.sql"],
                "sql": "insert into t values (1, 'a@b.c'); select count(*) from t;",
            },
            ctx,
            allowed=ALL,
        )
        bad = await registry.invoke(
            "run_sql",
            {"files": ["schema.sql"], "sql": "insert into t values (2, 'nope');"},
            ctx,
            allowed=ALL,
        )

        assert ok.ok, ok.output
        assert "(1 row)" in ok.output["output"]
        assert not bad.ok and "check constraint" in bad.output["output"]

    async def test_code_runs_with_the_workspace_and_without_network(
        self, workspace_task
    ) -> None:
        workspace, task = workspace_task
        ctx = _context(workspace, task)
        await workspace.write(task, "src/app.py", "print(6 * 7)")

        ran = await registry.invoke(
            "run_code",
            {"runtime": "python", "command": "python src/app.py"},
            ctx,
            allowed=ALL,
        )
        offline = await registry.invoke(
            "run_code",
            {"runtime": "python", "command": "python -c \"import socket; socket.create_connection(('1.1.1.1', 80), 3)\""},
            ctx, allowed=ALL,
        )  # fmt: skip

        assert ran.ok and ran.output["output"].strip().endswith("42")
        assert not offline.ok


# keep a reference so linters see the fixture-only import used
_ = (uuid, Artifact)
