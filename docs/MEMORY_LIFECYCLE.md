# Memory lifecycle (v0.4)

## Capture
Memories enter through `remember` (or domain tools that write memories: decisions, errors, solutions, session summaries). Content is redacted before persistence (API keys, GitHub/AWS/Slack tokens, private key blocks, credential assignments, bearer tokens). Capture is **idempotent per project + category + content digest** — repeating the same fact never duplicates it.

## Classification
Categories: `GLOBAL_USER_PREFERENCE`, `PROJECT_CONTEXT`, `ARCHITECTURE`, `DECISION`, `TASK`, `PROGRESS`, `ERROR`, `VERIFIED_SOLUTION`, `ENVIRONMENT`, `DEPENDENCY`, `RESEARCH`, `SESSION_SUMMARY`, `REFERENCE`, `CHECKPOINT`. Legacy lowercase names (`user_preference`, `project_context`, …) are accepted and mapped to canonical values. Records carry ID, scope, project, title, content, source agent/session, timestamps, importance, confidence, verification, version, tags, related IDs, and superseded-by.

## Importance scoring
`importance_score()` combines, transparently: category base weight, verification status (+0.15 verified), explicit importance (×0.25), confidence (×0.1), and inbound references (+0.05 each, capped). Superseded/rejected records take a −0.2 penalty. **Scores rank retrieval; they never authorize deletion.**

## Duplicate detection
`find_duplicate_memories` is read-only: exact duplicates (same content digest) and near-duplicates (token Jaccard similarity ≥ configurable threshold, compared within project+category only). Detection never modifies records.

## Consolidation
`consolidate_memories` merges a secondary memory into a primary: the primary gains a new version containing both contents; the secondary is marked superseded and linked via `consolidated_into`. Every original ID and version stays retrievable. Cross-project merges are refused.

## Verification
States: `PROPOSED` → `OBSERVED` → `VERIFIED` (requires evidence: passing test, command result, reproduction check, or human confirmation — `verify_memory` refuses without it), plus `SUPERSEDED`, `REJECTED`, `ARCHIVED`. Agent claims never verify anything alone.

## Versioning
`update_memory` and consolidation write the previous state to `memory_versions` with actor and timestamp, then bump `version`. `get_memory_history` returns the replacement audit trail plus relationships.

## Session lifecycle
`start_session` registers agent + project and selects the current project; `end_session` stores the summary as a session-summary memory, ends the row, and (when state is supplied) writes an auto-checkpoint. `recover_interrupted_sessions` marks stale active sessions as `interrupted` — the next agent treats them as incomplete work with their checkpoints intact.
