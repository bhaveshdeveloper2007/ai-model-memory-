# Installation

Memory OS is installed under `~/Documents/MemoryOS`. Codex MCP and hook settings are merged into `~/.codex/config.toml` and `~/.codex/hooks.json`; copies of originals are created in `~/.codex/*.memoryos` with mode 0600 before changes. Restart Codex to load the new MCP server and hooks.

GitHub backup is opt-in. Give the installer your own private repository with `MEMORYOS_BACKUP_REPO=OWNER/REPOSITORY ./install.sh`, or run `scripts/enable-backups.sh OWNER/REPOSITORY` later. The public code repository has no default backup destination.

Run `python3 ~/Documents/MemoryOS/engine/memoryos.py init` to initialize the database. Run `~/Documents/MemoryOS/scripts/dashboard.sh` and open http://127.0.0.1:8765. The dashboard is manual. The optional backup LaunchAgent runs at login and every 15 minutes when configured.
