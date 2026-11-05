# Using this repository with Codex

If the user asks to install or set up Memory OS from this repository, inspect `install.sh`, run it, check its output, and confirm that `~/Documents/MemoryOS/storage/database/memoryos.sqlite3` exists and `codex mcp list` includes `memory_os`. Tell the user to restart Codex before expecting tools to appear in the current app. Do not copy local databases, journals, checkpoints, credentials, or Codex settings into this repository. Never claim Claude Code or Cursor integrations work unless those applications have separately been configured and verified.
