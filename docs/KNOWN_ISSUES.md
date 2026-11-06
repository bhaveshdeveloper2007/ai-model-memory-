# Known issues (v0.4)

- Backup uploads remain **BLOCKED**: the configured backup repository `bhaveshdeveloper2007/ai-model-database` is PUBLIC (re-checked 2026-11-07). Existing snapshots stay encrypted; make the repository private to resume.
- Claude Code CLI is not installed (user authorization pending). Its MCP registration is prepared via `integrations/claude-code/configure.sh`; the true Codex↔Claude cross-agent test remains unverified until then. The multi-process shared-memory test (three independent MCP server processes) passes.
- Full Xcode is absent from this machine: the SwiftUI menu-bar companion (`apps/macos`) cannot be compiled or XCTest-run.
- The OpenAI account hit its usage limit (resets 2026-12-06), so a live `codex exec` tool-call session could not complete; registration, hooks, and the MCP protocol surface are verified by other means.
- SQLite → PostgreSQL cutover remains gated: the backend passes migration + 19 API tests in isolation, but the SQLite data migration path is not implemented.
- R2 live connectivity is BLOCKED awaiting user credentials; all software paths are tested with mocks (see CLOUDFLARE_R2_SETUP.md).
- The R2 encryption envelope depends on the macOS Keychain helper (`bin/memoryos-crypto`); on non-macOS hosts R2 sync reports the helper as unavailable instead of storing plaintext.
