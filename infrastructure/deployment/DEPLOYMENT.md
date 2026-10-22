# Deploying Evergent

This guide deploys the whole stack — frontend, API, worker, PostgreSQL with
pgvector, Redis — on one Linux host with Docker, behind HTTPS. It is the
configuration in the repository's `docker-compose.yml`.

```
 browser ──HTTPS──▶ Caddy (proxy, profile "tls") ──▶ frontend (Next.js, :3000)
                                                         │  /api/v1/* proxied with the
                                                         │  session's bearer token
                                                         ▼
                                     api (FastAPI, :8000) ──▶ postgres (pgvector)
                                         │       ▲
                                         ▼       │ events
                                       redis ◀── worker (Celery: runs workflows)
```

Only the proxy (ports 80/443) is reachable from outside. The frontend is
published on `127.0.0.1` only; Postgres, Redis, the API and the worker are on
an internal Docker network with no published ports.

## 1. Requirements

- A Linux host with Docker Engine 24+ and the Compose plugin (2.20+).
- 2 vCPU / 4 GB RAM is enough for a small team; workflows are I/O-bound on
  model calls.
- A DNS record for your domain pointing at the host, and ports 80 and 443 open
  (Caddy obtains the certificate automatically).
- At least one model provider key (Anthropic, OpenAI, Google, OpenRouter, or an
  OpenAI-compatible gateway).

## 2. Configure

```bash
git clone <your fork> evergent && cd evergent
cp .env.example .env
```

Fill in `.env`. Required:

| Variable | What it is | How to generate |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | Database password | `openssl rand -base64 32` |
| `SECRET_KEY` | Signs session tokens (≥ 32 chars) | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ENCRYPTION_KEY` | Encrypts provider keys stored via the UI (Fernet) | `python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `PUBLIC_URL` | e.g. `https://evergent.example.com` | — |
| `DOMAIN` | e.g. `evergent.example.com` | — |

The API refuses to start in production with a weak `SECRET_KEY`, a missing
`ENCRYPTION_KEY`, `DEBUG=true`, or `CORS_ORIGINS=*`.

Provider keys can be set in `.env` or later by an administrator in the UI
(**Models & providers → Set key**, stored encrypted). Optional: `TAVILY_API_KEY`
or `BRAVE_SEARCH_API_KEY` for web search, `SMTP_*` for password-reset and
verification email, `STORAGE_BACKEND=s3` and `S3_*` for object storage.

Keep `.env` out of version control (it is git-ignored) and readable only by
the deploying user: `chmod 600 .env`.

## 3. Start

```bash
IMAGE_TAG=$(git rev-parse --short HEAD) docker compose --profile tls up -d --build
docker compose ps
```

The `migrate` service runs `alembic upgrade head` and seeds the model catalogue
once, then exits; the API and worker start only after it succeeds.

To pull the images CI already built instead of building them on the server, set
`REGISTRY` (trailing slash required) and drop `--build`:

```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u <username> --password-stdin
REGISTRY=ghcr.io/chaitreya-spec/ IMAGE_TAG=$(git rev-parse HEAD) \
  docker compose --profile tls up -d
```

CI publishes `ghcr.io/chaitreya-spec/evergent-{backend,frontend}` on every push
to `main`, tagged `latest` and with the full commit SHA. Pin the SHA rather than
`latest` so a rollback is a one-line change. The login step is only needed while
the packages are private; a personal access token with `read:packages` is enough.

Check health:

```bash
docker compose exec api curl -fsS localhost:8000/health   # liveness
docker compose exec api curl -fsS localhost:8000/ready    # database + providers
curl -fsS https://$DOMAIN/api/health                      # frontend + backend reachability
```

## 4. First administrator

The first account is a member. Promote it once, from the host:

```bash
docker compose exec api python -m scripts.promote you@example.com
```

Administrators manage providers and agents; members run workflows.

## 5. Updating and rolling back

Every deploy builds images tagged with `IMAGE_TAG`. To update:

```bash
infrastructure/docker/backup.sh                 # always back up first
git pull
IMAGE_TAG=$(git rev-parse --short HEAD) docker compose --profile tls up -d --build
```

To roll back to the previous version:

```bash
git checkout <previous sha>
IMAGE_TAG=<previous sha> docker compose --profile tls up -d   # images are still local
```

