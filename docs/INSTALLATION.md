# Next-generation backend development

The existing installer still installs the first-generation SQLite service. The new FastAPI backend is under development and is not installed over that service.

Development setup uses an isolated `memoryos_dev` database, `backend/.venv`, and a mode-600 local token file at `backend/.env` (gitignored). Run `scripts/setup-dev.sh`; it will not start or replace the user's PostgreSQL service. Before migrating, it checks that the active server can see pgvector. On the current Mac this check blocks because the PostgreSQL 15 service lacks compatible pgvector extension files. No backend schema has been applied.

After a compatible extension is available, the API binds to `127.0.0.1:8766`. The vault projection worker runs with `scripts/run-vault-worker.sh`; the optional browser dashboard uses `scripts/serve-dashboard.sh` on `127.0.0.1:8767`. Ollama's `all-minilm` is an optional 384-dimension local embedding model (about 46 MB). Configure `MEMORYOS_EMBEDDING_MODEL=all-minilm` and `MEMORYOS_EMBEDDING_MODEL_VERSION=all-minilm-latest` in the ignored local environment file only if semantic retrieval is wanted. See [the official model page](https://ollama.com/library/all-minilm).

---

# Installation

Memory OS is installed under `~/Documents/MemoryOS`. Codex MCP and hook settings are merged into `~/.codex/config.toml` and `~/.codex/hooks.json`; copies of originals are created in `~/.codex/*.memoryos` with mode 0600 before changes. Restart Codex to load the new MCP server and hooks.

GitHub backup is opt-in. Give the installer your own private repository with `MEMORYOS_BACKUP_REPO=OWNER/REPOSITORY ./install.sh`, or run `scripts/enable-backups.sh OWNER/REPOSITORY` later. The public code repository has no default backup destination.

Run `python3 ~/Documents/MemoryOS/engine/memoryos.py init` to initialize the database. Run `~/Documents/MemoryOS/scripts/dashboard.sh` and open http://127.0.0.1:8765. The dashboard is manual. The optional backup LaunchAgent runs at login and every 15 minutes when configured.
