# MCP tools (61 in v0.4)

Transport: stdio JSON-RPC (`memoryos.py mcp`). Registered for Codex as `memory_os`; register for Claude Code with `integrations/claude-code/configure.sh`.

## Project
`list_projects` (memory_list_projects), `get_current_project`, `register_project`, `switch_project`, `get_project_context`, `get_project_health`, `build_project_context`
## Memory
`remember` (idempotent, redacted, versioned), `search_memory` (memory_search), `get_memory`, `update_memory`, `get_memory_history`, `link_memories`, `archive_memory`, `verify_memory`, `consolidate_memories`, `find_duplicate_memories`, `list_memories`
## Tasks
`create_task` (memory_create_task), `update_task` (memory_update_task), `list_tasks`, `complete_task` (evidence required), `add_task_dependency`, `get_project_progress`, `memory_verify_task`
## Errors & solutions
`record_error` (memory_record_error), `record_solution`, `search_solutions`
## Sessions
`start_session`, `end_session` (summary + auto-checkpoint), `get_recent_sessions`, `get_session_summary`, `recover_interrupted_sessions`, `create_checkpoint` (memory_save_checkpoint), `restore_checkpoint`
## Agent activity
`record_agent_activity`, `get_agent_activity`, `get_agent_history`
## Storage
`get_storage_status`, `get_backup_status`, `get_sync_status`, `request_sync` (dry-run default), `verify_storage_integrity`
## Legacy aliases
`memory_bootstrap`, `memory_get_project*`, `memory_get_recent_activity`, `memory_get_next_action`, `memory_compare_project_state` — all preserved for existing agent instructions.
