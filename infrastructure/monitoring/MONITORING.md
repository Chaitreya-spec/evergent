# Monitoring

## Health endpoints

| Endpoint | Where | Meaning |
| --- | --- | --- |
| `GET /health` | API (internal) | Process is up. Used by the container health check. |
| `GET /ready` | API (internal) | Database reachable and at least one provider configured. Reports Redis too (optional: the event bus falls back to in-process). |
| `GET /api/health` | Frontend (public) | Frontend up, and whether it can reach the API. Reveals nothing else. |

Point an uptime checker at `https://$DOMAIN/api/health` and alert when it is
not `200` or when `backend` is not `"ok"`.

## Logs

Every service logs to stdout; read them with `docker compose logs -f api worker`.
The API writes one structured line per request (method, path, status,
duration, request id) and the request id is returned as `X-Request-ID`, so a
user-reported problem can be traced to its log lines. Ship the Docker logs to
your aggregator (Loki, CloudWatch, Datadog…) with the json-file or a logging
driver; nothing in the logs contains API keys or passwords (the audit service
strips secret fields, and keys are never logged).

Useful events to alert on:

- `workflow_orphaned` — a workflow lost its worker (restart, crash, OOM). It
  is paused automatically; frequent occurrences mean workers are dying.
- `redis_unavailable` — the event bus fell back to in-process delivery; with
  a separate worker, live progress stops reaching browsers until Redis is back.
- `event_persist_failed` — an event could not be stored (database trouble).
- `sandbox_setup_failed` — tests could not be run on a host that should run them.
- HTTP `5xx` rate on the `request` log lines.

## Product signals

Inside the app:

- **Usage & cost** — estimated spend per day, model, provider and role, failed
  model calls, and today's spend against the daily limit.
- **Settings → Security log** — sign-ins, failed sign-ins, refresh-token reuse
  (a stolen-token signal), provider key changes, project deletions. From a
  shell: `docker compose exec api python -m scripts.auth_log` (add `-f` to
  follow, `--all` for every audited action, `--email` for one account).
- **Models & providers** — whether each provider is configured and whether its
  last verification succeeded.

The estimates use catalogue list prices; reconcile against provider invoices
monthly.

## Database

Watch disk usage of the `postgres_data` volume and the size of the
`workflow_events` and `executions` tables, which grow with every run. Both are
safe to prune for old, finished workflows (deleting a workflow cascades).
