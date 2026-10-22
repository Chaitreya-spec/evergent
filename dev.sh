#!/usr/bin/env bash
# Start everything for local development in one go:
#
#   ./dev.sh
#
# Then open http://localhost:3000. Press Ctrl+C to stop the backend and the
# website (Postgres and Redis keep running as background services).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
mkdir -p "$BACKEND/logs"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }

in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1; }

for port in 3000 8000; do
  if in_use "$port"; then
    warn "Port $port is already in use — is ./dev.sh already running in another window?"
    warn "Stop it there (Ctrl+C), or free the port: lsof -ti tcp:$port | xargs kill"
    exit 1
  fi
done

# --- Database and Redis (Homebrew services that start at login) -------------
if ! pg_isready -q -h localhost -p 5432; then
  say "Starting PostgreSQL…"
  brew services start postgresql@17 >/dev/null
  until pg_isready -q -h localhost -p 5432; do sleep 1; done
fi
if ! redis-cli ping >/dev/null 2>&1; then
  say "Starting Redis…"
  brew services start redis >/dev/null
  until redis-cli ping >/dev/null 2>&1; do sleep 1; done
fi

# --- Optional: email catcher and test sandbox (need Docker via Colima) ------
if docker info >/dev/null 2>&1; then
  docker compose -f "$ROOT/docker-compose.dev.yml" up -d mailpit >/dev/null 2>&1 \
    && say "Email inbox: http://localhost:8025"
else
  warn "Docker (Colima) is not running: emails are not delivered and generated tests are reported 'not run'."
  warn "To enable both: colima start   (then run ./dev.sh again)"
fi

# --- Backend ------------------------------------------------------------------
say "Updating the database schema…"
(cd "$BACKEND" && .venv/bin/alembic upgrade head >/dev/null 2>&1 && .venv/bin/python -m scripts.seed >/dev/null 2>&1)

say "Starting the backend on http://localhost:8000 (log: backend/logs/backend.log)…"
(cd "$BACKEND" && exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload \
  >>"$BACKEND/logs/backend.log" 2>&1) &
BACKEND_PID=$!

# --- Website ------------------------------------------------------------------
say "Starting the website on http://localhost:3000…"
(cd "$FRONTEND" && EVERGENT_API_URL=http://127.0.0.1:8000 NEXT_TELEMETRY_DISABLED=1 exec npx next dev --port 3000 \
  >>"$BACKEND/logs/frontend.log" 2>&1) &
FRONTEND_PID=$!

stop() {
  echo
  say "Stopping the backend and the website…"
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  # uvicorn --reload and next dev run child processes; take them down too.
  for port in 8000 3000; do lsof -ti tcp:"$port" | xargs kill 2>/dev/null || true; done
  wait 2>/dev/null || true
  say "Stopped."
}
trap stop INT TERM

until curl -sf -o /dev/null http://127.0.0.1:8000/health; do
  kill -0 "$BACKEND_PID" 2>/dev/null || { warn "The backend failed to start — see backend/logs/backend.log"; stop; exit 1; }
  sleep 1
done
until curl -sf -o /dev/null http://127.0.0.1:3000/api/health; do
  kill -0 "$FRONTEND_PID" 2>/dev/null || { warn "The website failed to start — see backend/logs/frontend.log"; stop; exit 1; }
  sleep 1
done

say ""
say "Ready: http://localhost:3000"
say "Keep this window open. Press Ctrl+C to stop."
[ -n "${NO_OPEN:-}" ] || open http://localhost:3000 2>/dev/null || true
wait
