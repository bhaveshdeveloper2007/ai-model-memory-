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
if [ -n "${MEMORYOS_AGENT_TOKEN_FILE:-}" ]; then
  if [ ! -r "$MEMORYOS_AGENT_TOKEN_FILE" ]; then
    echo "The configured agent token file cannot be read." >&2
    exit 1
  fi
  MEMORYOS_API_TOKEN="$(cat "$MEMORYOS_AGENT_TOKEN_FILE")"
  export MEMORYOS_API_TOKEN
fi
exec python3 "$ROOT/mcp-server/server.py"
