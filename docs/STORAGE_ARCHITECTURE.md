# Storage architecture

Local-first: the SQLite database and journal are authoritative; the filesystem holds checkpoints, encrypted snapshots, project context projections; Cloudflare R2 is an optional encrypted off-site copy.

## Layers
- **LocalDirectoryAdapter / R2Adapter** behind one `StorageAdapter` interface (`engine/memoryos_r2.py`): upload, download, head, list, delete, connectivity test. R2 uses the S3-compatible API with SigV4 signing implemented in stdlib (`hmac`/`hashlib`/`urllib`) — no third-party SDK, verified against an independent reference implementation in tests.
- **Encryption envelope**: `MEMORYOS-R2-AESGCM-1\n` + algorithm byte + AES-256-GCM ciphertext from the existing Keychain-backed crypto helper. Nonce reuse impossible (CryptoKit GCM seals); authentication verified on decrypt; envelope version byte allows future formats.
- **Sync engine**: additive-only (upload new/changed encrypted objects), idempotent (unchanged digests skipped), dry-run without side effects, exponential-backoff retries, conflict detection preserving both versions, explicit include list (snapshots, context projections, checkpoints — never databases, journals, `.env`, credentials).
- **Local retention**: `verify_storage_integrity` reports health plus recommendations; nothing is ever deleted automatically.

## Object layout (remote)
```
memoryos/
├── projects/global/backup-snapshots/<ts>.mos.enc
├── projects/global/checkpoints/<id>.json.enc
└── projects/<project-id>/context|decisions|tasks|.../<file>.md.enc
```
