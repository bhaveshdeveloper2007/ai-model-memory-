# PostgreSQL backend schema (next generation)

The Alembic migration under `backend/migrations/versions/` defines normalized PostgreSQL tables with UUID keys, project-scoped foreign keys, timestamps, check constraints, and indexes.

| Table | Purpose |
|---|---|
| `projects` | Stable project identity, repository/root, and granted agent IDs |
| `agents` | Agent kind, capabilities, and one-way credential digest |
| `agent_sessions` | Session status and JSON recovery state |
| `events` | Append-only idempotent observed activity and event provenance |
| `tasks`, `task_dependencies` | Work items, lifecycle state, and project-local dependency edges |
| `file_changes` | Observed path changes and evidence metadata |
| `memories`, `memory_embeddings`, `memory_relationships` | Durable project memory, versioned 384-dimension vectors, and relationships |
| `decisions` | Architectural and implementation decisions |
| `errors`, `error_resolutions` | Sanitized failures and evidence-backed fixes |
| `checkpoints` | Resumable state references |
| `agent_leases` | Expiring advisory leases, scoped by project/resource |
| `audit_logs` | Successful security-sensitive operations without tokens or raw prompts |
| `sync_outbox` | Transactional jobs for Markdown projection |

Project isolation is enforced by API authorization and every query's `project_id` filter. The schema also constrains dependency and memory relationship edges to one project. Embeddings use a model name and model version; changing models requires a deliberate reindex.

The local development migration is generated but cannot yet be applied on the current PostgreSQL 15 service: the installed Homebrew pgvector build targets PostgreSQL 17/18. See [TEST_RESULTS.md](TEST_RESULTS.md).

---

# Database schema

SQLite file: `storage/database/memoryos.sqlite3`. Tables include projects, workspaces, agents, sessions, tasks, subtasks, activity_events, artifacts, file_changes, terminal_commands, errors, error_resolutions, decisions, checkpoints, memories, memory_relationships, integrations and verification_results. FTS5 virtual tables index memories and events. WAL is enabled.
