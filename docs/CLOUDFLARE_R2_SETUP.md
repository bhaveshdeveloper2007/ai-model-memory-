# Cloudflare R2 setup guide

Everything is implemented. You only supply credentials and activate. **R2 is optional: local memory works completely without it, and credentials are never required for local operation.**

## 1. Create the bucket
1. Cloudflare Dashboard → R2 Object Storage → Create bucket.
2. Name it (e.g. `memoryos-backup`), pick a location, create.

## 2. Create scoped credentials
1. R2 → Manage API Tokens → Create API Token.
2. Scope: **Object Read & Write** for the single bucket only.
3. Copy the Access Key ID, Secret Access Key, and Account ID (shown once).

## 3. Enter credentials safely
```bash
cp ~/Developer/ai-model-memory-/.env.example ~/Developer/ai-model-memory-/.env
chmod 600 ~/Developer/ai-model-memory-/.env   # never committed (gitignored)
# edit .env: set MEMORYOS_R2_ENABLED=true, ACCOUNT_ID, ACCESS_KEY_ID,
# SECRET_ACCESS_KEY, BUCKET; leave ENDPOINT empty (auto-derived from account)
```
Export the same variables in the shell/agent environment, or `set -a; . .env; set +a` before running memoryos commands.

## 4. Connection test
```bash
python3 ~/Documents/MemoryOS/engine/memoryos.py storage test
```
Real credentials → talks to R2 (reads bucket listing). Without credentials it reports disabled.

## 5. Dry-run sync
```bash
python3 ~/Documents/MemoryOS/engine/memoryos.py storage sync          # dry-run default
```
Shows the plan: which objects upload, which are unchanged, any conflicts. Nothing is written.

## 6. First encrypted upload
```bash
python3 ~/Documents/MemoryOS/engine/memoryos.py storage sync --execute
```
Uploads only the include list (encrypted `.mos` snapshots, project context projections, checkpoint files) to `memoryos/…` under your bucket. Each object is sealed with `MEMORYOS-R2-AESGCM-1` envelope (client-side AES-256-GCM; key auto-created in your macOS Keychain under `com.memoryos.r2.<bucket-hash>` — never uploaded, never logged).

## 7. Verify
```bash
python3 ~/Documents/MemoryOS/engine/memoryos.py storage list
python3 ~/Documents/MemoryOS/engine/memoryos.py storage verify
python3 ~/Documents/MemoryOS/engine/memoryos.py storage status
```

## 8. Download / restore a test object
```bash
python3 ~/Documents/MemoryOS/engine/memoryos.py storage download projects/global/checkpoints/<name>.json.enc
# plaintext restored to ~/Documents/MemoryOS/storage/restored/
```

## Safety properties (implemented and tested)
- Upload-only sync: never deletes local data, never deletes remote objects.
- Conflicts preserve **both** versions (remote copy kept under a `.conflict-<hash>` key; local re-uploads).
- Retry with exponential backoff (3 attempts) on transient failures.
- Dry-run performs no crypto work and creates no keys.
- Integrity check via SHA-256 digests; tampered ciphertext is rejected on decrypt.
- Envelope versioning (`MEMORYOS-R2-AESGCM-1` + algorithm byte) is distinct from the `.mos` backup format.
- **Live R2 connectivity: BLOCKED — awaiting user Cloudflare credentials and bucket configuration.** Every code path is mock-tested; no live test has run.
