#!/usr/bin/env bash
# Update a running deployment to an image tag CI published, with a health
# check and automatic rollback if the new version does not come up.
#
#   infrastructure/docker/deploy.sh <git sha>     # deploy that commit
#   infrastructure/docker/deploy.sh main          # deploy the tip of main
#
# Requires REGISTRY in the environment or in .env (see DEPLOYMENT.md §3), and
# a `docker login ghcr.io` while the packages are private. Set SKIP_BACKUP=1
# to skip the pre-deploy backup — only sensible for a release you know carries
# no migration.
set -euo pipefail

ref="${1:?Usage: deploy.sh <git sha or branch>}"
cd "$(dirname "$0")/../.."

# Compose reads .env itself; this script needs two of the values as well.
[ -f .env ] || { echo "No .env in $(pwd) — see DEPLOYMENT.md §2." >&2; exit 1; }

# Read one key without sourcing the file: .env is not shell, and a value like
# `SMTP_FROM=Evergent <no-reply@example.com>` would be read as a redirect.
# Takes the last assignment, drops an inline comment, unquotes and trims.
env_value() {
  sed -n "s/^[[:space:]]*$1=//p" .env | tail -n 1 \
    | sed -e 's/[[:space:]]#.*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/" \
          -e 's/[[:space:]]*$//'
}

REGISTRY="${REGISTRY:-$(env_value REGISTRY)}"
PUBLIC_URL="${PUBLIC_URL:-$(env_value PUBLIC_URL)}"

: "${REGISTRY:?REGISTRY is not set: add it to .env, e.g. REGISTRY=ghcr.io/chaitreya-spec/}"
export REGISTRY

compose=(docker compose --profile tls)

# A dirty tree means someone edited the compose file or Caddyfile on the host.
# Overwriting that silently would discard a fix made during an incident.
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "The working tree has uncommitted changes. Commit, stash or revert them first:" >&2
  git status --short >&2
  exit 1
fi

previous="$(cat .deployed-tag 2>/dev/null || true)"
echo "Fetching ${ref}…"
git fetch --quiet origin
target="$(git rev-parse --verify "origin/${ref}" 2>/dev/null || git rev-parse --verify "${ref}")"

if [ "${SKIP_BACKUP:-}" != "1" ]; then
  echo "Backing up before deploying…"
  infrastructure/docker/backup.sh
fi

echo "Checking out ${target}…"
git checkout --quiet --detach "${target}"

# Roll the images back to whatever was running. The database is NOT rolled
# back: a release that migrated the schema needs `alembic downgrade` or the
# backup taken above (DEPLOYMENT.md §5).
rollback() {
  echo
  echo "Deploy failed." >&2
  if [ -z "${previous}" ]; then
    echo "No previous tag recorded, so nothing to roll back to. The stack may be" >&2
    echo "part-started: check 'docker compose ps' and the logs." >&2
    return
  fi
  echo "Rolling the images back to ${previous}…" >&2
  if IMAGE_TAG="${previous}" "${compose[@]}" up -d --wait --wait-timeout 180; then
    echo "Rolled back to ${previous}. If this release migrated the database," >&2
    echo "the schema is still the new one — downgrade or restore the backup." >&2
  else
    echo "Rollback also failed. Restore the backup: see BACKUP_AND_RESTORE.md." >&2
  fi
}

echo "Pulling images at ${target}…"
IMAGE_TAG="${target}" "${compose[@]}" pull --quiet

echo "Starting…"
# `migrate` runs alembic and exits; --wait blocks until api, worker, frontend
# and the datastores report healthy, and fails if they do not.
if ! IMAGE_TAG="${target}" "${compose[@]}" up -d --wait --wait-timeout 300; then
  rollback
  exit 1
fi

# --wait trusts the containers' own health checks; this confirms the whole
# path a browser takes, through Caddy and the frontend to the API.
if [ -n "${PUBLIC_URL:-}" ]; then
  echo "Checking ${PUBLIC_URL}/api/health…"
  for attempt in $(seq 1 10); do
    if curl -fsS --max-time 10 "${PUBLIC_URL}/api/health" >/dev/null; then
      break
    fi
    if [ "${attempt}" = 10 ]; then
      echo "${PUBLIC_URL}/api/health did not respond." >&2
      rollback
      exit 1
    fi
    sleep 3
  done
fi

echo "${target}" > .deployed-tag
echo
echo "Deployed ${target}."
"${compose[@]}" ps
