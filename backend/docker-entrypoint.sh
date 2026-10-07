#!/bin/sh
# JobPilot backend container entrypoint.
#
# 1. Wait for PostgreSQL (bounded; Compose's health check normally makes this instant).
# 2. Bring the schema to the Alembic head -- a fresh database is created from scratch.
# 3. Only then start the given command (the API by default).
#
# A failure in 1 or 2 stops the container with a non-zero exit and a clear log line
# (event=migration_failed ...); the API never starts on a missing or partial schema.
# Set RUN_MIGRATIONS=false to skip step 2 (e.g. when a separate release job migrates);
# the API's readiness check (/health/ready) still reports a schema that is not at head.
set -eu

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    python -m app.db.migrate
else
    echo "RUN_MIGRATIONS=false: not migrating; checking the schema only"
    python -m app.db.migrate --check
fi

exec "$@"
