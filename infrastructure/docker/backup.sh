#!/usr/bin/env bash
# Back up the database and stored files of a running `docker compose` stack.
#
#   infrastructure/docker/backup.sh            # writes backups/<UTC timestamp>/
#
# Restore with infrastructure/docker/restore.sh <that directory>.
set -euo pipefail

cd "$(dirname "$0")/../.."
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="backups/${stamp}"
mkdir -p "${dest}"

echo "Dumping the database…"
docker compose exec -T postgres pg_dump -U evergent -d evergent --format=custom --no-owner \
  > "${dest}/evergent.dump"

echo "Archiving stored files…"
docker compose run --rm --no-deps -T --entrypoint sh -v "$(pwd)/${dest}:/backup" api \
  -c 'tar -C /data -czf /backup/storage.tar.gz storage'

# Record what was running, so a restore can pair data with the right images.
docker compose images --format json > "${dest}/images.json" 2>/dev/null || true
docker compose exec -T api alembic current > "${dest}/alembic-revision.txt" 2>/dev/null || true

echo "Backup written to ${dest}"
ls -lh "${dest}"
