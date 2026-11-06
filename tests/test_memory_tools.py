"""Tests for the extended Memory OS engine: memory CRUD with versioning,
secret redaction, idempotent capture, project isolation, sessions,
checkpoints, agent activity, MCP protocol, and additive schema migration.

Every test runs against an isolated temporary database. The user's installed
Memory OS database is never touched.
"""
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('memoryos', ROOT / 'engine/memoryos.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.root = root
        self.old = (m.DB, m.JOURNAL, m.JOURNAL_LOCK, m.ROOT)
        m.DB = root / 'db.sqlite3'
        m.JOURNAL = root / 'journal' / 'events.jsonl'
        m.JOURNAL_LOCK = root / 'journal' / 'events.lock'
        m.ROOT = root
        (root / 'projA').mkdir()
        (root / 'projB').mkdir()

    def tearDown(self):
        m.DB, m.JOURNAL, m.JOURNAL_LOCK, m.ROOT = self.old
        self._tmp.cleanup()

    def test_remember_redacts_secrets_and_records_kind(self):
        result = m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'project_context', 'content': 'key sk-abcdef1234567890abcdef12 embedded'})
        self.assertIn('[REDACTED:api_key]', result['memory']['content'])
        self.assertIn('api_key', result['redaction_kinds'])

    def test_remember_rejects_unknown_category_and_verification(self):
        with self.assertRaises(ValueError):
            m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'gossip', 'content': 'x'})
        with self.assertRaises(ValueError):
            m.call('remember', {'cwd': str(self.root / 'projA'), 'verification': 'definitely-true', 'content': 'x'})

    def test_remember_is_idempotent_per_content_digest(self):
        first = m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'project_context', 'content': 'Use WAL mode.'})
        second = m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'project_context', 'content': 'Use   WAL mode.'})
        self.assertFalse(first['deduplicated'])
        self.assertTrue(second['deduplicated'])
        self.assertEqual(first['memory']['id'], second['memory']['id'])
        with m.connect() as db:
            count = db.execute("SELECT count(*) FROM memories WHERE category IN ('project_context','PROJECT_CONTEXT') AND project_id=?", (first['memory']['project_id'],)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_update_memory_versions_previous_state(self):
        first = m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'architecture', 'content': 'v1 design'})
        updated = m.call('update_memory', {'memory_id': first['memory']['id'], 'content': 'v2 design', 'verification': 'verified', 'agent': 'codex'})
        self.assertEqual(updated['memory']['version'], 2)
        self.assertEqual(updated['previous_version'], 1)
        history = m.call('get_memory_history', {'memory_id': first['memory']['id']})
        # The version table stores every superseded state; the current state
        # lives on the memory row itself (current_version reflects it).
        self.assertEqual(history['current_version'], 2)
        self.assertEqual(len(history['versions']), 1)
        self.assertEqual(history['versions'][0]['version'], 1)
        self.assertEqual(history['versions'][0]['content'], 'v1 design')

    def test_supersede_marks_prior_memory_and_keeps_link(self):
        first = m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'decision', 'content': 'Choice A'})
        second = m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'decision', 'content': 'Choice B supersedes', 'supersedes': first['memory']['id']})
        self.assertEqual(second['superseded'], first['memory']['id'])
        with m.connect() as db:
            prior = dict(db.execute('SELECT status, verification FROM memories WHERE id=?', (first['memory']['id'],)).fetchone())
        self.assertEqual(prior['status'], 'superseded')
        self.assertEqual(prior['verification'], 'superseded')

    def test_link_memories_is_idempotent(self):
        a = m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'error', 'content': 'crash on launch'})
        b = m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'solution', 'content': 'fixed path handling'})
        first = m.call('link_memories', {'from_memory': a['memory']['id'], 'to_memory': b['memory']['id'], 'relation': 'resolved_by'})
        second = m.call('link_memories', {'from_memory': a['memory']['id'], 'to_memory': b['memory']['id'], 'relation': 'resolved_by'})
        self.assertEqual(first['relationship_id'], second['relationship_id'])

    def test_archive_and_history(self):
        mem = m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'research_finding', 'content': 'temporary finding'})
        m.call('archive_memory', {'memory_id': mem['memory']['id'], 'agent': 'codex'})
        listed = m.call('list_memories', {'cwd': str(self.root / 'projA')})
        self.assertEqual([item['id'] for item in listed if item['id'] == mem['memory']['id']], [])

    def test_project_isolation_for_memories_and_context(self):
        m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'project_context', 'content': 'Project A secret architecture note'})
        m.call('remember', {'cwd': str(self.root / 'projB'), 'category': 'project_context', 'content': 'Project B private context'})
        context_a = m.call('get_project_context', {'cwd': str(self.root / 'projA')})
        self.assertTrue(all('Project B private context' not in (item['content'] or '') for item in context_a['context_memories']))
        listed_a = m.call('list_memories', {'cwd': str(self.root / 'projA')})
        self.assertTrue(all('Project B private context' not in (item['content'] or '') for item in listed_a))

    def test_session_lifecycle_summary_and_restore_checkpoint(self):
        session = m.call('start_session', {'cwd': str(self.root / 'projA'), 'agent': 'codex', 'session_id': 'sess-1'})
        m.call('memory_save_checkpoint', {'cwd': str(self.root / 'projA'), 'summary': 'before refactor', 'state': {'step': 3}, 'session_id': session['session_id'], 'agent': 'codex'})
        ended = m.call('end_session', {'cwd': str(self.root / 'projA'), 'session_id': session['session_id'], 'summary': 'Completed the refactor phase.'})
        self.assertTrue(ended['ended'])
        self.assertIsNotNone(ended['summary_memory'])
        sessions = m.call('get_recent_sessions', {'cwd': str(self.root / 'projA')})
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]['status'], 'ended')
        restored = m.call('restore_checkpoint', {'cwd': str(self.root / 'projA')})
        self.assertEqual(restored['state'], {'step': 3})

    def test_session_identity_is_refused_across_agents_and_projects(self):
        session = m.call('start_session', {'cwd': str(self.root / 'projA'), 'agent': 'codex', 'session_id': 'shared'})
        with self.assertRaises(ValueError):
            m.call('memory_record_event', {'cwd': str(self.root / 'projA'), 'session_id': session['session_id'], 'event_type': 'probe', 'agent': 'claude-code'})
        with self.assertRaises(ValueError):
            m.call('memory_record_event', {'cwd': str(self.root / 'projA'), 'session_id': session['session_id'], 'event_type': 'probe', 'agent': 'codex', 'cwd': str(self.root / 'projB')})

    def test_end_session_inherits_owner_agent(self):
        session = m.call('start_session', {'cwd': str(self.root / 'projA'), 'agent': 'codex', 'session_id': 'owner'})
        ended = m.call('end_session', {'cwd': str(self.root / 'projA'), 'session_id': session['session_id'], 'summary': 'done'})
        self.assertTrue(ended['ended'])

    def test_solutions_record_search_and_resolve_error(self):
        error = m.call('memory_record_error', {'cwd': str(self.root / 'projA'), 'summary': 'build fails on clean checkout', 'agent': 'codex'})
        solution = m.call('record_solution', {'cwd': str(self.root / 'projA'), 'error_id': error['error_id'], 'resolution': 'run bootstrap script', 'verified': True})
        self.assertTrue(solution['verified'])
        found = m.call('search_solutions', {'query': 'clean checkout', 'cwd': str(self.root / 'projA')})
        self.assertTrue(found)
        progress = m.call('get_project_progress', {'cwd': str(self.root / 'projA')})
        self.assertEqual(progress['open_errors'], 0)

    def test_solution_requires_existing_error(self):
        with self.assertRaises(ValueError):
            m.call('record_solution', {'cwd': str(self.root / 'projA'), 'error_id': 'missing', 'resolution': 'x'})

    def test_agent_activity_round_trip(self):
        m.call('record_agent_activity', {'cwd': str(self.root / 'projA'), 'agent': 'codex', 'operation': 'tests.run', 'result': 'passed', 'status': 'completed'})
        rows = m.call('get_agent_activity', {'cwd': str(self.root / 'projA'), 'agent': 'codex'})
        self.assertTrue(any(row['event_type'] == 'agent.activity' and isinstance(row['payload'], dict) and row['payload'].get('operation') == 'tests.run' for row in rows))

    def test_current_project_explicit_selection_beats_detection(self):
        registered = m.call('register_project', {'path': str(self.root / 'projA'), 'name': 'Alpha'})
        switched = m.call('switch_project', {'agent': 'qa-agent', 'project_id': registered['project_id']})
        current = m.call('get_current_project', {'agent': 'qa-agent'})
        self.assertEqual(current['project_id'], switched['current_project'])
        self.assertEqual(current['source'], 'explicit_selection')
        self.assertEqual(current['name'], 'Alpha')

    def test_switch_project_rejects_unknown_project(self):
        with self.assertRaises(ValueError):
            m.call('switch_project', {'agent': 'qa-agent', 'project_id': 'does-not-exist'})

    def test_tasks_decisions_errors_redact_secrets(self):
        task = m.call('memory_create_task', {'cwd': str(self.root / 'projA'), 'title': 'rotate', 'details': 'password="hunter2hunter2"'})
        self.assertIn('credential_assignment', task['redaction_kinds'])
        decision = m.call('memory_record_decision', {'cwd': str(self.root / 'projA'), 'summary': 'use vault; token="abcdefabcdefabcdef"'})
        self.assertIn('credential_assignment', decision['redaction_kinds'])
        error = m.call('memory_record_error', {'cwd': str(self.root / 'projA'), 'summary': 'leak: ghp_' + 'a' * 36})
        self.assertIn('github_token', error['redaction_kinds'])
        with m.connect() as db:
            raw = db.execute('SELECT details FROM tasks WHERE id=?', (task['task_id'],)).fetchone()[0]
            self.assertNotIn('hunter2hunter2', raw)

    def test_additive_migration_preserves_existing_records(self):
        """A database created by the previous engine version must migrate in
        place: no data loss, no table recreation, new columns present."""
        legacy_dir = self.root / 'legacy'
        legacy_dir.mkdir()
        legacy_db = legacy_dir / 'old.sqlite3'
        old = sqlite3.connect(legacy_db)
        old.executescript('''
        CREATE TABLE projects(id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, name TEXT NOT NULL, root_path TEXT NOT NULL, remote_url TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE memories(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, category TEXT NOT NULL, content TEXT NOT NULL, importance REAL NOT NULL DEFAULT 0.5, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO projects VALUES ('p1', 'ident-1', 'Legacy', '/legacy', NULL, '2020-01-01', '2020-01-01');
        INSERT INTO memories VALUES ('m1', 'p1', 'project_context', 'legacy memory content', 0.5, '2020-01-01', '2020-01-01');
        ''')
        old.commit()
        old.close()
        original_db = m.DB
        m.DB = legacy_db
        try:
            with m.connect() as db:
                columns = {row[1] for row in db.execute('PRAGMA table_info(memories)')}
                self.assertIn('verification', columns)
                self.assertIn('content_digest', columns)
                row = dict(db.execute('SELECT * FROM memories WHERE id=?', ('m1',)).fetchone())
            self.assertEqual(row['content'], 'legacy memory content')
            self.assertEqual(row['status'], 'active')
            self.assertEqual(row['version'], 1)
        finally:
            m.DB = original_db

    def test_fts_index_rebuild_preserves_legacy_memories(self):
        """Databases created before the title column must rebuild their FTS
        index in place: search keeps working and no memory rows are lost."""
        legacy_dir = self.root / 'legacy-fts'
        legacy_dir.mkdir()
        legacy_db = legacy_dir / 'old.sqlite3'
        old = sqlite3.connect(legacy_db)
        old.executescript('''
        CREATE TABLE projects(id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, name TEXT NOT NULL, root_path TEXT NOT NULL, remote_url TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE memories(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, category TEXT NOT NULL, content TEXT NOT NULL, importance REAL NOT NULL DEFAULT 0.5, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE VIRTUAL TABLE memories_fts USING fts5(memory_id UNINDEXED, project_id UNINDEXED, content, category);
        INSERT INTO projects VALUES ('p1', 'ident-fts', 'Legacy', '/legacy', NULL, '2020-01-01', '2020-01-01');
        INSERT INTO memories VALUES ('m1', 'p1', 'project_context', 'legacy searchable content zz9q', 0.5, '2020-01-01', '2020-01-01');
        INSERT INTO memories_fts VALUES ('m1', 'p1', 'legacy searchable content zz9q', 'project_context');
        ''')
        old.commit()
        old.close()
        original_db = m.DB
        m.DB = legacy_db
        try:
            m.call('remember', {'cwd': str(legacy_dir), 'category': 'project_context', 'content': 'new memory after rebuild'})
            with m.connect() as db:
                columns = [row[1] for row in db.execute('PRAGMA table_info(memories_fts)')]
                count = db.execute('SELECT count(*) FROM memories').fetchone()[0]
            self.assertIn('title', columns)
            self.assertEqual(count, 2)
            found = m.call('memory_search', {'query': 'zz9q'})
            self.assertTrue(any('legacy searchable content' in (row.get('content') or '') for row in found['results']))
        finally:
            m.DB = original_db

    def test_dashboard_endpoints_are_loopback_json(self):
        import threading
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(('127.0.0.1', 0), m.Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            from urllib.request import urlopen
            import time as _time
            _time.sleep(0.1)
            health = json.loads(urlopen(f'http://127.0.0.1:{port}/health', timeout=5).read())
            self.assertIn(health['status'], {'ok', 'degraded'})
            self.assertEqual(health['integrity'], 'ok')
            projects = json.loads(urlopen(f'http://127.0.0.1:{port}/projects', timeout=5).read())
            self.assertIsInstance(projects, list)
            cwd = str(self.root / 'projA')
            memories = json.loads(urlopen(f'http://127.0.0.1:{port}/memories?cwd={cwd}', timeout=5).read())
            self.assertIn('memories', memories)
            progress = json.loads(urlopen(f'http://127.0.0.1:{port}/progress?cwd={cwd}', timeout=5).read())
            self.assertIn('total_tasks', progress)
        finally:
            server.shutdown()
            server.server_close()

    def test_memory_search_filters_archived(self):
        mem = m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'progress', 'content': 'searchable unique phrase zqxj'})
        m.call('update_memory', {'memory_id': mem['memory']['id'], 'status': 'archived'})
        results = m.call('memory_search', {'query': 'zqxj', 'cwd': str(self.root / 'projA')})
        self.assertFalse(any(row.get('id') == mem['memory']['id'] for row in results['results']))


class McpProtocolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.old = (m.DB, m.JOURNAL, m.JOURNAL_LOCK, m.ROOT, os.getcwd())
        m.DB = root / 'db.sqlite3'
        m.JOURNAL = root / 'journal' / 'events.jsonl'
        m.JOURNAL_LOCK = root / 'journal' / 'events.lock'
        m.ROOT = root
        (root / 'proj').mkdir()
        os.chdir(root / 'proj')

    def tearDown(self):
        os.chdir(self.old[4])
        m.DB, m.JOURNAL, m.JOURNAL_LOCK, m.ROOT = self.old[:4]
        self._tmp.cleanup()

    def _rpc(self, proc, payload):
        proc.stdin.write(json.dumps(payload) + '\n')
        proc.stdin.flush()
        line = proc.stdout.readline()
        return json.loads(line)

    def _start_mcp(self):
        import subprocess, sys
        env = dict(os.environ)
        env['MEMORYOS_DB'] = str(m.DB)
        env['MEMORYOS_JOURNAL'] = str(m.JOURNAL)
        return subprocess.Popen([sys.executable, str(ROOT / 'engine/memoryos.py'), 'mcp'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=str(ROOT), env=env)

    def test_mcp_round_trip_initialize_list_and_call(self):
        proc = self._start_mcp()
        try:
            init = self._rpc(proc, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}})
            self.assertEqual(init['result']['serverInfo']['name'], 'memory-os')
            self.assertEqual(init['result']['serverInfo']['version'], '0.3.0')
            tools = self._rpc(proc, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
            names = {tool['name'] for tool in tools['result']['tools']}
            required_tools = {
                'memory_bootstrap', 'memory_search', 'search_memory', 'memory_get_project', 'memory_get_recent_activity',
                'memory_record_event', 'memory_create_task', 'memory_update_task', 'memory_record_decision',
                'memory_record_error', 'memory_verify_task', 'memory_save_checkpoint', 'memory_list_projects',
                'remember', 'get_memory', 'update_memory', 'link_memories', 'archive_memory', 'get_memory_history',
                'list_memories', 'list_tasks', 'get_project_progress', 'record_solution', 'search_solutions',
                'start_session', 'end_session', 'get_recent_sessions', 'restore_checkpoint',
                'record_agent_activity', 'get_agent_activity', 'register_project', 'switch_project',
                'get_current_project', 'get_project_context',
            }
            self.assertTrue(required_tools <= names, sorted(required_tools - names))
            written = self._rpc(proc, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'remember', 'arguments': {'category': 'project_context', 'content': 'mcp captured note'}}})
            self.assertEqual(written['result']['content'][0]['type'], 'text')
            payload = json.loads(written['result']['content'][0]['text'])
            self.assertFalse(payload['deduplicated'])
            missing = self._rpc(proc, {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'remember', 'arguments': {}}})
            self.assertIn('error', missing)
        finally:
            proc.stdin.close()
            proc.wait(timeout=10)

    def test_legacy_tool_names_still_work(self):
        proc = self._start_mcp()
        try:
            self._rpc(proc, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
            bootstrap = self._rpc(proc, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'memory_bootstrap', 'arguments': {}}})
            payload = json.loads(bootstrap['result']['content'][0]['text'])
            self.assertIn('recovery_context', payload)
        finally:
            proc.stdin.close()
            proc.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
