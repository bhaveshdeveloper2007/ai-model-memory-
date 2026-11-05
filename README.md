# Memory OS

Local-first memory for AI-assisted development on macOS. This public-source repository contains the installable program. By default, your database, event journal and checkpoints stay on your Mac under `~/Documents/MemoryOS`; optional encrypted backup requires a private repository you choose.

## One-command setup

Clone this repository, then run its installer:

```bash
git clone https://github.com/bhaveshdeveloper2007/ai-model-memory-.git
cd ai-model-memory-
./install.sh
```

Restart Codex after installation. The installer adds the `memory_os` MCP server and metadata-only lifecycle hooks to your user-level Codex settings. It preserves existing settings and makes permission-restricted backups before changes. It does not change individual project repositories.

The public-source installer does not send data anywhere by default. To use encrypted GitHub backups, first create your own private empty repository and authenticate GitHub CLI with `gh auth login`, then provide its `OWNER/REPOSITORY`:

```bash
MEMORYOS_BACKUP_REPO=OWNER/PRIVATE-REPOSITORY ./install.sh
```

Or enable backups later:

```bash
~/Documents/MemoryOS/scripts/enable-backups.sh OWNER/PRIVATE-REPOSITORY
```

The installer verifies the destination is private. Backups go to its separate `memoryos-backups` branch and are encrypted before upload. Keep the generated recovery key outside your Mac; without it, encrypted backups cannot be restored if your Keychain is lost. See [encrypted backups and recovery](docs/BACKUPS_AND_RECOVERY.md).

If you already have the repository open in Codex, you can simply ask Codex: **“Set up Memory OS from this repository by running `./install.sh`, then check that the database initializes and the Codex MCP server is configured.”**

## Use it

Open any project in Codex. Memory OS detects its Git root and remote (or the local path for a non-Git folder). Ask Codex to resume a project, save a decision, record a task, or save a checkpoint; the global Codex instructions also tell it to bootstrap project context when MCP tools are available.

To open the local dashboard, run:

```bash
~/Documents/MemoryOS/scripts/dashboard.sh
```

Then visit http://127.0.0.1:8765. The dashboard is local-only and runs while that command is active.

## Local data and privacy

- SQLite database: `~/Documents/MemoryOS/storage/database/memoryos.sqlite3`
- Append-only event journal: `~/Documents/MemoryOS/storage/journal/events.jsonl`
- Backups of existing Codex settings: `~/.codex/`
- Hook capture stores event names and limited tool/path metadata, not prompt text or tool output.
- Optional backups are configured per user, encrypted with AES-GCM, and refused unless the destination repository is private.
- The installer preserves existing memory data during upgrades. It does not install a privileged service or request macOS privacy permissions.

Read `docs/SECURITY.md` and `docs/INSTALLATION.md` for details. Claude Code and Cursor setup are not yet included in the installer.
