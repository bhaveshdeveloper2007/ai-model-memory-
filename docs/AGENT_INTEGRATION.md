# Agent integration

Any MCP-capable agent shares the same memory through one engine.

## Codex (verified)
Registered in `~/.codex/config.toml` as `memory_os` → `python3 ~/Documents/MemoryOS/engine/memoryos.py mcp`. Check: `codex mcp list`. Lifecycle hooks are metadata-only and deduplicated by the installer.

## Claude Code (pending CLI install)
```bash
npm install -g @anthropic-ai/claude-code
bash ~/Developer/ai-model-memory-/integrations/claude-code/configure.sh   # claude mcp add --scope user memory_os
claude mcp list   # verify
```
Then Codex and Claude Code read and write the same project-scoped records.

## Any other MCP client
Run the same stdio command. Identify with a stable agent name (passed as `agent` in tool arguments) — identities, sessions, and permissions are recorded per agent.

## Rules
Search before asking; store outcomes not raw transcripts; never store secrets (redaction is a safety net, not a license); verification requires evidence; project isolation is enforced by the engine.
