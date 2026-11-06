# Agent integrations

## Codex

The installed first-generation service currently exposes a stdio MCP server and metadata-only hooks. The next-generation stdio adapter is in `mcp-server/server.py`. It is not installed into Codex settings automatically. See [POSTGRES_BACKEND_SETUP.md](../integrations/codex/POSTGRES_BACKEND_SETUP.md) for the manual setup command.

## Claude Code

Not installed as a local CLI and not configured or verified on this machine. Before creating an installer, inspect the installed Claude Code version and its current official MCP and lifecycle-hook documentation. Use explicit MCP calls where reliable hook capture is unavailable.

## Generic agents

The versioned loopback API is the generic integration surface. It requires a local bearer token and project-scoped identifiers. Agent-supplied text is untrusted and is never interpreted as executable instructions.

See [CODEX_INTEGRATION.md](CODEX_INTEGRATION.md) and [CLAUDE_CODE_INTEGRATION.md](CLAUDE_CODE_INTEGRATION.md) for the current first-generation guidance.
