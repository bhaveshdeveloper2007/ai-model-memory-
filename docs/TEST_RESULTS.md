# Test results

Only actual command output is recorded here. Do not treat a design review or an unrun test as passing evidence.

## Environment inspection — 2026-11-06

- macOS 27.2, Apple Silicon arm64.
- Swift 6.4.2 and Command Line Tools are present. `xcodebuild` was not available, so a full SwiftUI build is not currently possible.
- Homebrew 7.0.12; Python 3.14.7.
- PostgreSQL 15.19 is running locally on port 5432; pgvector 0.8.6 is installed.
- Existing databases were listed for collision avoidance; no database was created, altered, or dropped during inventory.
- About 213 GiB disk free; 16 GiB RAM.

## PostgreSQL migration attempt — 2026-11-06

- A new development-only database named `memoryos_dev` was created after confirming that name was unused. No pre-existing database was changed.
- Python dependencies were installed in the repository's ignored `backend/.venv`; no system-wide Python packages were installed.
- `alembic upgrade head` stopped at `CREATE EXTENSION vector`: PostgreSQL 15 could not find `vector.control`. Homebrew's installed pgvector files are built for PostgreSQL 17/18. Transactional DDL rolled back; the new database contains only Alembic's version table and no Memory OS application tables.
- No API integration tests have run yet because the database migration is blocked. The test suite is pinned to a separate database named `memoryos_test` and will refuse to run against another name.

## Phase 1 and vertical slice

- Python 3.14 `compileall` passed for backend source, migration source, scripts, tests, and MCP adapter.
- 11 new non-database backend unit tests passed across project authorization helpers, secret redaction, local embedding adapter behavior, vault file projection permissions, MCP handshake/tool catalog, and loopback URL enforcement.
- 10 existing SQLite engine and backup regression tests passed using isolated temporary directories, including snapshot archiving, journal safety, retention, and restore merging. No installed database was read or modified. Combined Python checks: 21 passed.
- `create_app()` imported successfully and registered 32 FastAPI routes.
- SQLAlchemy compiled all 17 declared PostgreSQL tables into 28 DDL statements using a PostgreSQL mock engine; the schema includes all required entities.
- Shell syntax checks passed for setup, API, workers, MCP, test, dashboard, and macOS app scripts.
- Swift source syntax parsing passed with `swiftc -frontend -parse`.
- `git diff --check` passed with no whitespace errors. A source scan found only deliberately fake credential strings in redaction tests; no personal home-directory paths were found in project files.
- Full API integration tests were not run. The initial Alembic migration reached `CREATE EXTENSION vector` and failed because the active PostgreSQL 15 service cannot find a compatible pgvector extension. The migration transaction rolled back. The integration test script now checks this prerequisite and stops before tests.
- Full SwiftUI compilation and XCTest were not run. `xcodebuild` reports that the active `/Library/Developer/CommandLineTools` directory is Command Line Tools and requires full Xcode.
- Codex CLI 0.166.0-alpha.6 was inspected with `codex mcp --help`, `codex mcp add --help`, and `codex mcp list`; existing user MCP configuration was not changed. Claude Code CLI is not installed.
