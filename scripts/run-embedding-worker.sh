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
export PYTHONPATH="$ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$ROOT/backend/.venv/bin/python" -m memoryos_backend.workers.embeddings --once
