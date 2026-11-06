# Codex setup for the PostgreSQL backend

This template targets the next-generation backend and does not change the installed SQLite integration. It has been checked against the locally installed Codex CLI 0.166.0-alpha.6; the supported CLI pattern is `codex mcp add NAME -- COMMAND [ARGS...]`.

After PostgreSQL migration succeeds and the backend API is running:

1. In a terminal, from the Memory OS source checkout, check the stdio handshake with:

   ```sh
   printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' | scripts/run-mcp-server.sh
   ```

   It uses the local API token from the ignored `backend/.env` file.
2. Add the MCP server with the absolute script path:

   ```sh
   codex mcp add memory_os_postgres --env MEMORYOS_AGENT_TOKEN_FILE=/private/path/to/codex-agent-token -- /absolute/path/to/ai-model-memory-/scripts/run-mcp-server.sh
   ```

3. Verify it is listed and enabled with `codex mcp list`, then start a new Codex session and call `memory_bootstrap` or `project_context`.

For least privilege, create a per-agent credential through the owner API, grant that agent only its project, store the token in a private file with mode `600`, and set `MEMORYOS_AGENT_TOKEN_FILE` in the Codex MCP environment. The installed Codex CLI accepts `--env KEY=VALUE` for a stdio server; this was confirmed locally with `codex mcp add --help`. Do not put the owner token directly in `config.toml` or a shared repository. The installer does not edit `~/.codex/config.toml`.

The current wrapper uses the owner token unless an agent token file is explicitly configured. Full unattended Keychain enrollment is still pending; do not use the owner token for untrusted agents.
