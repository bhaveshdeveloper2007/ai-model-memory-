"""MEMORY OS v0.4 storage tests: R2 configuration, encryption envelope,
SigV4 signing, sync engine with a local mock adapter, dry-run, conflict
detection, retry behavior, and disabled-mode isolation.

No real Cloudflare credentials are used or required. Live R2 connectivity is
BLOCKED — Awaiting user Cloudflare credentials and bucket configuration.
"""
import datetime as dt
import hashlib
import hmac
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT / 'engine')
import sys
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)
import memoryos_r2 as r2

spec = importlib.util.spec_from_file_location('memoryos', ROOT / 'engine/memoryos.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

CRYPTO_AVAILABLE = r2._crypto_binary() is not None


def base_environ(**overrides):
    env = {
        'MEMORYOS_R2_ENABLED': 'true',
        'MEMORYOS_R2_ACCOUNT_ID': 'testaccount123',
        'MEMORYOS_R2_ACCESS_KEY_ID': 'test-access-key',
        'MEMORYOS_R2_SECRET_ACCESS_KEY': 'test-secret-key',
        'MEMORYOS_R2_BUCKET': 'test-bucket',
        'MEMORYOS_R2_ENDPOINT': 'https://testaccount123.r2.cloudflarestorage.com',
        'MEMORYOS_R2_REGION': 'auto',
        'MEMORYOS_R2_PREFIX': 'memoryos/',
        'MEMORYOS_R2_SYNC_MODE': 'manual',
        'MEMORYOS_R2_ENCRYPTION': 'client-side',
    }
    env.update(overrides)
    return env


class TestR2Configuration(unittest.TestCase):
    def test_disabled_by_default_and_valid_when_empty(self):
        config = r2.get_r2_config(environ={})
        self.assertEqual(config['MEMORYOS_R2_ENABLED'], 'false')
        self.assertTrue(config['valid'])
        self.assertEqual(r2.get_sync_status(config)['status'], 'disabled')

    def test_enabled_without_required_fields_is_invalid(self):
        config = r2.get_r2_config(environ={'MEMORYOS_R2_ENABLED': 'true'})
        self.assertFalse(config['valid'])
        self.assertIn('ACCOUNT_ID', ' '.join(config['problems']))

    def test_endpoint_must_be_https(self):
        config = r2.get_r2_config(environ=base_environ(MEMORYOS_R2_ENDPOINT='http://insecure.example'))
        self.assertFalse(config['valid'])

    def test_plaintext_upload_never_allowed(self):
        config = r2.get_r2_config(environ=base_environ(MEMORYOS_R2_ENCRYPTION='none'))
        self.assertFalse(config['valid'])

    def test_redacted_config_never_contains_secrets(self):
        config = r2.get_r2_config(environ=base_environ())
        redacted = json.dumps(r2.redacted_config(config))
        self.assertNotIn('test-secret-key', redacted)
        self.assertNotIn('test-access-key', redacted)
        self.assertIn('test-bucket', redacted)


class TestSigV4Signing(unittest.TestCase):
    """Verify the signer against an independently written reference
    implementation of the AWS SigV4 specification, plus structural checks."""

    def reference_signature(self, method, url, headers, payload, access_key, secret, region, service, timestamp):
        import urllib.parse
        parsed = urllib.parse.urlsplit(url)
        amz_date = timestamp.strftime('%Y%m%dT%H%M%SZ')
        date_stamp = timestamp.strftime('%Y%m%d')
        payload_hash = hashlib.sha256(payload).hexdigest()
        all_headers = {**headers, 'host': parsed.netloc, 'x-amz-content-sha256': payload_hash, 'x-amz-date': amz_date}
        signed = ';'.join(sorted(all_headers))
        canonical = '\n'.join([
            method.upper(), parsed.path or '/', parsed.query,
            ''.join(f'{name}:{all_headers[name]}\n' for name in sorted(all_headers)),
            signed, payload_hash])
        scope = f'{date_stamp}/{region}/{service}/aws4_request'
        sts = '\n'.join(['AWS4-HMAC-SHA256', amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        key = ('AWS4' + secret).encode()
        for part in (date_stamp, region, service, 'aws4_request'):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        return hmac.new(key, sts.encode(), hashlib.sha256).hexdigest()

    def test_signature_matches_reference_implementation(self):
        url = 'https://testaccount123.r2.cloudflarestorage.com/test-bucket/memoryos/projects/global/manifest.json'
        timestamp = dt.datetime(2026, 11, 7, 12, 0, 0, tzinfo=dt.timezone.utc)
        headers = r2.sigv4_headers('PUT', url, {}, b'payload-bytes', 'AKID', 'SECRET', 'auto', 's3', timestamp)
        expected = self.reference_signature('PUT', url, {}, b'payload-bytes', 'AKID', 'SECRET', 'auto', 's3', timestamp)
        self.assertIn('AWS4-HMAC-SHA256', headers['authorization'])
        self.assertIn(f'Credential=AKID/20261107/auto/s3/aws4_request', headers['authorization'])
        self.assertIn(f'Signature={expected}', headers['authorization'])
        self.assertEqual(headers['x-amz-date'], '20261107T120000Z')
        self.assertEqual(headers['x-amz-content-sha256'], hashlib.sha256(b'payload-bytes').hexdigest())

    def test_different_payload_changes_signature(self):
        url = 'https://example.r2.cloudflarestorage.com/bucket/key'
        ts = dt.datetime.now(dt.timezone.utc)
        first = r2.sigv4_headers('GET', url, {}, b'', 'AKID', 'SECRET', 'auto', 's3', ts)['authorization']
        second = r2.sigv4_headers('GET', url, {}, b'data', 'AKID', 'SECRET', 'auto', 's3', ts)['authorization']
        self.assertNotEqual(first, second)


@unittest.skipUnless(CRYPTO_AVAILABLE, 'Memory OS crypto helper not available')
class TestEncryptionEnvelope(unittest.TestCase):
    def test_round_trip(self):
        payload = b'Memory OS secret content ' * 100
        blob = r2.encrypt_for_r2(payload, 'roundtrip-bucket')
        self.assertTrue(blob.startswith(r2.R2_MAGIC))
        self.assertEqual(blob[len(r2.R2_MAGIC):len(r2.R2_MAGIC) + 1], r2.ALGORITHM_ID)
        self.assertNotIn(payload, blob)
        self.assertEqual(r2.decrypt_for_r2(blob, 'roundtrip-bucket'), payload)

    def test_tampered_ciphertext_is_rejected(self):
        blob = bytearray(r2.encrypt_for_r2(b'tamper probe', 'tamper-bucket'))
        blob[-5] ^= 0xFF
        with self.assertRaises(ValueError):
            r2.decrypt_for_r2(bytes(blob), 'tamper-bucket')

    def test_wrong_envelope_version_rejected(self):
        blob = r2.encrypt_for_r2(b'x', 'version-bucket')
        blob = blob[:len(r2.R2_MAGIC)] + b'\x09' + blob[len(r2.R2_MAGIC) + 1:]
        with self.assertRaisesRegex(ValueError, 'envelope version'):
            r2.decrypt_for_r2(blob, 'version-bucket')

    def test_non_r2_object_rejected(self):
        with self.assertRaisesRegex(ValueError, 'header mismatch'):
            r2.decrypt_for_r2(b'plain data', 'any-bucket')


class TestSyncEngine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.root = root
        self.old = (m.DB, m.JOURNAL, m.JOURNAL_LOCK, m.ROOT)
        m.DB = root / 'db.sqlite3'
        m.JOURNAL = root / 'journal' / 'events.jsonl'
        m.JOURNAL_LOCK = root / 'journal' / 'events.lock'
        m.ROOT = root
        self.local_bucket = root / 'local-bucket'
        self.adapter = r2.LocalDirectoryAdapter(self.local_bucket)
        (root / 'storage/backups/github-repo/snapshots').mkdir(parents=True)
        (root / 'storage/backups/github-repo/snapshots/20261107T000000Z.mos').write_bytes(b'encrypted-snapshot-bytes')
        (root / 'storage/checkpoints').mkdir(parents=True)
        (root / 'storage/checkpoints/cp1.json').write_text('{"step": 1}')
        self.config = r2.get_r2_config(environ=base_environ())

    def tearDown(self):
        m.DB, m.JOURNAL, m.JOURNAL_LOCK, m.ROOT = self.old
        self._tmp.cleanup()

    def _sync(self, dry_run=True, adapter=None):
        return r2.sync(self.config, self.root, dry_run=dry_run, local_adapter=adapter or self.adapter)

    def test_disabled_r2_never_touches_local_memory(self):
        config = r2.get_r2_config(environ={'MEMORYOS_R2_ENABLED': 'false'})
        result = r2.sync(config, self.root, dry_run=True, local_adapter=self.adapter)
        self.assertEqual(result['status'], 'disabled')
        self.assertFalse(self.local_bucket.exists() and any(self.local_bucket.iterdir()))

    @unittest.skipUnless(CRYPTO_AVAILABLE, 'Memory OS crypto helper not available')
    def test_dry_run_uploads_nothing(self):
        result = self._sync(dry_run=True)
        self.assertEqual(result['status'], 'dry-run')
        self.assertEqual(result['uploaded'], 0)
        self.assertEqual(self.adapter.list(), [])

    @unittest.skipUnless(CRYPTO_AVAILABLE, 'Memory OS crypto helper not available')
    def test_sync_encrypts_uploads_and_is_idempotent(self):
        first = self._sync(dry_run=False)
        self.assertEqual(first['status'], 'completed')
        self.assertGreater(first['uploaded'], 0)
        keys = self.adapter.list()
        self.assertTrue(all(key.endswith('.enc') for key in keys))
        # Remote objects are ciphertext: the plaintext is not stored remotely.
        blob = self.adapter.download(keys[0])
        self.assertTrue(blob.startswith(r2.R2_MAGIC))
        self.assertNotIn(b'encrypted-snapshot-bytes', blob)
        # Second sync: everything unchanged, nothing re-uploaded.
        second = self._sync(dry_run=False)
        self.assertEqual(second['uploaded'], 0)
        self.assertEqual(second['unchanged'], first['planned'])

    @unittest.skipUnless(CRYPTO_AVAILABLE, 'Memory OS crypto helper not available')
    def test_changed_local_content_upload_and_no_local_deletion(self):
        self._sync(dry_run=False)
        snapshot = self.root / 'storage/backups/github-repo/snapshots/20261107T000000Z.mos'
        snapshot.write_bytes(b'new snapshot content')
        result = self._sync(dry_run=False)
        self.assertEqual(result['uploaded'], 1)
        # Local file still present: sync never deletes local data.
        self.assertEqual(snapshot.read_bytes(), b'new snapshot content')

    def test_mock_network_failures_exhaust_retries_and_report(self):
        class FailingAdapter(r2.LocalDirectoryAdapter):
            calls = 0
            def upload(self, key, payload):
                FailingAdapter.calls += 1
                raise RuntimeError('R2 request failed (ConnectionError); diagnostics suppressed')
        failing = FailingAdapter(self.local_bucket)
        with unittest.mock.patch.object(r2, '_crypto_binary', return_value=Path('/bin/true')) if False else unittest.mock.patch('builtins.open', unittest.mock.mock_open(read_data='')) if False else _noop():
            pass  # encryption path guarded by skipUnless below
        # Force plaintext mode via a config the sync accepts by patching encryption check off:
        config = dict(self.config)
        with unittest.mock.patch.object(r2, 'encrypt_for_r2', side_effect=lambda data, bucket: data), \
             unittest.mock.patch.object(r2, 'ensure_r2_key', side_effect=lambda bucket: None):
            with self.assertRaises(RuntimeError):
                r2.sync(config, self.root, dry_run=False, local_adapter=failing)
        self.assertGreaterEqual(FailingAdapter.calls, 3)  # retried with backoff before raising

    @unittest.skipUnless(CRYPTO_AVAILABLE, 'Memory OS crypto helper not available')
    def test_remote_conflict_preserves_both_versions(self):
        self._sync(dry_run=False)
        # Change local content, and also make the remote object unreadable/corrupt
        # to force the conflict branch (decryption failure).
        keys = self.adapter.list()
        target = keys[0]
        corrupt = self.adapter.download(target)[:-8] + b'XXXXXXXX'
        self.adapter.upload(target, corrupt)
        snapshot = self.root / 'storage/backups/github-repo/snapshots/20261107T000000Z.mos'
        snapshot.write_bytes(b'locally changed snapshot')
        result = self._sync(dry_run=False)
        self.assertGreaterEqual(result['conflicts'], 1)
        listed = self.adapter.list()
        self.assertTrue(any('.conflict-' in key for key in listed), listed)  # remote version preserved
        self.assertTrue(any(key == target for key in listed))  # and the refreshed local version
        self.assertEqual(snapshot.read_bytes(), b'locally changed snapshot')  # local authoritative, never deleted

    def test_object_naming_follows_layout(self):
        objects = r2.syncable_objects(self.root)
        keys = [key for key, _ in objects]
        self.assertTrue(any(key.startswith('projects/global/backup-snapshots/') for key in keys))
        self.assertTrue(any(key.startswith('projects/global/checkpoints/') for key in keys))

    def test_syncable_objects_exclude_secrets_and_databases(self):
        (self.root / 'config').mkdir(parents=True, exist_ok=True)
        (self.root / 'config/backup.json').write_text('{"repository":"x/y"}')
        (self.root / 'storage').joinpath('database').mkdir(parents=True, exist_ok=True)
        (self.root / 'storage/database/memoryos.sqlite3').write_bytes(b'sqlite bytes')
        (self.root / '.env').write_text('MEMORYOS_R2_SECRET_ACCESS_KEY=leaked')
        keys = [key for key, _ in r2.syncable_objects(self.root)]
        self.assertFalse(any('sqlite' in key or '.env' in key or 'backup.json' in key for key in keys))


class TestSyncStatusTool(unittest.TestCase):
    def test_engine_sync_tools_report_disabled_by_default(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.old = (m.DB, m.JOURNAL, m.JOURNAL_LOCK, m.ROOT)
        m.DB = root / 'db.sqlite3'
        m.JOURNAL = root / 'journal' / 'events.jsonl'
        m.JOURNAL_LOCK = root / 'journal' / 'events.lock'
        m.ROOT = root
        try:
            result = m.call('request_sync', {'dry_run': True})
            self.assertEqual(result['status'], 'disabled')
            status = m.call('get_sync_status', {})
            self.assertEqual(status['status'], 'disabled')
        finally:
            m.DB, m.JOURNAL, m.JOURNAL_LOCK, m.ROOT = self.old
            self._tmp.cleanup()


class _Noop:
    def __enter__(self): return self
    def __exit__(self, *args): return False


def _noop():
    return _Noop()


import unittest.mock  # noqa: E402

if __name__ == '__main__':
    unittest.main()
