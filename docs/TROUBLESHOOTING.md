# Troubleshooting

If Codex does not show Memory OS tools, restart Codex and run `codex mcp list`. If the dashboard is unavailable, start `scripts/dashboard.sh` and open http://127.0.0.1:8765. Check `storage/journal/events.jsonl` and `storage/database/memoryos.sqlite3`. Hook failures are intentionally non-blocking. Restore the timestamped `~/.codex/config.toml.*.memoryos` and `hooks.json.*.memoryos` files to roll back.
