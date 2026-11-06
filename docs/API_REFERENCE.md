# PostgreSQL backend API (next generation)

The API is versioned under `/v1`, binds to loopback, and requires `Authorization: Bearer <token>` on all data endpoints. `GET /health` is unauthenticated and returns only backend/database status and whether the pgvector extension is available. Each agent token is stored as a SHA-256 digest and can access only projects where the owner granted membership. The owner token can administer projects and agents.

## Main routes

| Method | Path | Purpose |
|---|---|---|
| `POST`, `GET` | `/v1/projects` | Create a project (owner) or list accessible projects |
| `GET` | `/v1/projects/{id}` | Read a project if the caller has access |
| `POST` | `/v1/agents` | Create an agent and return its token once (owner only) |
| `PUT`, `DELETE` | `/v1/projects/{project_id}/agents/{agent_id}` | Grant or revoke project access (owner only) |
| `POST` | `/v1/sessions` | Start/reuse a session |
| `GET` | `/v1/sessions/{session_id}/resume` | Rebuild useful project context from stored state |
| `POST` | `/v1/events` | Persist an idempotent event; acknowledgement follows database commit |
| `GET` | `/v1/projects/{id}/events` | Read recent events for one project |
| `POST`, `GET` | `/v1/memories` | Add and list durable project memory |
| `GET` | `/v1/memories/{id}` | Read one accessible memory |
| `PATCH`, `DELETE` | `/v1/memories/{id}` | Correct/archive or soft-delete a memory |
| `GET` | `/v1/projects/{id}/memories/search?q=...` | Project-scoped full-text and optional local vector search |
| `POST` | `/v1/tasks` | Create a task |
| `GET` | `/v1/projects/{id}/tasks` | List tasks in one project |
| `PATCH` | `/v1/tasks/{task_id}` | Update the task status |
| `POST`, `GET` | `/v1/tasks/{task_id}/dependencies` | Manage task dependencies; cycles and cross-project edges are rejected |
| `POST`, `DELETE` | `/v1/leases` | Acquire/release advisory project-scoped leases |
| `POST` | `/v1/leases/heartbeat` | Extend an active lease owned by the caller |
| `POST` | `/v1/decisions`, `/v1/errors`, `/v1/checkpoints` | Record decisions, sanitized errors, and resumable state |

The API enforces a 1 MiB request body limit. Duplicate event IDs with identical content return a duplicate acknowledgement; reusing an ID with different content returns `409`. Sensitive event keys and common credential formats are redacted before storage. Project filters are applied in SQL and membership is checked before each scoped read or write.

PostgreSQL English full-text search is always available after migration. Results include source and verification metadata, an explanation, and ranking based on full-text match, importance, confidence, verification, and recency. When a loopback Ollama model is configured, matching model-version embeddings in pgvector are included. If the local model is unavailable, the request falls back to full-text results. OpenAPI schema is served at `/docs`.

---

# API reference

The MCP server provides the 16 `memory_*` tools listed in Codex. A loopback HTTP API exposes GET `/health`, `/projects`, `/summary?cwd=...`, `/search?q=...` and POST `/` with a JSON activity event. Start with `scripts/dashboard.sh`. The API is local and unauthenticated; keep it bound to loopback.
