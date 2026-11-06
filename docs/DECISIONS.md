# Decisions

## 2026-11-06 — Build PostgreSQL backend beside the installed SQLite edition

**Decision:** Keep the existing installed Memory OS and encrypted backup history unchanged while building the requested PostgreSQL/FastAPI implementation in this repository.

**Reason:** The installed app contains user data and working remote backups. Replacing it before schema/import/restore parity would create unnecessary data-loss risk. PostgreSQL is already installed and running, so an isolated application database can be used without replacing system services.

**Effect:** The new backend will use its own PostgreSQL database and data directory. Migration is a later, explicit phase with a dry run and full record comparison.

## 2026-11-06 — Structured records are authoritative; Markdown is a recoverable projection

**Decision:** PostgreSQL owns structured state. Markdown files are generated through a transactional outbox and remain readable outside Memory OS.

**Reason:** A durable database transaction must be acknowledged before projection work. An outbox lets the worker retry file writes after a crash without losing accepted records.

**Effect:** Filesystem edits will be imported as separately sourced notes; they will not silently overwrite structured records.

## 2026-11-06 — No hosted embedding dependency

**Decision:** The first retrieval implementation uses local PostgreSQL full-text search. pgvector columns are versioned, but semantic search is enabled only when a local embedding model is configured.

**Reason:** The system must work offline and privacy defaults must avoid sending memory to a cloud provider.

**Effect:** Do not describe semantic similarity as available until a local provider is installed and tested.
