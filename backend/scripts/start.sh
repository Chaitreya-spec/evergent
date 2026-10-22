#!/bin/sh
# Start the API on a host with no separate migration step (Render's free
# plan): bring the schema and the model catalogue up to date, then serve.
# Both steps are no-ops when there is nothing to do.
#
#   sh scripts/start.sh
set -e

echo "Applying database migrations…"
alembic upgrade head

echo "Seeding the model catalogue…"
python -m scripts.seed

echo "Starting the API on port ${PORT:-8000}…"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
