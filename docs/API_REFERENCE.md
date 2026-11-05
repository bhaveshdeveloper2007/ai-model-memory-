# API reference

The MCP server provides the 16 `memory_*` tools listed in Codex. A loopback HTTP API exposes GET `/health`, `/projects`, `/summary?cwd=...`, `/search?q=...` and POST `/` with a JSON activity event. Start with `scripts/dashboard.sh`. The API is local and unauthenticated; keep it bound to loopback.
