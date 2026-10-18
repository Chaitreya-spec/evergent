#!/usr/bin/env bash
# Restore a backup made by backup.sh into the running stack. DESTRUCTIVE: the
# current database and stored files are replaced.
#
#   infrastructure/docker/restore.sh backups/20261015T101500Z
set -euo pipefail

src="${1:?Usage: restore.sh <backup directory>}"
cd "$(dirname "$0")/../.."
[ -f "${src}/evergent.dump" ] || { echo "No evergent.dump in ${src}" >&2; exit 1; }

read -r -p "Replace the current database and files with ${src}? Type 'restore': " answer
[ "${answer}" = "restore" ] || { echo "Aborted."; exit 1; }

echo "Stopping the API and worker…"
docker compose stop api worker frontend

echo "Restoring the database…"
docker compose exec -T postgres pg_restore -U evergent -d evergent --clean --if-exists --no-owner \
  < "${src}/evergent.dump"

if [ -f "${src}/storage.tar.gz" ]; then
  echo "Restoring stored files…"
  docker compose run --rm --no-deps -T --entrypoint sh -v "$(cd "${src}" && pwd):/backup" api \
    -c 'rm -rf /data/storage/* && tar -C /data -xzf /backup/storage.tar.gz'
fi

echo "Starting again…"
docker compose up -d api worker frontend
echo "Restored from ${src}."
