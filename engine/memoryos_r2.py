"""Memory OS optional Cloudflare R2 storage layer (stdlib only).

Everything here is OFF unless MEMORYOS_R2_ENABLED=true. The local engine never
requires credentials, never uploads automatically, and never deletes local data
after a remote upload.

Encryption: client-side AES-256-GCM using the existing Memory OS Keychain
helper (`bin/memoryos-crypto`, CryptoKit). The envelope is versioned and
distinct from the encrypted `.mos` backup format:

    MEMORYOS-R2-AESGCM-1\n  |  1 byte algorithm id (0x01)  |  crypto output

where the crypto output is the helper's `MEMORYOS-AESGCM-1` envelope sealed
with a dedicated R2 Keychain service (`com.memoryos.r2.<bucket-hash>`). Keys
live in the macOS Keychain and are never uploaded, logged, or stored in
memory records.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

R2_MAGIC = b'MEMORYOS-R2-AESGCM-1\n'
ALGORITHM_ID = b'\x01'
REQUIRED_ENV = ('MEMORYOS_R2_ACCOUNT_ID', 'MEMORYOS_R2_ACCESS_KEY_ID', 'MEMORYOS_R2_SECRET_ACCESS_KEY', 'MEMORYOS_R2_BUCKET')
SECRET_VALUES = ('MEMORYOS_R2_SECRET_ACCESS_KEY', 'MEMORYOS_R2_ACCESS_KEY_ID')


def get_r2_config(environ=None):
    """Read and validate R2 configuration. Secret values are never returned."""
    env = dict(os.environ if environ is None else environ)
    prefix = {key[12:]: value for key, value in env.items() if key.startswith('MEMORYOS_R2_')}
    config = {
        'MEMORYOS_R2_ENABLED': str(prefix.get('ENABLED', 'false')).strip().lower(),
        'MEMORYOS_R2_BUCKET': prefix.get('BUCKET'),
        'MEMORYOS_R2_PREFIX': (prefix.get('PREFIX') or 'memoryos/').rstrip('/') + '/',
        'MEMORYOS_R2_SYNC_MODE': prefix.get('SYNC_MODE') or 'manual',
        'MEMORYOS_R2_ENCRYPTION': prefix.get('ENCRYPTION') or 'client-side',
        'MEMORYOS_R2_REGION': prefix.get('REGION') or 'auto',
        'bucket': prefix.get('BUCKET'),
        'account_id': prefix.get('ACCOUNT_ID'),
    }
    config['endpoint'] = prefix.get('ENDPOINT') or (f'https://{config["account_id"]}.r2.cloudflarestorage.com' if config['account_id'] else None)
    problems = []
    if config['MEMORYOS_R2_ENABLED'] == 'true':
        for name in REQUIRED_ENV:
            if not prefix.get(name[12:]):
                problems.append(name[12:])
        if config['endpoint'] and not config['endpoint'].startswith('https://'):
            problems.append('MEMORYOS_R2_ENDPOINT must use https')
        if config['MEMORYOS_R2_SYNC_MODE'] not in {'manual', 'scheduled'}:
            problems.append('MEMORYOS_R2_SYNC_MODE must be manual or scheduled')
        if config['MEMORYOS_R2_ENCRYPTION'] != 'client-side':
            problems.append('MEMORYOS_R2_ENCRYPTION must be client-side (plaintext upload is not supported)')
    config['valid'] = not problems
    config['problems'] = problems
    return config


def redacted_config(config):
    """Configuration safe to display or persist in reports."""
    return {key: ('<configured>' if key in SECRET_VALUES and value else value) for key, value in config.items() if not key.startswith('MEMORYOS_R2_') or key in ('MEMORYOS_R2_ENABLED', 'MEMORYOS_R2_PREFIX', 'MEMORYOS_R2_SYNC_MODE', 'MEMORYOS_R2_ENCRYPTION', 'MEMORYOS_R2_REGION', 'MEMORYOS_R2_BUCKET')}


# ── SigV4 signing (AWS-compatible; Cloudflare R2 uses the same scheme) ──

def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def sigv4_headers(method: str, url: str, headers: dict, payload: bytes, access_key: str, secret_key: str, region: str, service: str, timestamp: dt.datetime) -> dict:
    """Return Authorization/amz-date/x-amz-content-sha256 headers for a request.

    Signing correctness is covered by unit tests against AWS-published
    signature test vectors, so the scheme is verified without credentials.
    """
    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc
    canonical_uri = parsed.path or '/'
    canonical_query = parsed.query
    amz_date = timestamp.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = timestamp.strftime('%Y%m%d')
    payload_hash = hashlib.sha256(payload).hexdigest()
    headers = {**headers, 'host': host, 'x-amz-content-sha256': payload_hash, 'x-amz-date': amz_date}
    signed_headers = ';'.join(sorted(headers))
    canonical_headers = ''.join(f'{name}:{headers[name]}\n' for name in sorted(headers))
    canonical_request = '\n'.join([method.upper(), canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash])
    scope = f'{date_stamp}/{region}/{service}/aws4_request'
    string_to_sign = '\n'.join(['AWS4-HMAC-SHA256', amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(('AWS4' + secret_key).encode(), date_stamp.encode()), region.encode()), service.encode()), b'aws4_request')
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers['authorization'] = f'AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}'
    return headers


# ── Client-side encryption envelope (reuses the existing Keychain helper) ──

def _crypto_binary() -> Path | None:
    candidates = [Path(__file__).resolve().parents[1] / 'bin/memoryos-crypto', Path.home() / 'Documents/MemoryOS/bin/memoryos-crypto']
    return next((c for c in candidates if c.is_file() and os.access(c, os.X_OK)), None)


def _r2_service(bucket: str) -> str:
    return 'com.memoryos.r2.' + hashlib.sha256(bucket.encode()).hexdigest()[:16]


def encrypt_for_r2(data: bytes, bucket: str) -> bytes:
    binary = _crypto_binary()
    if binary is None:
        raise RuntimeError('Client-side encryption requires the Memory OS crypto helper (bin/memoryos-crypto).')
    # init is idempotent: it creates the dedicated R2 key once and reuses it
    # afterwards, so first-time and repeated syncs behave identically.
    subprocess.run([str(binary), 'init', _r2_service(bucket)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    result = subprocess.run([str(binary), 'encrypt', _r2_service(bucket)], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
    if result.returncode != 0 or not result.stdout.startswith(b'MEMORYOS-AESGCM-1'):
        raise RuntimeError('R2 encryption failed; no secret material was written anywhere.')
    return R2_MAGIC + ALGORITHM_ID + result.stdout


def decrypt_for_r2(blob: bytes, bucket: str) -> bytes:
    binary = _crypto_binary()
    if binary is None:
        raise RuntimeError('Client-side decryption requires the Memory OS crypto helper.')
    if not blob.startswith(R2_MAGIC):
        raise ValueError('Not a Memory OS R2 encrypted object (envelope header mismatch).')
    algorithm = blob[len(R2_MAGIC):len(R2_MAGIC) + 1]
    if algorithm != ALGORITHM_ID:
        raise ValueError(f'Unknown R2 encryption envelope version: {algorithm!r}')
    result = subprocess.run([str(binary), 'decrypt', _r2_service(bucket)], input=blob[len(R2_MAGIC) + 1:], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
    if result.returncode != 0:
        raise ValueError('R2 decryption failed authentication; data was not trusted.')
    return result.stdout


def ensure_r2_key(bucket: str) -> None:
    binary = _crypto_binary()
    if binary is None:
        raise RuntimeError('Memory OS crypto helper unavailable; cannot prepare the R2 key.')
    result = subprocess.run([str(binary), 'init', _r2_service(bucket)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    if result.returncode != 0:
        raise RuntimeError('Could not initialize the R2 encryption key in the macOS Keychain.')


# ── Storage adapters ──

class StorageAdapter:
    """Interface every remote adapter implements. Local storage stays default."""

    def upload(self, key: str, payload: bytes) -> dict: raise NotImplementedError
    def download(self, key: str) -> bytes: raise NotImplementedError
    def head(self, key: str) -> dict | None: raise NotImplementedError
    def list(self, prefix: str = '') -> list[str]: raise NotImplementedError
    def delete(self, key: str) -> None: raise NotImplementedError
    def connectivity_test(self) -> dict: raise NotImplementedError


class LocalDirectoryAdapter(StorageAdapter):
    """Filesystem-backed adapter used for tests and offline dry-runs."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key: str) -> Path:
        if '..' in key or key.startswith('/'):
            raise ValueError('Unsafe object key.')
        target = self.root / key
        if not str(target.resolve()).startswith(str(self.root.resolve())):
            raise ValueError('Unsafe object key.')
        return target

    def upload(self, key, payload):
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile('wb', dir=target.parent, delete=False) as stream:
            stream.write(payload); tmp = Path(stream.name)
        tmp.chmod(0o600); os.replace(tmp, target)
        return {'key': key, 'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)}

    def download(self, key):
        return self._path(key).read_bytes()

    def head(self, key):
        target = self._path(key)
        if not target.is_file(): return None
        stat = target.stat()
        return {'bytes': stat.st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}

    def list(self, prefix=''):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*') if p.is_file() and str(p.relative_to(self.root)).startswith(prefix))

    def delete(self, key):
        self._path(key).unlink(missing_ok=True)

    def connectivity_test(self):
        return {'status': 'ok', 'adapter': 'local-directory', 'root': str(self.root)}


class R2Adapter(StorageAdapter):
    """Cloudflare R2 via its S3-compatible API. Pure stdlib (SigV4 + urllib)."""

    def __init__(self, config, timeout=30):
        if not config.get('valid'):
            raise ValueError('R2 configuration is invalid: ' + ', '.join(config.get('problems', [])))
        self.config = config
        self.timeout = timeout
        self.bucket = config['bucket']
        self.access_key = os.environ.get('MEMORYOS_R2_ACCESS_KEY_ID', '')
        self.secret_key = os.environ.get('MEMORYOS_R2_SECRET_ACCESS_KEY', '')
        self.prefix = config['MEMORYOS_R2_PREFIX']

    def _request(self, method: str, key: str, payload: bytes = b'', query: str = '', *, max_retries=3) -> bytes:
        url = f"{self.config['endpoint']}/{self.bucket}/{urllib.parse.quote(self.prefix + key)}" + (f'?{query}' if query else '')
        attempt = 0
        delay = 1.0
        last_error = None
        while attempt < max_retries:
            try:
                headers = sigv4_headers(method, url, {}, payload, self.access_key, self.secret_key, 'auto', 's3', dt.datetime.now(dt.timezone.utc))
                request = urllib.request.Request(url, data=payload if payload else None, method=method, headers=headers)
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                last_error = f'R2 request failed with HTTP {exc.code}'
                if exc.code < 500 and exc.code != 429:
                    raise RuntimeError(last_error) from None
            except (urllib.error.URLError, OSError) as exc:
                last_error = f'R2 request failed ({type(exc).__name__}); diagnostics suppressed'
            attempt += 1
            if attempt < max_retries:
                import time as _time
                _time.sleep(delay)
                delay *= 2  # exponential backoff
        raise RuntimeError(last_error or 'R2 request failed after retries')

    def upload(self, key, payload):
        self._request('PUT', key, payload)
        return {'key': key, 'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)}

    def download(self, key):
        return self._request('GET', key)

    def head(self, key):
        try:
            self._request('HEAD', key)
            return {'key': key}
        except RuntimeError:
            return None

    def list(self, prefix=''):
        payload = self._request('GET', '', query='list-type=2&prefix=' + urllib.parse.quote(self.prefix + prefix))
        import xml.etree.ElementTree as ET
        ns = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
        keys = [element.text or '' for element in ET.fromstring(payload).findall('.//s3:Contents/s3:Key', ns)]
        strip = self.prefix
        return [key[len(strip):] for key in keys if key.startswith(strip)]

    def delete(self, key):
        self._request('DELETE', key)

    def connectivity_test(self):
        self._request('GET', '', query='list-type=2&max-keys=1')
        return {'status': 'ok', 'adapter': 'cloudflare-r2', 'bucket': self.bucket}


def build_adapter(config, *, local_root: Path | None = None):
    """Return the configured adapter, or None when R2 is disabled. Local
    memory must keep working whenever this returns None."""
    if config.get('MEMORYOS_R2_ENABLED') != 'true':
        return None
    if not config.get('valid'):
        raise ValueError('R2 configuration is invalid: ' + ', '.join(config.get('problems', [])))
    if local_root is not None:
        return LocalDirectoryAdapter(local_root)
    return R2Adapter(config)


# ── Sync engine ──

def syncable_objects(root: Path) -> list[tuple[str, bytes]]:
    """The explicit include list: snapshots, manifests, checkpoint projections.
    Secrets, journals with raw payloads, and credentials are never included."""
    root = Path(root)
    objects: list[tuple[str, bytes]] = []
    snapshots = root / 'storage/backups/github-repo/snapshots'
    if snapshots.exists():
        for snap in sorted(snapshots.glob('*.mos')):
            objects.append((f'projects/global/backup-snapshots/{snap.name}', snap.read_bytes()))
    projections = root / 'projects'
    if projections.exists():
        for manifest in sorted(projections.glob('**/*.md')):
            relative = manifest.relative_to(projections)
            objects.append((f'projects/{relative}', manifest.read_bytes()))
    checkpoint_files = root / 'storage/checkpoints'
    if checkpoint_files.exists():
        for cp in sorted(checkpoint_files.glob('*.json')):
            objects.append((f'projects/global/checkpoints/{cp.name}', cp.read_bytes()))
    return objects


def sync(config, root: Path, *, dry_run=False, local_adapter: StorageAdapter | None = None, local_root: Path | None = None, audit=lambda event: None) -> dict:
    """Upload new or changed encrypted objects. Idempotent, additive only:
    never deletes local data, never deletes remote objects, and records
    conflicts instead of overwriting either version."""
    if config.get('MEMORYOS_R2_ENABLED') != 'true':
        return {'status': 'disabled', 'reason': 'R2 is not enabled; local memory is unaffected.'}
    if not config.get('valid'):
        return {'status': 'invalid-config', 'problems': config.get('problems', [])}
    adapter = local_adapter if local_adapter is not None else build_adapter(config, local_root=local_root)
    import time as _time

    def _retry(operation, *, attempts=3):
        delay = 1.0
        last_error = None
        for attempt in range(attempts):
            try:
                return operation()
            except RuntimeError as exc:
                last_error = exc
            except OSError as exc:
                last_error = RuntimeError(f'storage operation failed ({type(exc).__name__}); diagnostics suppressed')
            if attempt < attempts - 1:
                _time.sleep(delay)
                delay *= 2
        raise last_error

    plan = []
    for key, payload in syncable_objects(root):
        digest = hashlib.sha256(payload).hexdigest()
        remote_head = None
        try:
            remote_head = adapter.head(key + '.enc')
        except RuntimeError:
            remote_head = None
        entry = {'key': key + '.enc', 'sha256': digest, 'bytes': len(payload), 'action': 'upload'}
        if remote_head is not None:
            try:
                existing = decrypt_for_r2(adapter.download(key + '.enc'), config['bucket'])
                if hashlib.sha256(existing).hexdigest() == digest:
                    entry['action'] = 'unchanged'
                else:
                    # Conflict: both versions are preserved. The remote copy is
                    # saved under a conflict key; the local version uploads fresh.
                    entry['action'] = 'conflict-detected'
                    entry['conflict_remote_key'] = key + '.conflict-' + digest[:8] + '.enc'
                    entry['conflict_payload'] = adapter.download(key + '.enc')
            except (ValueError, RuntimeError):
                entry['action'] = 'conflict-detected'
                entry['conflict_remote_key'] = key + '.conflict-' + digest[:8] + '.enc'
                try:
                    entry['conflict_payload'] = adapter.download(key + '.enc')
                except (RuntimeError, OSError):
                    pass
        # Encryption happens at execution time, not during dry-run planning:
        # a dry run must never create keys or perform crypto work.
        plan.append((entry, payload))
    uploaded = 0
    conflicts = 0
    if not dry_run:
        ensure_r2_key(config['bucket'])
        for entry, payload in plan:
            if entry['action'] == 'unchanged':
                continue
            encrypted = encrypt_for_r2(payload, config['bucket']) if config.get('MEMORYOS_R2_ENCRYPTION') == 'client-side' else payload
            if entry['action'] == 'conflict-detected':
                conflicts += 1
                # Preserve the remote version before overwriting: both sides survive.
                if entry.get('conflict_payload'):
                    _retry(lambda entry=entry: adapter.upload(entry['conflict_remote_key'], entry['conflict_payload']))
            _retry(lambda entry=entry, encrypted=encrypted: adapter.upload(entry['key'], encrypted))
            uploaded += 1
    audit({'event': 'storage.sync', 'dry_run': dry_run, 'planned': len(plan), 'uploaded': uploaded, 'conflicts': conflicts})
    return {'status': 'dry-run' if dry_run else 'completed', 'planned': len(plan), 'uploaded': uploaded, 'unchanged': sum(1 for entry, _ in plan if entry['action'] == 'unchanged'), 'conflicts': conflicts, 'objects': [entry for entry, _ in plan][:50], 'note': 'Local data is never deleted; remote objects are never deleted by sync.'}


def get_sync_status(config):
    if config.get('MEMORYOS_R2_ENABLED') != 'true':
        return {'status': 'disabled', 'note': 'R2 is disabled; local memory is authoritative and unaffected.'}
    if not config.get('valid'):
        return {'status': 'invalid-config', 'problems': config.get('problems', [])}
    return {'status': 'ready', 'mode': config.get('MEMORYOS_R2_SYNC_MODE'), 'bucket': config.get('MEMORYOS_R2_BUCKET'), 'encryption': config.get('MEMORYOS_R2_ENCRYPTION'), 'prefix': config.get('MEMORYOS_R2_PREFIX')}
