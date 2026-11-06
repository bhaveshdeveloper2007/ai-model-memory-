#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec swift run --package-path "$ROOT/apps/macos" MemoryOSMenuBar
