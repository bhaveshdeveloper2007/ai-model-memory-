# PostgreSQL generation lifecycle

An accepted event is size-checked, project-authorized, redacted, assigned an idempotency digest, and committed to PostgreSQL before a success response is returned. Memories are explicit inputs with a category, source, confidence, verification flag, importance, project scope, and optional source event. Agent-generated memories are unverified unless independently verified.

Creating or updating a memory commits a Markdown projection job to `sync_outbox` in the same transaction. A bounded worker claims jobs with `FOR UPDATE SKIP LOCKED`, writes a private frontmatter Markdown file atomically, and marks the job complete. Retries are idempotent. Safe deletion marks the memory deleted so retrieval excludes it, then removes its Markdown projection; hard database erasure is not performed by this initial API.

Full-text retrieval is always available. Optional embeddings come from a loopback-only Ollama endpoint and are stored in a model-versioned pgvector table. Missing model/runtime falls back to full-text search. No remote embedding service is used.

---

# Memory lifecycle

Hook events are synchronously appended and fsynced to `storage/journal/events.jsonl`, then ingested into SQLite. Duplicate event digests are ignored. The journal is retained; automatic archival/retention is not yet enabled. Retrieval uses project-filtered SQLite and FTS5 search. Semantic embeddings and automatic summarization are not configured.
