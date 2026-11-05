# Encrypted backups and recovery

Memory OS can keep an encrypted backup in a **private GitHub repository**. It uses a separate `memoryos-backups` branch; the code branch remains free of database files. Backups use AES-256-GCM through Apple's CryptoKit. The encryption key is kept in macOS Keychain; GitHub receives only authenticated ciphertext. The app checks that the configured repository is private before every upload and stops if it is public.

## Enable backups

Authenticate GitHub CLI (`gh auth login`), then run:

```bash
~/Documents/MemoryOS/scripts/enable-backups.sh OWNER/PRIVATE-REPOSITORY
```

This verifies repository privacy and access, creates the first encrypted snapshot, then installs a user-level LaunchAgent to make a snapshot at login and every 24 hours. Backups are not configured by default in the public project installer. Each user must provide their own private repository so their data cannot flow to the project author's repository.

The recovery key is stored at `~/Documents/MemoryOS/storage/backups/recovery-key-OWNER_PRIVATE-REPOSITORY.txt` with owner-only permissions. **Copy this key to a separate safe place.** It is needed if the Mac's Keychain is lost. Never commit or upload the recovery key.

## What is backed up

An encrypted snapshot contains the complete Memory OS SQLite database (including projects, sessions, tasks, decisions, errors, verification results and search indexes), the append-only event journal, the Memory OS backup configuration, checkpoints and stored artifacts. The backup refuses to prune local history if any journal entry cannot be ingested. It does not contain full source repositories, passwords, private keys, raw prompts, tool output, or private chain-of-thought; the current Codex adapter intentionally captures event metadata rather than conversation text. Snapshots are full recovery points. The backup branch retains prior snapshots.

After a remote snapshot is uploaded and its commit is confirmed, Memory OS trims local activity-event rows older than 30 days and removes only the journal bytes included in that verified snapshot. Tasks, decisions, errors, memories and checkpoints stay local. If upload fails, local history is left in place.

## Restore after a crash

On a new Mac, install Memory OS, authenticate GitHub CLI, copy the recovery key to the path above, configure the private repository, and restore before enabling the daily job:

```bash
python3 ~/Documents/MemoryOS/scripts/backup.py configure OWNER/PRIVATE-REPOSITORY
python3 ~/Documents/MemoryOS/scripts/backup.py restore-latest
~/Documents/MemoryOS/scripts/enable-backups.sh OWNER/PRIVATE-REPOSITORY
```

Close Codex and the dashboard before restoring. Restore downloads the encrypted snapshots, authenticates/decrypts them locally, merges all durable records from prior snapshots (using the newest version of updated tasks), keeps a local pre-restore database copy, and replaces the database only after an integrity check. No decrypted snapshot is written to the GitHub checkout.

If automatic backup logs show an error, inspect `~/Documents/MemoryOS/logs/backup-error.log`. The job never uploads to a repository unless GitHub reports it as private.
