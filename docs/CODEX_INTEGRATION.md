# Codex integration

The global `memory_os` MCP server uses Python stdio. It offers project bootstrap, search, task, error, decision, checkpoint, verification and project listing tools. Lifecycle hooks are merged into the existing hooks file for SessionStart, UserPromptSubmit, PostToolUse, SessionEnd and Stop. The hook stores event name, session and limited path/tool metadata; it does not store prompt text, command text or tool output. Restart Codex after the configuration change.
