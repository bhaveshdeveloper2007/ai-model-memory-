#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec swift build --package-path "$ROOT/apps/macos" --configuration release
