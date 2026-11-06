# Project memory

Projects are identified by Git remote (shared across clones) or path hash; each clone is a workspace. Every memory, task, decision, error, and session belongs to a project scope — cross-project retrieval is impossible by construction.

- `get_current_project` resolves an agent's explicit selection first, then the working directory.
- `get_project_context` returns bounded ranked context: incomplete tasks, checkpoint, recent activity, top memories, decisions, open errors.
- `get_project_health` reports staleness, progress, open errors, verified solutions, and unverified claims.
- `build_project_context` writes human-readable projections under `~/Documents/MemoryOS/projects/<project-id>/` (overview, current-state, important-context, decisions, tasks, errors, solutions, sessions, checkpoints) — atomically, with a configurable budget.
