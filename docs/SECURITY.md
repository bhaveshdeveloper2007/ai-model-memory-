# Security

- **Local-only services.** The engine's HTTP dashboard binds 127.0.0.1; MCP is stdio. R2 endpoints must be https.
- **Secret redaction before persistence.** API keys, GitHub/AWS/Slack tokens, private-key blocks, credential assignments, and bearer tokens are replaced with `[REDACTED:<kind>]` markers in memories, tasks, decisions, errors, solutions, and event payloads; redaction kinds are recorded without values. Never store credentials at all — redaction is a safety net.
- **Credentials never displayed.** `redacted_config()` masks R2 keys; the dashboard shows configuration presence, not values. `.env` is gitignored; only `.env.example` is committed.
- **R2 encryption.** Client-side AES-256-GCM via the macOS Keychain-backed helper; versioned envelope; tampered ciphertext rejected; keys never uploaded.
- **Project isolation.** All retrieval is project-scoped; `get_memory` validates project membership; cross-project consolidation refused; session identity is agent+project scoped and cross-use refused.
- **Untrusted content.** Memory content is inert data: stored verbatim, never interpreted as instructions or executed.
- **Path safety.** Storage object keys reject traversal; project projections are confined to the Memory OS projects directory; database paths reject symlinks.
- **Concurrency.** Schema migration serialized by an advisory file lock with re-checks; project registration race-safe (`ON CONFLICT`); WAL + busy timeout for readers/writers.
- **Permissions.** Database and journal 0600, directories 0700, configs 0600; backup uploads refuse public repositories; audit trail via append-only events.
