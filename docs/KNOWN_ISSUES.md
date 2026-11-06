# Known issues

- The installed runtime still uses SQLite as its authoritative store. The new PostgreSQL backend now migrates and passes API integration tests, but SQLite remains live until the migration path (record-count and relationship parity) is implemented.
- **Backup uploads are blocked: the configured backup repository `bhaveshdeveloper2007/ai-model-database` is PUBLIC (checked 2026-11-07).** Existing snapshots are AES-256-GCM encrypted, so ciphertext exposure does not disclose memory contents, and no new uploads were made. Restore private visibility (requires your GitHub account; not done automatically) and re-run `scripts/enable-backups.sh OWNER/REPOSITORY` to resume backups.
- Claude Code is not installed on this machine, so its integration is provided (`integrations/claude-code/configure.sh`) but runtime verification is pending.
- Full Xcode is not installed on this machine (Xcode 27.2 was removed; package receipts remain). The SwiftUI menu-bar companion and XCTest require an Xcode-enabled environment.
- Semantic retrieval requires a local embedding provider. pgvector 0.8.7 is now installed and loads on PostgreSQL 15; embeddings still require a running local model provider and remain optional.
- The initial local API token model is single-user. Per-agent credentials and granular permissions remain a later security task.
- The live SQLite edition's backup job refuses public repositories by design and therefore pauses (rather than uploading) while the backup repository is public.
- Ollama is installed but not running; no local embedding model was downloaded. Full-text search is the active retrieval path.
