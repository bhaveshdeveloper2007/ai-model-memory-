# Implementation plan

This plan is the source of truth for advancing the installed SQLite edition into the PostgreSQL/FastAPI/macOS architecture. Items are marked done only after evidence is recorded in `TEST_RESULTS.md`.

## Phase 0 — Environment and preservation

- [x] Inspect macOS, Apple Silicon, Swift toolchain, Homebrew, Python, PostgreSQL, pgvector, listeners, disk, RAM, Git settings, and existing source/runtime.
- [x] Confirm PostgreSQL 15 is running and Homebrew pgvector files are present but target PostgreSQL 17/18; leave pre-existing databases and services untouched.
- [x] Keep the installed SQLite database and encrypted backup configuration out of this source repository.
- [x] Record the migration boundary: build next generation alongside installed generation, then migrate only after restore parity.

## Phase 1 — Foundation

- [x] Add a Python package with FastAPI, SQLAlchemy, Alembic, PostgreSQL driver, configuration, structured logs, and typed health API.
- [ ] Create an isolated `memoryos_dev` PostgreSQL database and enable pgvector only inside it (new isolated DB exists; extension is unavailable to the active server).
- [x] Generate a reversible initial Alembic migration with project isolation, foreign keys, constraints, and indexes; applying it awaits a PostgreSQL 15-compatible pgvector extension.
- [x] Add localhost-only API authentication, project-scoped agent grants, and a health check that reports service/database status without exposing secrets.
- [x] Add a repeatable local development setup and migration command.
- [ ] Verify migration up/down/up and backend health against the isolated database.

## Phase 2 — Memory core and vertical slice

- [x] Implement idempotent durable event ingestion and project/session/agent registration.
- [x] Implement task, decision, error, checkpoint, and memory APIs with project-scoped queries.
- [x] Add sensitive-field filtering and preserve event-to-memory provenance.
- [x] Add PostgreSQL full-text search, a versioned pgvector schema, and optional local embedding support with graceful full-text fallback.
- [x] Add Markdown vault projection through a durable transactional outbox.
- [x] Add API integration tests for persistence, idempotency, project boundaries, redaction, search, and outbox recovery (execution awaits database migration).

## Phase 3 — Agent integration

- [x] Add a stdio MCP adapter for the new API and document Codex setup.
- [ ] Inspect current Codex and Claude Code versions and their official local integration interfaces.
- [ ] Generate optional integration templates without overwriting existing user settings.
- [ ] Verify explicit write/read sharing across two authorized agent identities.

## Phase 4 — Coordination and recovery

- [x] Add task dependencies, leases, heartbeats, conflict rejection, and handoff context.
- [ ] Add session recovery summaries and evidence-linked error resolutions.
- [ ] Add bounded, restartable workers with retries and dead-letter handling.
- [ ] Test lease expiry, worker interruption, and fresh-database restore.

## Phase 5 — User interfaces

- [x] Add a SwiftUI menu-bar companion source and optional browser dashboard using real API endpoints.
- [x] Add tasks, events, project selection, memory search, local-token settings, and connection status to the interface source.
- [ ] Add accessible empty/loading/offline/error states, keyboard navigation, and native dark mode.
- [ ] Document the notch integration API; integrate with an existing notch app only when separately authorized.
- [ ] Build and run XCTest/UI checks on a machine with full Xcode (this Mac currently has Command Line Tools only).

## Phase 6 — Reliability and security

- [ ] Add local PostgreSQL and vault backups, retention, integrity check, and isolated restore test.
- [ ] Add API/MCP authorization tests, secret redaction tests, project isolation tests, and deletion tests.
- [ ] Measure startup, idle CPU, memory, and local search latency; document measured values.
- [ ] Review graceful shutdown, retry limits, logs, deletion, and audit records.

## Phase 7 — Migration and release

- [ ] Implement a dry-run importer from the current SQLite schema into PostgreSQL.
- [ ] Compare all durable table counts and provenance in an isolated restore before enabling the new backend.
- [ ] Produce reproducible installer, agent templates, manual setup, rollback, and recovery instructions.
- [ ] Do not publish, change repository visibility, enable cloud sync, or replace the installed service without the required approval.

## Current scope

The immediate target is Phase 1 plus a tested Phase 2 vertical slice (project → durable event → memory → project-aware search → Markdown projection). A local Ollama embedding adapter is implemented but awaits a configured model and working pgvector extension. Advanced consolidation, full SwiftUI, Claude integration, and live migration remain later phases.
