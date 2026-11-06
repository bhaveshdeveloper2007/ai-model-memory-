# Architecture

## Current installed generation

The first-generation Memory OS is a local Python service without third-party runtime dependencies. Codex connects over stdio MCP; user-level lifecycle hooks record limited metadata. A loopback-only dashboard reads SQLite and the JSONL journal. Git remotes identify related clones as one project, while each checkout is a workspace. Non-Git folders are identified by normalized local path.

SQLite runs in WAL mode and separates projects by project ID. The journal is append-only and fsyncs each record. FTS5 supports local search. Encrypted GitHub backup is an optional, separately configured feature. The installed copy at `~/Documents/MemoryOS` remains the active system while the next generation is built and migrated.

## Target generation

The new backend under `backend/` uses FastAPI, SQLAlchemy, Alembic, PostgreSQL, and pgvector. It binds to loopback by default. PostgreSQL is the authoritative structured store for newly accepted events, tasks, sessions, memories, and audit records. Idempotent writes commit before an API acknowledgement is returned.

The Markdown vault is a readable projection of durable records. PostgreSQL's transactional outbox schedules vault updates, so a crash between database commit and file write can be recovered by a worker. The database remains authoritative for structured records; hand-authored Markdown is imported as a separately sourced note and never silently overwrites a database memory.

Memory retrieval starts with PostgreSQL full-text search and project filtering. The schema includes versioned pgvector embeddings. Semantic ranking stays disabled until a local embedding provider is configured; the service must work without downloading or calling a hosted model.

The existing SQLite service and its encrypted backup history are not modified as part of the backend transition. Migration of the installed user's records is a separate, explicit, integrity-checked operation after feature parity and restore tests.

## Runtime boundaries

- Source code, migrations, and tests belong in this repository.
- Runtime tokens, PostgreSQL databases, the Markdown vault, logs, and encrypted snapshots stay outside Git.
- PostgreSQL binds to the already-running local service; the application API binds to `127.0.0.1` only.
- The service does not expose PostgreSQL, MCP, or the API to a network interface.
- Agent supplied payloads are untrusted. Prompt text, chain-of-thought, credentials, and secret-bearing fields must not be captured.

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), [SECURITY.md](SECURITY.md), and [RECOVERY.md](RECOVERY.md) for operational details.
