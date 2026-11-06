#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/backend/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "Run scripts/setup-dev.sh first." >&2
  exit 1
fi
set -a
. "$ENV_FILE"
set +a
exec "$ROOT/backend/.venv/bin/uvicorn" memoryos_backend.api:app --app-dir "$ROOT/backend/src" --host "${MEMORYOS_BIND_HOST:-127.0.0.1}" --port "${MEMORYOS_PORT:-8766}" --log-level "${MEMORYOS_LOG_LEVEL:-info}"