If the release you are leaving applied a database migration, downgrade first
(from the newer image, before switching), or restore the backup taken before
the update (see [BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md)):

```bash
docker compose run --rm api alembic downgrade -1
```

Workflows running during a restart are not lost or silently re-run: the API
notices their heartbeat stopped and marks them **paused** ("interrupted"), with
in-flight tasks put back in the queue. Resume them from the UI.

### Scripted deploys

`infrastructure/docker/deploy.sh <sha>` does the update sequence above in one
step: back up, check out the commit, pull its images, restart, wait for every
container to report healthy, then confirm `PUBLIC_URL/api/health` answers. If
the new version does not come up it puts the previous images back.

```bash
infrastructure/docker/deploy.sh main        # or a specific sha
```

It refuses to run if the working tree is dirty, so a fix applied on the host
during an incident is never silently overwritten. Rollback covers images only
— see the warning above about migrations.

### Deploys from GitHub Actions

The **Deploy** workflow runs that script over SSH. It is manual
(Actions → Deploy → Run workflow), because a deploy migrates the database and
restarts the stack.

One-time setup on the host, as the user that owns the checkout:

```bash
# 1. A key for CI to log in with. Keep the private half out of the host.
ssh-keygen -t ed25519 -f ~/.ssh/evergent-deploy -C github-actions -N ''
cat ~/.ssh/evergent-deploy.pub >> ~/.ssh/authorized_keys

# 2. A read-only GHCR login, so deploys need no registry token from CI.
#    Use a PAT with read:packages only. Skip if you made the packages public.
echo <PAT> | docker login ghcr.io -u <username> --password-stdin

# 3. The host key CI will pin, and the private key to paste into GitHub.
ssh-keyscan -t ed25519 <host> 2>/dev/null
cat ~/.ssh/evergent-deploy
```

Then add these repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | The host's address |
| `DEPLOY_USER` | The user that owns the checkout |
| `DEPLOY_SSH_KEY` | The private key from step 3 |
| `DEPLOY_KNOWN_HOSTS` | The `ssh-keyscan` line from step 3 |
| `DEPLOY_PATH` | Absolute path of the checkout, e.g. `/srv/evergent` |

Optionally set the `DEPLOY_URL` *variable* to your public URL, so a successful
run links to it in the job summary.

The host key is pinned rather than trusted on first use: this connection can
restart production, so an unverified host key is not an acceptable risk. No
secret is passed as an argument to the remote command, where it would appear
in the host's process list.

Add required reviewers to the `production` environment to make deploys need an
approval before they run.

## 6. Running generated tests

Testing agents write tests; executing them needs a container runtime. The
worker container is deliberately **not** given the host's Docker socket — that
would hand model-generated code root on the host — so in this stack tests are
reported as *not run* (honestly, in the UI), and review proceeds on the code
alone.

To execute them, run a worker on a dedicated, isolated host (or VM) that has
Docker, outside this compose stack:

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
EXECUTION_BACKEND=celery SANDBOX_ENABLED=true DATABASE_URL=… REDIS_URL=… \
  CELERY_BROKER_URL=… .venv/bin/celery -A app.workers.celery_app.celery_app worker
```

Each test run is a throwaway container with no privileges, dropped
capabilities, CPU/memory/PID/time limits, a read-only root filesystem, and
network only while dependencies install. Prefer a runtime with stronger
isolation (gVisor `runsc`, Kata, or rootless Docker) on that host.

## 7. Security checklist

- [ ] `.env` filled from the generators above, `chmod 600`, never committed.
- [ ] Only ports 80/443 open in the host firewall.
- [ ] `PUBLIC_URL` uses `https://` (cookies are then `Secure`).
- [ ] Backups scheduled (see below) and a restore rehearsed once.
- [ ] Per-workflow and daily spending limits set to amounts you accept.
- [ ] Provider keys scoped and spend-limited at the provider as well.
- [ ] SMTP configured if users must be able to reset passwords themselves.

## 8. Scaling notes

- The API is stateless; rate limits are shared through Redis, so API replicas
  can be added freely (`--scale api=2` behind the proxy).
- Add worker replicas with `docker compose up -d --scale worker=3`.
- Use `STORAGE_BACKEND=s3` before running more than one host.
- Managed PostgreSQL works: set `DATABASE_URL` and enable the `vector`
  extension there.

See also [BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md) and
[../monitoring/MONITORING.md](../monitoring/MONITORING.md).
