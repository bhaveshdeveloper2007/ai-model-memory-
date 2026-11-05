# Memory lifecycle

Hook events are synchronously appended and fsynced to `storage/journal/events.jsonl`, then ingested into SQLite. Duplicate event digests are ignored. The journal is retained; automatic archival/retention is not yet enabled. Retrieval uses project-filtered SQLite and FTS5 search. Semantic embeddings and automatic summarization are not configured.
