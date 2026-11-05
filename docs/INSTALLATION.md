# Installation

Memory OS is installed under `~/Documents/MemoryOS`. Codex MCP and hook settings were merged into `~/.codex/config.toml` and `~/.codex/hooks.json`; copies of both originals are in `~/.codex/*.memoryos` with mode 0600. Restart Codex to load the new MCP server and hooks.

Run `python3 ~/Documents/MemoryOS/engine/memoryos.py init` to initialize the database. Run `~/Documents/MemoryOS/scripts/dashboard.sh` and open http://127.0.0.1:8765. The dashboard process is currently manual; no LaunchAgent has been installed.
