---
name: memory-os
description: Use the local Memory OS shared memory system correctly — find the current project, load its context, save durable memories without duplicates, record decisions, tasks, errors and solutions, and recover after interruption.
version: 1.0.0
---

# Memory OS skill

Memory OS is a local-first shared memory system for AI agents. This skill
teaches you how to use it through its MCP tools. It applies to any agent
(Codex, Claude Code, or another MCP client) connected to the same engine.

## 1. Start of every session

1. Call `get_current_project` (with your `cwd`). It resolves your explicit
   selection first, then detects the project from the working directory.
2. Call `start_session` with your agent name. It registers your session and
   records which project you are working in.
3. Call `get_project_context` to load: incomplete tasks, the latest
   checkpoint, recent activity, high-importance context memories, recent
   decisions, and open errors.
4. Work from that context. Never ask the user to repeat information that is
   already stored — search first.

## 2. Search before you ask or write

Call `memory_search` (or `search_memory`) with keywords before asking the
user anything the project may already know. Search is project-scoped: you
only see memories for the current project plus global records you are
authorized to read.

## 3. When to save a memory

Use `remember` for durable, attributable facts:

- A meaningful architectural decision and its rationale (or use
  `record_decision` for formal decisions).
- A completed feature or milestone.
- A bug and its verified fix (record the error with `memory_record_error`,
  then attach the fix with `record_solution`, marking `verified: true` only
  when evidence — a passing command, a test run, a file diff — supports it).
- A change to project structure or dependencies.
- An incomplete task that must survive the session (`create_task` /
  `memory_create_task`).
- A stable user preference (`category: user_preference`).
- The session outcome (`end_session` with a short `summary` — it is stored
  as a session-summary memory automatically).

Set `verification` honestly: `proposed` for your idea, `observed` for
something you directly saw, `verified` only with real evidence. A proposed
solution must never be recorded as verified.

## 4. What not to save

Never store: passwords, API keys, tokens, private keys, recovery keys,
credentials of any kind (Memory OS redacts obvious secret patterns, but do
not rely on that), private chain-of-thought, raw prompt dumps, or bulk tool
output. Record concise outcomes and facts instead.

## 5. Avoid duplicates

`remember` is idempotent per project + category + content. If you need to
correct or extend an existing memory, call `update_memory` (it keeps the
previous state as a version) rather than writing a near-copy. When a memory
is genuinely obsolete and replaced by a new one, pass its ID as `supersedes`
to `remember` — the old record is marked superseded, never silently erased.

## 6. Record work honestly

- Update task status with `update_task` as work progresses.
- Record meaningful operations with `record_agent_activity` (operation,
  target, result, status).
- Never claim a task is done without evidence; verification comes from
  actual command results, tests, or file changes — not from intention.

## 7. Respect project isolation

You see only the current project's memory. If you believe you need another
project's context, ask the user; do not try to reach across projects.
`switch_project` changes your explicit selection only when the user directs
it. Different agents share the same project memory by design — Claude Code
and Codex see the same authorized records.

## 8. Recovering after interruption

At session start, `get_project_context` and `restore_checkpoint` reconstruct
where things stopped: the latest checkpoint, incomplete tasks, and recent
events. Compare with the actual Git working tree before trusting saved
state; the working tree is the source of truth for code.

## 9. If tools fail

Memory OS MCP failures are never a reason to stop working. Continue the
task normally and report that memory tools were unavailable. Do not store
memory through any channel other than the MCP tools.

## 10. Quick reference

| Goal | Tool |
|---|---|
| Find your project | `get_current_project` |
| Begin a session | `start_session` |
| Load context | `get_project_context` |
| Find knowledge | `memory_search` / `search_memory` |
| Save durable fact | `remember` |
| Correct a fact | `update_memory` |
| Replace a fact | `remember` with `supersedes` |
| Link related facts | `link_memories` |
| Retire a fact | `archive_memory` |
| Inspect changes | `get_memory_history` |
| Formal decision | `record_decision` |
| Task | `create_task` / `update_task` / `list_tasks` |
| Progress overview | `get_project_progress` |
| Bug | `memory_record_error` → `record_solution` → `search_solutions` |
| Session end | `end_session` |
| Resume point | `memory_save_checkpoint` / `restore_checkpoint` |
| Your activity | `record_agent_activity` / `get_agent_activity` |
