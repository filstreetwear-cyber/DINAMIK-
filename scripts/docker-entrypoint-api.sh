#!/usr/bin/env bash
# Phase J (Docker/VPS) entrypoint for the `api` service.
#
# depends_on: postgres: condition: service_healthy (docker-compose.yml)
# only guarantees Postgres accepts TCP connections and answers pg_isready
# — not that it's done with first-boot initialization by the instant this
# container's process starts. A short retry loop around the migration
# step is cheap insurance against that race, and it doubles as the thing
# that actually applies the schema on every deploy, not just the first
# one.
set -euo pipefail

echo "[api] applying database migrations..."
attempt=0
until alembic upgrade head; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "[api] migrations failed after ${attempt} attempts, giving up" >&2
        exit 1
    fi
    echo "[api] migration attempt ${attempt} failed, retrying in 2s..."
    sleep 2
done
echo "[api] migrations applied."

exec "$@"
