#!/bin/bash
# Connect Claude Code to the shared Memory OS MCP server.
#
# Claude Code supports MCP servers through `claude mcp add`. This script
# registers the same stdio engine Codex uses, so both agents read and write
# one shared local memory database. It never touches ~/.hermes, ReverseLab,
# or any other installation.
set -euo pipefail

if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code CLI is not installed. Install it from https://claude.com/claude-code, then re-run this script." >&2
  exit 1
fi

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)"
ENGINE="$SCRIPT_PATH/../../engine/memoryos.py"
PYTHON_BIN="$(command -v python3)"

# User-level registration so every Claude Code session can use Memory OS.
claude mcp add --scope user memory_os -- "$PYTHON_BIN" "$ENGINE" mcp
echo "Registered memory_os MCP server for Claude Code (user scope)."
echo "Verify with: claude mcp list"
