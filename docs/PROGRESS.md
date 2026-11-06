# Progress

Machine-readable phase status is maintained in `progress.json` at the repository root. This file provides the human-readable checkpoint.

## Current checkpoint

- Current phase: Foundation and vertical slice.
- Working in: existing source checkout inside the authorized Codex workspace.
- Installed runtime: `~/Documents/MemoryOS`, first-generation SQLite edition; left in place.
- Host: Apple Silicon macOS 27.2; Swift 6.4.2 via Command Line Tools; Homebrew 7.0.12; Python 3.14.7; PostgreSQL 15.19. Homebrew pgvector 0.8.6 has extension binaries/control files for PostgreSQL 17/18, so PostgreSQL 15 cannot load it.
- PostgreSQL: localhost port 5432 is already used by the user's Homebrew service. Existing databases are present; none will be altered. The app will use its own database.
- Resources: 16 GiB RAM and about 213 GiB free disk at inspection.
- Current Git branch: `main`; source remote is `ai-model-memory-`. Local environment files, databases, vault contents, and backups are excluded from source control.
- Active issues: existing installations must continue using SQLite until the new backend has migration and restore parity; the PostgreSQL 15-compatible pgvector extension is missing and its installation would modify Homebrew outside the workspace.

## Completed evidence

- [x] Environment inventory collected without changing unrelated services.
- [x] Existing local and encrypted backup system identified as user data to preserve.
- [x] Python package, typed API models, project/agent authentication, project-scoped routes, task dependencies, advisory leases, event idempotency, redaction, memory search, and outbox worker source are implemented.
- [x] Alembic initial migration is generated; the schema compiles to PostgreSQL DDL.
- [x] MCP stdio adapter, optional local embedding provider, browser dashboard source, and SwiftUI menu-bar source are present.
- [x] 11 isolated unit tests, Python compile, shell syntax, Swift parsing, SQLAlchemy metadata compilation, and `git diff --check` passed.
- [ ] PostgreSQL migration and API integration tests (blocked by missing PostgreSQL 15-compatible pgvector extension; see TEST_RESULTS.md).
- [ ] Full SwiftUI build and XCTest (requires full Xcode).

Update this checkpoint after each implementation milestone and before ending interrupted work.
