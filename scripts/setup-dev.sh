#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
ENV_FILE="$BACKEND/.env"
VENV_PY="$BACKEND/.venv/bin/python"

if ! command -v psql >/dev/null 2>&1 || ! command -v createdb >/dev/null 2>&1; then
  echo "PostgreSQL client tools are required. Install PostgreSQL separately, then run this script again." >&2
  exit 1
fi
if ! pg_isready -q; then
  echo "The existing local PostgreSQL service is not ready. This script will not start or replace it." >&2
  exit 1
fi

if ! psql -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname='memoryos_dev'" | grep -qx 1; then
  createdb memoryos_dev
fi

if [ ! -x "$VENV_PY" ]; then
  python3 -m venv "$BACKEND/.venv"
fi
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -e "$BACKEND[dev]"

if [ ! -f "$ENV_FILE" ]; then
  API_TOKEN="$("$VENV_PY" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  umask 077
  cat > "$ENV_FILE" <<EOF
MEMORYOS_DATABASE_URL=postgresql+psycopg:///memoryos_dev
MEMORYOS_API_TOKEN=$API_TOKEN
MEMORYOS_DATA_DIR=$BACKEND/.dev-data
MEMORYOS_BIND_HOST=127.0.0.1
MEMORYOS_PORT=8766
MEMORYOS_LOG_LEVEL=INFO
EOF
  chmod 600 "$ENV_FILE"
  unset API_TOKEN
fi

set -a
. "$ENV_FILE"
set +a
cd "$BACKEND"
if find "$BACKEND/migrations/versions" -maxdepth 1 -type f -name '*.py' ! -name '__init__.py' 2>/dev/null | grep -q .; then
  if ! psql -d memoryos_dev -Atqc "SELECT 1 FROM pg_available_extensions WHERE name='vector'" | grep -qx 1; then
    PG_VERSION="$(psql -d memoryos_dev -Atqc 'SHOW server_version')"
    echo "The running PostgreSQL $PG_VERSION server cannot see compatible pgvector extension files." >&2
    echo "No migration was applied. Install a pgvector build compatible with the running server before continuing." >&2
    exit 2
  fi
  "$VENV_PY" -m alembic -c alembic.ini upgrade head
else
  echo "Dependencies and isolated database are ready; no migration has been generated yet."
fi
echo "Memory OS backend setup finished. Start it with scripts/run-api.sh."
