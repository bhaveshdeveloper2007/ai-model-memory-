#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="$ROOT/backend/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "Run scripts/setup-dev.sh first." >&2
  exit 1
fi
if ! psql -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname='memoryos_test'" | grep -qx 1; then
  createdb memoryos_test
fi

set -a
. "$ROOT/backend/.env"
set +a
export MEMORYOS_DATABASE_URL="postgresql+psycopg:///memoryos_test"
export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"
if ! psql -d memoryos_test -Atqc "SELECT 1 FROM pg_available_extensions WHERE name='vector'" | grep -qx 1; then
  PG_VERSION="$(psql -d memoryos_test -Atqc 'SHOW server_version')"
  echo "The running PostgreSQL $PG_VERSION server cannot see compatible pgvector extension files; integration tests were not run." >&2
  exit 2
fi
cd "$ROOT/backend"
"$VENV_PY" -m alembic -c alembic.ini upgrade head
exec "$VENV_PY" -m pytest -q
