# Evergent — AI Engineering Room (backend)

One goal in. The system understands it, decomposes it into a dependency graph,
routes each subtask to the model best suited to it, runs the independent ones
in parallel, validates the results, repairs what fails review, and synthesises
one coherent answer.

It behaves like an engineering manager, not a chatbot.

```
goal
 │
 ▼  COMMANDER      understands; emits a checkable brief
 ▼  PLANNER        decomposes into a DAG; assigns exclusive file ownership
 ▼  MODEL ROUTER   scores capability against cost, latency and reliability
 │
 ├──────┬──────┐   independent tasks run concurrently
 ▼      ▼      ▼
AGENT  AGENT  AGENT    each writes only the files it owns
 │      │      │
 └──────┴──────┘
 ▼  REVIEWER       validates against the brief; failures go back for repair
 ▼  SYNTHESIZER    resolves contradictions into one deliverable
```

## Running locally

Needs Python 3.13, PostgreSQL and Redis. With Docker, skip to the next section.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # runtime + test/lint tools

cp .env.example .env          # then set SECRET_KEY and one provider key
createdb evergent

.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed
.venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000/docs for interactive API documentation.

### Reaching models through OmniRoute

Instead of one key per provider, the backend can use a self-hosted
OpenAI-compatible gateway. With [OmniRoute](https://github.com/diegosouzapw/OmniRoute):

```bash
# Localhost only — by default it listens on every interface.
cd ~/.omniroute && OMNIROUTE_SERVER_HOST=127.0.0.1 omniroute serve --no-open
```

and in `.env`:

```bash
COMPATIBLE_BASE_URL=http://127.0.0.1:20128/v1
COMPATIBLE_API_KEY=<a key from OmniRoute's dashboard, API Keys page>
```

The catalog routes three models through it: `gemini/gemini-pro-latest`,
`gemini/gemini-flash-latest` and `gemini/gemini-flash-lite-latest` — so
OmniRoute needs a working Gemini connection. Add one in its dashboard
(http://localhost:20128) with a key from aistudio.google.com.

Every model call is capped by `MODEL_TIMEOUT_SECONDS` (300) and
`MODEL_MAX_RETRIES` (1). A gateway that holds a request open fails the call
instead of stalling the run.

Admin routes — adding providers and agents, refreshing routing stats — need an
admin account, and there is deliberately no API that grants one. Register, then:

```bash
.venv/bin/python -m scripts.promote you@example.com
```

### With Docker

The full stack (frontend, API, worker, Postgres, Redis) is defined at the
repository root:

```bash
cd .. && cp .env.example .env     # fill in the REQUIRED values
docker compose up -d --build      # http://localhost:3000
```

For local development, `docker compose -f ../docker-compose.dev.yml up -d`
starts only Postgres and Redis (on 127.0.0.1). See
[../infrastructure/deployment/DEPLOYMENT.md](../infrastructure/deployment/DEPLOYMENT.md).
To make your account an admin: `docker compose exec api python -m scripts.promote you@example.com`.

## Example workflow

```bash
BASE=http://localhost:8000/api/v1

# 1. Register
TOKEN=$(curl -s -X POST $BASE/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"a-long-enough-password"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

AUTH="Authorization: Bearer $TOKEN"

# 2. Create a project
PROJECT=$(curl -s -X POST $BASE/projects -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Travel site","max_cost_usd":2.0}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

# 3. Start a workflow
WORKFLOW=$(curl -s -X POST $BASE/workflows -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d "{\"project_id\":\"$PROJECT\",\"goal\":\"Build a responsive travel website with a hero, destination cards and a contact form\",\"routing_strategy\":\"balanced\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

# 4. Watch it run
curl -N -H "$AUTH" $BASE/workflows/$WORKFLOW/stream

# 5. Collect the results
curl -s -H "$AUTH" $BASE/workflows/$WORKFLOW/tasks
curl -s -H "$AUTH" $BASE/workflows/$WORKFLOW/artifacts
curl -s -H "$AUTH" $BASE/workflows/$WORKFLOW | python3 -m json.tool
```

## API

Full OpenAPI at `/openapi.json`, browsable at `/docs`.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/auth/register` | Create an account, receive a token pair |
| POST | `/api/v1/auth/login` | Exchange credentials for tokens |
| POST | `/api/v1/auth/refresh` | Rotate the token pair; a reused refresh token ends the session |
| POST | `/api/v1/auth/logout` | End this session (or `{"everywhere": true}`: every session) |
| POST | `/api/v1/auth/password-reset/request` | Email a reset link (same answer whether or not the account exists) |
| POST | `/api/v1/auth/password-reset/confirm` | Set a new password; signs out every session |
| POST | `/api/v1/auth/verify-email/request`, `/confirm` | Email verification |
| GET | `/api/v1/auth/me` | The current user |
| POST/GET | `/api/v1/projects` | Create / list projects |
| GET/PATCH/DELETE | `/api/v1/projects/{id}` | Read / update / delete a project (refused while a workflow runs) |
| POST/GET | `/api/v1/projects/{id}/attachments` | Upload / list text files handed to the planner |
| GET/DELETE | `/api/v1/attachments/{id}`, `/download` | One attachment |
| POST/GET | `/api/v1/workflows` | Create (`Idempotency-Key` header, `team`, `model`, `require_approval`, `attachment_ids`) / list (`status`, `q`, `project_id`) |
| GET | `/api/v1/workflows/{id}` | Workflow state and final result |
| POST | `/api/v1/workflows/{id}/plan` | Plan (or re-plan) and wait for approval |
| POST | `/api/v1/workflows/{id}/approve` | Approve the plan, including edits, and run it |
| POST | `/api/v1/workflows/{id}/run` | Start; on a planned workflow, run what is pending |
| POST | `/api/v1/workflows/{id}/pause` | Pause; unstarted tasks go back in the queue |
| POST | `/api/v1/workflows/{id}/resume` | Resume a paused workflow |
| POST | `/api/v1/workflows/{id}/cancel` | Cancel |
| GET | `/api/v1/workflows/{id}/tasks` | The task graph with routing reasons |
| GET | `/api/v1/workflows/{id}/artifacts` | Files produced |
| GET | `/api/v1/workflows/{id}/archive` | The whole project as one zip |
| GET | `/api/v1/workflows/{id}/stream` | SSE execution events, replayed from `?after=` / `Last-Event-ID` |
| GET | `/api/v1/workflows/{id}/events` | Recorded events as JSON |
| GET | `/api/v1/workflows/{id}/tool-calls` | Every tool call in the workflow, test runs included |
| GET | `/api/v1/workflows/{id}/reviews` | Every review in the workflow |
| POST | `/api/v1/tasks` | Add a task by hand, optionally to a named agent |
| GET/PATCH | `/api/v1/tasks/{id}` | Read / amend a task, or move it to another agent |
| POST | `/api/v1/tasks/{id}/cancel` | Cancel one task |
| POST | `/api/v1/tasks/{id}/retry` | Requeue a finished task |
| GET | `/api/v1/tasks/{id}/reviews` | Every review of the task's attempts |
| GET | `/api/v1/tasks/{id}/tool-calls` | Tools it used, including each test run and its output |
| GET | `/api/v1/projects/{id}/conversations` | A project's threads |
| GET/DELETE | `/api/v1/conversations/{id}` | A thread with its messages / delete the transcript |
| GET/POST | `/api/v1/projects/{id}/memories` | List / add a memory or standing preference |
| GET | `/api/v1/projects/{id}/memories/search?q=` | Find memories by meaning |
| DELETE | `/api/v1/projects/{id}/memories/{mid}` | Forget one |
| POST | `/api/v1/projects/{id}/memories/reindex` | Embed memories written before an embedder existed |
| GET | `/api/v1/artifacts` | Generated files across workflows (no content) |
| GET/DELETE | `/api/v1/artifacts/{id}` | Read / delete one file |
| GET | `/api/v1/artifacts/{id}/download` | Download the raw file |
| GET | `/api/v1/models` | Model catalog with capability scores |
| POST | `/api/v1/models/refresh-stats` | Recompute latency and reliability (admin) |
| GET | `/api/v1/agents` | The roster: built-in, overridden and custom agents |
| GET | `/api/v1/tools` | Every tool, whether it works right now, and who may use it |
| GET | `/api/v1/agents/{name}` | One agent, with its instructions |
| POST | `/api/v1/agents` | Add an agent (admin) |
| PATCH | `/api/v1/agents/{name}` | Re-instruct, re-tune or disable one (admin) |
| DELETE | `/api/v1/agents/{name}` | Remove a custom agent, or restore a built-in (admin) |
| GET/POST | `/api/v1/providers` | List (configured vs verified) / add providers (POST is admin-only) |
| PATCH/DELETE | `/api/v1/providers/{id}` | Set or clear a stored key, enable/disable (admin) |
| POST | `/api/v1/providers/{id}/verify` | Make a real, token-free call and record the result (admin) |
| GET | `/api/v1/usage?days=` | Estimated spend by day, model, provider and role; today against the daily limit |
| GET | `/api/v1/audit-logs` | Security log (own entries; admins see all) |
| GET/POST | `/api/v1/api-keys` | List / create personal API keys (the key is returned once) |
| DELETE | `/api/v1/api-keys/{id}` | Revoke a key |
| GET | `/api/v1/executions` | Every model call |
| GET | `/health`, `/ready` | Liveness and readiness |

### Streamed events

`workflow_created`, `workflow_started`, `planning_started`, `plan_ready`,
`plan_approved`, `task_created`, `model_selected`, `task_queued`,
`task_started`, `task_progress`, `task_completed`, `task_failed`,
`retry_started`, `tool_started`, `tool_completed`, `tests_started`,
`tests_completed`, `tests_skipped`, `validation_started`, `review_completed`,
`artifact_created`, `synthesis_started`, `workflow_completed`,
`workflow_failed`, `workflow_paused`, `workflow_cancelled`, `budget_exceeded`.

Every event is stored before it is sent, with a monotonic sequence number
used as the SSE `id`. A client that reconnects (EventSource sends
`Last-Event-ID`) or passes `?after=<seq>` first receives everything it missed,
then live events — nothing is lost and nothing repeats. `GET
/workflows/{id}/events` returns the same history as JSON.

The first event on every stream is `subscribed`; `ping` arrives every 15
seconds of silence. When the server closes a stream on purpose — the workflow
finished, or was already finished when the stream opened — it sends
`stream_end` last, so a client knows not to reconnect.

## The web UI

The frontend lives in [`../frontend`](../frontend) (Next.js). It keeps the
tokens in httpOnly cookies and proxies `/api/v1/*` here, so the browser never
sees a token and needs no CORS. Run this on port 8000, then `npm run dev`
there, and open http://localhost:3000.

## Execution infrastructure

**Where runs execute.** `EXECUTION_BACKEND=inline` (the default) runs workflows
inside the API process — fine for development, but restarting the API drops
whatever was running. `EXECUTION_BACKEND=celery` hands them to Celery workers
over Redis instead; Compose does this. Start a worker on the host with:

```bash
.venv/bin/celery -A app.workers.celery_app.celery_app worker --pool=solo
```

If the queue is unreachable, starting a run returns 503 rather than accepting
work that will never run.

**Running model-written code.** When the Tester writes tests, it also says how
to run them. The engine copies the whole workspace into a throwaway Docker
container and runs them there:

- no network while tests run (an optional install step before it may use the
  network; `SANDBOX_INSTALL_NETWORK=false` closes that too)
- every Linux capability dropped, `no-new-privileges`, read-only root
  filesystem, non-root user
- memory, CPU and process count capped; a hard timeout that kills the
  container, not just the client

Failing output goes back to the Tester as feedback, the same way review
feedback does; tests that still fail after retries fail the task, and the
synthesizer reports it. The reviewer sees the real test output. Every run is
recorded as a `run_tests` tool call. There is no fallback that runs code on the
host: without a container runtime, tests are reported as not run and the task
continues on review alone.

The sandbox needs a Docker-compatible runtime on the machine running the
engine. On a Mac without Docker Desktop: `brew install colima docker && colima
start` (and `colima start` again after a reboot).

Workspaces are created under `SANDBOX_WORKDIR_ROOT` (default
`~/.evergent/sandbox`), because Colima and Docker Desktop share your home
directory with their VM but not the system temp dir — and an unshared
directory mounts as an empty folder without any error. Every run also proves
inside the container that the workspace really arrived before anything
executes, so an empty mount is reported as "tests not run", never as a pass.

## Agent tools

Agents work in rounds: they may call tools, see the results, call more, then
give their final answer. Up to `MAX_TOOL_ROUNDS` (6) rounds of up to 4 calls,
every call recorded as a tool call and streamed as an event.

| Tool | What it does | Needs |
| --- | --- | --- |
| `list_files`, `read_file` | See the workspace, read another agent's actual file | — |
| `write_file` | Save a file the task owns; ownership enforced as for final answers | — |
| `web_search` | Search the web (Tavily or Brave) | `TAVILY_API_KEY` or `BRAVE_SEARCH_API_KEY` |
| `fetch_url` | Read a public page as text | — |
| `run_code` | Run a command against the workspace in the sandbox, offline | Docker |
| `run_sql` | Run SQL on a fresh, throwaway PostgreSQL 17 + pgvector | Docker |

A tool is only offered when it can work right now — `GET /tools` says which are
available and why not. Each agent has its own grant: every engineer gets the
file and web tools, code-writing engineers get `run_code`, and those who touch
data (Backend, Database, Testing, Debugger) get `run_sql`. Change a grant with
`PATCH /agents/{name}` and `"tools": [...]`.

Tools are called through a JSON protocol in the model's ordinary output rather
than each provider's native function-calling API: the same loop works for every
provider and every OpenAI-compatible gateway, and it is fully testable without
credentials.

**`fetch_url` never reaches the server's own network.** Hosts are resolved first
and anything that is not a public address is refused — localhost, private
ranges, link-local (including cloud metadata at 169.254.169.254), CGNAT,
IPv4-mapped IPv6. Redirects are followed by hand and every hop is re-checked,
and the address the socket actually connected to is checked too, against DNS
rebinding.

## Choosing the team

`POST /workflows` takes an optional `team` — the engineers to put on the job:

```json
{"project_id": "…", "goal": "Build a booking site", "team": ["frontend", "backend"]}
```

The planner sees only those engineers and must name one of them for every
task. Work for a speciality left off the team is not planned at all — no tests
without `testing`, no security review without `security` — and the final
write-up says that was the team's scope, not a failure. Hand-added and moved
tasks are held to the team too. Omit `team` to make the whole roster available.

## Conversations and memory

Every workflow is one turn of a conversation. `POST /workflows` without a
`conversation_id` opens a new thread; with one, it continues that thread. When
the Commander plans a request it sees the thread's earlier turns — so "now add
dark mode" has a referent — and the project's most relevant memories.

When a workflow completes, what it built (request, objective, stack, files,
summary) is written to project memory; the result, or the failure, is written
to the thread. You can also add standing preferences ("always use Tailwind").

Memory search is semantic when an embedding provider is configured
(`GOOGLE_API_KEY` → `gemini-embedding-001`, or `OPENAI_API_KEY` →
`text-embedding-3-small`, both at 768 dimensions). Vectors live in a pgvector
`vector(768)` column with an HNSW cosine index, so recall stays fast as history
grows. Each vector records the model that made it, and search never compares
vectors from different models. Without an embedder, or for memories written
before one existed, search ranks by keyword overlap; `/memories/reindex`
upgrades them once an embedder is set.

The migration runs `CREATE EXTENSION IF NOT EXISTS vector`. Managed Postgres
(RDS, Supabase, Neon, Cloud SQL) ships pgvector; self-hosted needs the package
(`brew install pgvector`, or the `pgvector/pgvector` Docker image).

## Layout

```
app/
  api/v1/        auth, projects, workflows, catalog, health
  core/          config, security, logging, errors
  db/            base, session, seed
  models/        SQLAlchemy entities and enums
  schemas/       Pydantic request/response models
  services/
    providers/   AIProvider interface + Anthropic, OpenAI, Google, gateways
    router/      capability/cost/latency scoring
    agents/      12 specialist definitions
    orchestrator/ planner, engine, synthesizer, events, instrumented llm
    context/     per-task context assembly within a token budget
    validation/  reviewer
    tools/       tool framework with permissions
    memory/      four-scope memory store
  workers/       Celery app
```

## Extending

### Add an AI provider

1. Subclass `AIProvider` in `app/services/providers/` and implement
   `is_configured`, `generate` and `stream`. If the endpoint is
   OpenAI-compatible, subclass `OpenAICompatibleProvider` instead — that is
   usually about ten lines.
2. Register it in `PROVIDER_FACTORIES` in `app/services/providers/registry.py`.
3. Add its models to `app/db/seed.py` with capability scores.

Nothing in the router, planner or engine changes.

### Add an agent

At runtime, with no code change or redeploy:

```bash
curl -X POST localhost:8000/api/v1/agents -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "name": "mobile",
    "display_name": "Mobile Engineer",
    "description": "Builds React Native screens.",
    "instructions": "You are a mobile engineer. You build for small screens first.",
    "capabilities": ["frontend", "coding"]
  }'
```

From the next workflow run, the planner sees it in its roster and can assign it
work; the router staffs it with the model that scores best on its
`capabilities`. Write only the role in `instructions` — the output format the
engine parses is appended automatically, so a new agent cannot break it.

`PATCH /api/v1/agents/frontend` re-instructs a built-in without touching the
shipped default; `DELETE` on it restores the default. Built-ins can be disabled
but not deleted. `planner`, `reviewer` and `synthesizer` are the engine's own
machinery and cannot be changed.

To ship an agent with the code instead, add a `make_worker(...)` entry to
`BUILTIN_AGENTS` in `app/services/agents/registry.py`. Its `handles` list sends
work of those task types to it without the planner having to name it.

### Add a tool

Subclass `Tool` in `app/services/tools/`, declare `input_schema` and a
`ToolPermission`, implement `run`, and register it. Then add its name to the
`allowed_tools` of agents that may use it — an agent can never call a tool
outside its own list.

## Tests

```bash
.venv/bin/python -m pytest          # 287 tests (+1 that needs TEST_POSTGRES_URL)
.venv/bin/ruff check app tests
```

The suite runs on SQLite, so it needs no database server. `tests/test_engine.py`
drives the real engine end to end against a scripted provider, which verifies
planning, parallel execution, ownership enforcement, retries onto a different
model, pause and resume, custom agents, review and synthesis without
credentials or spend.

## Security

JWT access and refresh tokens with type separation, bcrypt password hashing
(SHA-256 pre-hash so passwords beyond 72 bytes keep their entropy), Fernet
encryption of provider credentials at rest, role-based access on admin routes,
ownership scoping on every query, rate limiting, security headers, and per-tool
permissions. Provider API keys are never returned by the API — only masked
previews.

Internal errors are logged in full and returned as a generic message, so stack
details and paths never reach a caller.

Agents with web access read untrusted pages. A page can try to instruct the
model — and an agent that can both read your workspace and fetch URLs could be
steered into putting workspace content in a URL it fetches. `fetch_url` cannot
reach anything internal, and code only runs in the sandbox, but outbound GETs
to public hosts are allowed by design. If a project's workspace is sensitive,
remove the web tools from its agents or set `WEB_FETCH_ENABLED=false`.

## What this does not do yet

- **Only tests are executed**, and only where a container runtime exists.
  Other agents' code is reviewed, not run, and a failing test is fed back to
  the Tester only — no Debugger task is spawned to fix the code under test.
- **No Git integration.** Download a workflow as a zip from `/archive`.
- **Tools have not been driven by a real model yet.** The loop is tested end
  to end with a scripted model, and every tool against real infrastructure
  (Docker, Postgres, the public web) — but no model API key is configured.
- **Semantic memory needs an embedding key.** With none configured it falls
  back to keyword ranking. The vector path is tested against real pgvector,
  but with a stand-in embedder: no real embedding model has been called yet.
- **Capability scores are hand-seeded priors.** They live in data, so a
  benchmark job can rewrite them and routing improves with no code change.
- Rate limits are shared through Redis; if Redis is down each API process
  falls back to counting on its own.
