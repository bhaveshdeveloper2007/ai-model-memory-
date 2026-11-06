# Known issues

- The installed runtime still uses SQLite. The new PostgreSQL service is not a replacement until migration and recovery parity are demonstrated.
- Claude Code integration has not been configured or verified on this machine.
- Full Xcode is not detected; SwiftUI application compilation and XCTest require an Xcode-enabled environment.
- Semantic retrieval requires a local embedding provider. pgvector schema alone does not create embeddings.
- The initial local API token model is single-user. Per-agent credentials and granular permissions remain a later security task.
- The existing backup job is for the SQLite edition. PostgreSQL backup scheduling and restore validation remain unimplemented until the new schema is stable.
- The running PostgreSQL service is 15.19. Homebrew pgvector 0.8.6 has extension control files and binaries for PostgreSQL 17/18, not 15. Alembic correctly stops before creating tables because `CREATE EXTENSION vector` fails. Installing/building an extension for PostgreSQL 15 modifies Homebrew files outside this workspace and needs user approval.
- Ollama 0.40.13 is installed but not running and no local embedding model was confirmed. Full-text search remains the fallback; no embedding model was downloaded.
