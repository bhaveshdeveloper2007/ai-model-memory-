# Agent integrations

## Codex

The installed first-generation service exposes the shared stdio MCP server (`engine/memoryos.py mcp`) with 46 tools — the full shared-memory surface plus the original `memory_*` names — and metadata-only lifecycle hooks. Registered in `~/.codex/config.toml` as `memory_os`; verify from a user terminal with `codex mcp list`. The installer (updated 2026-11-07) now deduplicates its hook entries correctly; 35 accumulated duplicate hook groups were removed from the live configuration on that date (backup at `~/.codex/hooks.json.pre-dedupe-*.bak`).

The next-generation stdio adapter for the PostgreSQL backend remains in `mcp-server/server.py` and is not installed automatically. See [POSTGRES_BACKEND_SETUP.md](../integrations/codex/POSTGRES_BACKEND_SETUP.md).

## Claude Code

Not installed on this machine; runtime verification is pending. The integration script `integrations/claude-code/configure.sh` registers the same shared MCP server under the user scope (`claude mcp add --scope user memory_os`) once the CLI is installed. After setup, Claude Code and Codex read and write the same project-scoped records through one engine.

## Generic agents

Any MCP-compatible or HTTP client can integrate: the MCP stdio interface for agents, the versioned loopback API (PostgreSQL backend, bearer token) for services. Agent-supplied text is untrusted and is never interpreted as executable instructions. Keep `~/.hermes/` untouched when adding agents.
