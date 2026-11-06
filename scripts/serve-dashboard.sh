#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 -m http.server 8767 --bind 127.0.0.1 --directory "$ROOT/apps/dashboard"
