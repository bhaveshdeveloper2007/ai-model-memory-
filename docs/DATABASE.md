# Database (SQLite — authoritative)

Live database: `~/Documents/MemoryOS/storage/database/memoryos.sqlite3` (WAL mode, `synchronous=FULL`, foreign keys ON, files 0600, parent 0700).

## Schema (v0.4)
Core tables: `projects` (archived flag), `workspaces`, `agents`, `agent_settings` (per-agent current-project selection), `sessions` (summary column), `tasks` (priority, description, agent), `task_dependencies` (cycle-checked), `subtasks`, `activity_events` (digest-deduped), `artifacts`, `file_changes`, `terminal_commands`, `errors`, `error_resolutions`, `decisions`, `checkpoints`, `memories` (title, source agent/session, confidence, verification, status, tags, version, content digest, supersedes, expires), `memory_versions` (replacement audit trail), `memory_relationships`, `integrations`, `verification_results`. FTS5: `memories_fts` (content + title), `events_fts`.

## Migration policy
Migrations are **additive and in place** (`_migrate`): columns are added with `ALTER TABLE`, missing indexes created, and a pre-0.3.0 four-column `memories_fts` is rebuilt from the authoritative rows (FTS5 tables cannot be altered). Migration is serialized by an advisory `migration.lock` and every step re-checks state, so concurrent agent startup is safe. Existing records are never deleted or rewritten. `sqlite3 … 'PRAGMA integrity_check'` must be `ok` before and after any change; a pre-change safety copy is stored in `storage/backups/`.

## PostgreSQL
The next-generation backend (`backend/`) passes migration and API integration tests on an isolated database (see PROGRESS.md) but the live system stays on SQLite until a separately verified migration, backup, restore, and rollback path exists.
