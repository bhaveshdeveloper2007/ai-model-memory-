# Architecture

Memory OS is a local Python service with no third-party runtime dependencies. Codex connects over stdio MCP; user-level lifecycle hooks record metadata. A loopback-only dashboard reads the same SQLite database and JSONL journal. Git remotes identify related clones as one project, while each checkout is a workspace. Non-Git folders are identified by normalized local path.

SQLite runs in WAL mode and separates projects by project ID. The journal is append-only and fsyncs each record. FTS5 supports local search. There is no cloud embedding provider or semantic index enabled.
