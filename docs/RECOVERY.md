# Recovery status for the new backend

The PostgreSQL/FastAPI edition is built alongside the installed SQLite service. Do not point the new backend at the installed SQLite files or replace the active service before a migration is dry-run, record-count checked, and restored in an isolated test environment.

PostgreSQL backups must use a consistent database dump together with a vault snapshot. Restore must run integrity checks and preserve a pre-restore copy. The first-generation backup documented below remains the recovery path for the installed SQLite edition until a PostgreSQL backup implementation is verified.

# Recovery

At session start use `memory_bootstrap` with the current working directory. Inspect its incomplete tasks, checkpoint and recent events, then compare with the actual Git working tree before relying on saved state. Save a checkpoint before compaction or interruption. Only record verification after a command or other evidence actually supports it.
