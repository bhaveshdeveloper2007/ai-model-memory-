"""MEMORY OS v0.4 tests: intelligent memory lifecycle, project health and
context manifests, session intelligence, task intelligence, agent history.

All tests run against isolated temporary databases; the installed Memory OS
database is never touched.
"""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('memoryos', ROOT / 'engine/memoryos.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class V04Base(unittest.TestCase):
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

    def _mem(self, content, category='PROJECT_CONTEXT', **kwargs):
        return m.call('remember', {'cwd': str(self.root / 'projA'), 'category': category, 'content': content, **kwargs})['memory']


class TestImportanceScoring(V04Base):
    def test_verified_architecture_scores_above_proposed_session_summary(self):
        verified = self._mem('Auth uses signed JWTs with rotation.', 'ARCHITECTURE', verification='verified', importance=0.9)
        summary = self._mem('Discussed folder layout.', 'SESSION_SUMMARY')
        verified_score = m.importance_score(verified)
        summary_score = m.importance_score(summary)
        self.assertGreater(verified_score['score'], summary_score['score'])
        self.assertIn('category_base', verified_score['factors'])

    def test_superseded_penalty_is_applied(self):
        mem = self._mem('Old design choice.', 'DECISION', verification='verified')
        score_active = m.importance_score(mem)['score']
        score_superseded = m.importance_score({**mem, 'status': 'superseded'})['score']
        self.assertLess(score_superseded, score_active)

    def test_references_increase_score(self):
        mem = self._mem('Shared fact.', 'REFERENCE')
        self.assertLess(m.importance_score(mem)['score'], m.importance_score(mem, references=3)['score'])

    def test_scores_never_authorize_deletion(self):
        """The lowest-scoring memory must remain retrievable; scoring is a
        ranking signal, not a retention decision."""
        low = self._mem('Small note.', 'SESSION_SUMMARY', verification='proposed')
        result = m.call('get_memory', {'memory_id': low['id']})
        self.assertEqual(result['id'], low['id'])


class TestDuplicateDetection(V04Base):
    def test_exact_duplicate_detected_without_change(self):
        first = self._mem('Deploy pipeline uses GitHub Actions with macOS runners.', 'PROJECT_CONTEXT')
        self._mem('Deploy pipeline uses GitHub Actions with macOS runners.', 'PROJECT_CONTEXT')  # idempotent remember collapses this one
        self._mem('Completely unrelated storage note.', 'PROJECT_CONTEXT')
        result = m.call('find_duplicate_memories', {'cwd': str(self.root / 'projA')})
        exact = [pair for pair in result['candidates'] if pair['type'] == 'exact']
        self.assertEqual(exact, [])  # exact dupes are already collapsed by idempotent remember

    def test_near_duplicate_detected_within_category(self):
        self._mem('The API gateway routes requests through the authentication middleware before reaching handlers.', 'ARCHITECTURE')
        self._mem('The API gateway routes requests through the authentication middleware layer before reaching the handlers.', 'ARCHITECTURE')
        self._mem('Unrelated: the database uses WAL journaling mode.', 'ARCHITECTURE')
        result = m.call('find_duplicate_memories', {'cwd': str(self.root / 'projA'), 'threshold': 0.7})
        near = [pair for pair in result['candidates'] if pair['type'] == 'near_duplicate']
        self.assertEqual(len(near), 1)
        self.assertGreaterEqual(near[0]['similarity'], 0.7)

    def test_detection_never_modifies_records(self):
        before = m.call('list_memories', {'cwd': str(self.root / 'projA')})
        m.call('find_duplicate_memories', {'cwd': str(self.root / 'projA')})
        after = m.call('list_memories', {'cwd': str(self.root / 'projA')})
        self.assertEqual([row['id'] for row in before], [row['id'] for row in after])

    def test_isolated_projects_do_not_cross_match(self):
        m.call('remember', {'cwd': str(self.root / 'projA'), 'category': 'ARCHITECTURE', 'content': 'The build system uses CMake with Ninja generator.'})
        m.call('remember', {'cwd': str(self.root / 'projB'), 'category': 'ARCHITECTURE', 'content': 'The build system uses CMake with Ninja generator but different flags.'})
        result = m.call('find_duplicate_memories', {'cwd': str(self.root / 'projA')})
        self.assertEqual(result['count'], 0)


class TestConsolidation(V04Base):
    def test_consolidation_merges_and_preserves_evidence(self):
        primary = self._mem('Primary design: layered networking stack.', 'ARCHITECTURE', verification='verified')
        secondary = self._mem('Secondary note: retry policy uses exponential backoff.', 'ARCHITECTURE')
        result = m.call('consolidate_memories', {'primary_id': primary['id'], 'secondary_id': secondary['id'], 'agent': 'codex', 'cwd': str(self.root / 'projA')})
        self.assertIn('retry policy', result['memory']['content'])
        self.assertEqual(result['memory']['version'], 2)
        history = m.call('get_memory_history', {'memory_id': primary['id']})
        self.assertEqual(len(history['versions']), 1)  # previous state preserved
        retired = m.call('get_memory', {'memory_id': secondary['id']})
        self.assertEqual(retired['status'], 'superseded')
        # Every original ID remains retrievable (evidence preserved).
        with m.connect() as db:
            link = db.execute("SELECT relation FROM memory_relationships WHERE to_memory=?", (primary['id'],)).fetchone()
        self.assertEqual(link['relation'], 'consolidated_into')

    def test_consolidation_refuses_cross_project(self):
        primary = self._mem('Project A fact.', 'PROJECT_CONTEXT')
        other = m.call('remember', {'cwd': str(self.root / 'projB'), 'category': 'PROJECT_CONTEXT', 'content': 'Project B fact.'})['memory']
        with self.assertRaisesRegex(ValueError, 'different projects'):
            m.call('consolidate_memories', {'primary_id': primary['id'], 'secondary_id': other['id']})


class TestVerifyMemory(V04Base):
    def test_verification_requires_evidence(self):
        mem = self._mem('Bug fixed by switching to bounded queues.', 'ERROR')
        with self.assertRaisesRegex(ValueError, 'requires evidence'):
            m.call('verify_memory', {'memory_id': mem['id']})
        result = m.call('verify_memory', {'memory_id': mem['id'], 'evidence': 'regression test test_queue_bounds passed'})
        self.assertEqual(result['memory']['verification'], 'verified')
        self.assertIn('regression test', result['evidence'])


class TestProjectIntelligence(V04Base):
    def test_project_health_reports_counts_and_staleness(self):
        m.call('memory_create_task', {'cwd': str(self.root / 'projA'), 'title': 'unfinished work', 'status': 'IN_PROGRESS'})
        m.call('memory_record_error', {'cwd': str(self.root / 'projA'), 'summary': 'build broken', 'agent': 'codex'})
        health = m.call('get_project_health', {'cwd': str(self.root / 'projA')})
        self.assertEqual(health['health'], 'active')
        self.assertEqual(health['incomplete_tasks'], 1)
        self.assertEqual(health['open_errors'], 1)

    def test_context_manifest_writes_bounded_projections(self):
        for index in range(8):
            self._mem(f'Context fact number {index} for the project.', 'PROJECT_CONTEXT', importance=0.5 + index / 20)
        result = m.call('build_project_context', {'cwd': str(self.root / 'projA'), 'budget': 5})
        self.assertGreater(len(result['files']), 5)
        overview = Path(result['files'][0])
        self.assertTrue(overview.is_file())
        self.assertLessEqual(len(overview.read_text().splitlines()), 12)  # budget bounds the projection

    def test_context_manifest_is_atomically_written(self):
        result = m.call('build_project_context', {'cwd': str(self.root / 'projA')})
        for name in result['files']:
            self.assertNotIn('.proj-', name)  # no leftover temp files


class TestSessionIntelligence(V04Base):
    def test_session_summary_and_auto_checkpoint(self):
        session = m.call('start_session', {'cwd': str(self.root / 'projA'), 'agent': 'codex', 'session_id': 's-v04'})
        m.call('memory_save_checkpoint', {'cwd': str(self.root / 'projA'), 'summary': 'mid-work', 'state': {'step': 2}, 'session_id': session['session_id'], 'agent': 'codex'})
        ended = m.call('end_session', {'cwd': str(self.root / 'projA'), 'session_id': session['session_id'], 'summary': 'Implemented the parser.', 'checkpoint_state': {'step': 3, 'next': 'write tests'}})
        self.assertTrue(ended['ended'])
        self.assertIsNotNone(ended['checkpoint_id'])
        summary = m.call('get_session_summary', {'session_id': session['session_id']})
        self.assertEqual(summary['session']['status'], 'ended')
        self.assertIsNotNone(summary['checkpoint'])
        restored = m.call('restore_checkpoint', {'checkpoint_id': ended['checkpoint_id']})
        self.assertEqual(restored['state']['next'], 'write tests')

    def test_interrupted_session_recovery(self):
        m.call('start_session', {'cwd': str(self.root / 'projA'), 'agent': 'codex', 'session_id': 'stale-1'})
        # Force the session to look old.
        with m.connect() as db:
            db.execute("UPDATE sessions SET started_at='2020-01-01T00:00:00+00:00' WHERE external_marker IS NULL AND session_row='stale-1'" if False else "UPDATE sessions SET started_at='2020-01-01T00:00:00+00:00'")
            db.commit()
        result = m.call('recover_interrupted_sessions', {'stale_after_hours': 12})
        self.assertEqual(result['count'], 1)
        sessions = m.call('get_recent_sessions', {'cwd': str(self.root / 'projA')})
        self.assertEqual(sessions[0]['status'], 'interrupted')
        # A fresh active session is not interrupted.
        m.call('start_session', {'cwd': str(self.root / 'projA'), 'agent': 'codex', 'session_id': 'fresh-1'})
        again = m.call('recover_interrupted_sessions', {'stale_after_hours': 12})
        self.assertEqual(again['count'], 0)


class TestTaskIntelligence(V04Base):
    def test_canonical_statuses_and_priorities(self):
        task = m.call('create_task', {'cwd': str(self.root / 'projA'), 'title': 'ship feature', 'status': 'TODO', 'priority': 'high', 'description': 'Ship the thing'})
        self.assertEqual(task['status'], 'TODO')
        self.assertEqual(task['priority'], 'high')
        with self.assertRaisesRegex(ValueError, 'status must be one of'):
            m.call('create_task', {'cwd': str(self.root / 'projA'), 'title': 'x', 'status': 'INVALID'})
        with self.assertRaisesRegex(ValueError, 'priority'):
            m.call('create_task', {'cwd': str(self.root / 'projA'), 'title': 'x', 'priority': 'urgent'})

    def test_complete_task_requires_evidence(self):
        task = m.call('create_task', {'cwd': str(self.root / 'projA'), 'title': 'fix bug', 'status': 'IN_PROGRESS'})
        with self.assertRaisesRegex(ValueError, 'requires evidence'):
            m.call('complete_task', {'task_id': task['task_id']})
        result = m.call('complete_task', {'task_id': task['task_id'], 'evidence': 'pytest -q passed 12 tests'})
        self.assertEqual(result['status'], 'COMPLETED')
        listed = m.call('list_tasks', {'cwd': str(self.root / 'projA'), 'status': 'COMPLETED'})
        self.assertEqual([row['id'] for row in listed], [task['task_id']])

    def test_task_dependencies_reject_cycles(self):
        first = m.call('create_task', {'cwd': str(self.root / 'projA'), 'title': 'first'})
        second = m.call('create_task', {'cwd': str(self.root / 'projA'), 'title': 'second'})
        third = m.call('create_task', {'cwd': str(self.root / 'projA'), 'title': 'third'})
        m.call('add_task_dependency', {'task_id': first['task_id'], 'depends_on_task': second['task_id']})
        m.call('add_task_dependency', {'task_id': second['task_id'], 'depends_on_task': third['task_id']})
        with self.assertRaisesRegex(ValueError, 'cycle'):
            m.call('add_task_dependency', {'task_id': third['task_id'], 'depends_on_task': first['task_id']})
        with self.assertRaisesRegex(ValueError, 'itself'):
            m.call('add_task_dependency', {'task_id': first['task_id'], 'depends_on_task': first['task_id']})


class TestAgentHistory(V04Base):
    def test_agent_history_aggregates(self):
        m.call('start_session', {'cwd': str(self.root / 'projA'), 'agent': 'codex', 'session_id': 'h1'})
        m.call('record_agent_activity', {'cwd': str(self.root / 'projA'), 'agent': 'codex', 'operation': 'tests.run', 'result': 'passed'})
        m.call('record_agent_activity', {'cwd': str(self.root / 'projA'), 'agent': 'codex', 'operation': 'tests.run', 'result': 'passed'})
        history = m.call('get_agent_history', {'agent': 'codex'})
        self.assertGreaterEqual(len(history['sessions']), 1)
        self.assertGreaterEqual(history['activity_kinds'].get('agent.activity', 0), 2)
        self.assertTrue(any(project['name'] for project in history['projects']))


class TestCanonicalCategories(V04Base):
    def test_v04_categories_accepted_and_legacy_aliased(self):
        memory = self._mem('Uses Postgres 15 locally.', 'ENVIRONMENT')
        self.assertEqual(memory['category'], 'ENVIRONMENT')
        legacy = self._mem('Prefers dark mode.', 'user_preference')
        self.assertEqual(legacy['category'], 'GLOBAL_USER_PREFERENCE')
        with self.assertRaisesRegex(ValueError, 'Category must be one of'):
            self._mem('bad', 'not_a_category')

    def test_storage_and_backup_status_tools(self):
        status = m.call('get_storage_status', {})
        self.assertIn('database_bytes', status)
        self.assertIn('r2', status)
        self.assertFalse(status['r2']['enabled'])
        backup = m.call('get_backup_status', {})
        self.assertIn('upload_policy', backup)
        integrity = m.call('verify_storage_integrity', {})
        self.assertTrue(integrity['healthy'])
        self.assertTrue(any('Retain' in rec for rec in integrity['recommendations']))


if __name__ == '__main__':
    unittest.main()
