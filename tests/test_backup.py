import importlib.util, io, json, pathlib, sqlite3, tempfile, tarfile, unittest

ROOT=pathlib.Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('memoryos_backup',ROOT/'scripts/backup.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

class BackupArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();base=pathlib.Path(self.tmp.name)
        b.mem.DB=base/'database.sqlite3';b.mem.JOURNAL=base/'journal'/'events.jsonl';b.mem.JOURNAL_LOCK=base/'journal'/'events.lock'
        b.ROOT=base
        self.project=base/'sample-project';self.project.mkdir()
        (base/'config').mkdir()
        (base/'config/backup.json').write_text('{"repository":"test/private"}\n')
    def tearDown(self): self.tmp.cleanup()
    def test_snapshot_contains_database_and_flushed_event_journal(self):
        b.mem.event({'cwd':str(self.project),'event_type':'test.snapshot','summary':'safe metadata'})
        archive,journal=b.snapshot_archive()
        with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tar:
            self.assertIn('memoryos.sqlite3',tar.getnames())
            self.assertIn('events.jsonl',tar.getnames())
            self.assertIn('config/backup.json',tar.getnames())
            dbpath=pathlib.Path(self.tmp.name)/'snapshot.sqlite3';dbpath.write_bytes(tar.extractfile('memoryos.sqlite3').read())
            db=sqlite3.connect(dbpath)
            self.assertEqual(db.execute('select count(*) from activity_events').fetchone()[0],1)
            self.assertIn(b'test.snapshot',journal)
            db.close()
    def test_snapshot_archive_is_decryptable_format_by_contract(self):
        archive,_=b.snapshot_archive()
        self.assertGreater(len(archive),0)
        with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tar:
            self.assertTrue(any(x.name=='memoryos.sqlite3' for x in tar.getmembers()))

    def test_snapshot_refuses_to_clear_an_uningestable_event(self):
        b.mem.JOURNAL.parent.mkdir(parents=True,exist_ok=True)
        b.mem.JOURNAL.write_text('{"not":"a valid memory event"}\n')
        with self.assertRaisesRegex(RuntimeError,'Journal ingestion incomplete'):
            b.snapshot_archive()
        self.assertTrue(b.mem.JOURNAL.exists())
        self.assertEqual(len(b.mem.JOURNAL.read_text().splitlines()),1)

    def test_local_retention_runs_only_after_upload_and_preserves_tasks(self):
        old={'cwd':str(self.project),'event_type':'old','occurred_at':'2020-01-01T00:00:00+00:00'}
        recent={'cwd':str(self.project),'event_type':'recent'}
        b.mem.event(old);b.mem.event(recent)
        task=b.mem.call('memory_create_task',{'cwd':str(self.project),'title':'important task'})
        captured=b.mem.JOURNAL.read_bytes()
        b.prune_after_backup(captured,30)
        with b.mem.connect() as db:
            self.assertEqual(db.execute("select count(*) from activity_events where event_type='old'").fetchone()[0],0)
            self.assertEqual(db.execute("select count(*) from activity_events where event_type='recent'").fetchone()[0],1)
            self.assertEqual(db.execute('select count(*) from tasks where id=?',(task['task_id'],)).fetchone()[0],1)
        self.assertEqual(b.mem.JOURNAL.read_bytes(),b'')

    def test_restore_merges_old_events_from_earlier_encrypted_snapshots(self):
        b.mem.event({'cwd':str(self.project),'event_type':'historic','occurred_at':'2020-01-01T00:00:00+00:00'})
        b.mem.event({'cwd':str(self.project),'event_type':'recent'})
        task=b.mem.call('memory_create_task',{'cwd':str(self.project),'title':'restore important task'})
        decision=b.mem.call('memory_record_decision',{'cwd':str(self.project),'summary':'restore decision'})
        error=b.mem.call('memory_record_error',{'cwd':str(self.project),'summary':'restore error'})
        old_archive,_=b.snapshot_archive()
        b.prune_after_backup(b.mem.JOURNAL.read_bytes(),30)
        # Emulate a new, empty local installation creating a snapshot before restore.
        b.mem.DB=pathlib.Path(self.tmp.name)/'fresh.sqlite3';b.mem.JOURNAL=pathlib.Path(self.tmp.name)/'fresh-journal'/'events.jsonl';b.mem.JOURNAL_LOCK=pathlib.Path(self.tmp.name)/'fresh-journal'/'events.lock'
        b.mem.connect().close()
        latest_archive,_=b.snapshot_archive()
        repo=self.tmp.name and pathlib.Path(self.tmp.name)/'backup-repo';(repo/'snapshots').mkdir(parents=True)
        old_file=repo/'snapshots'/'20200101T000000Z.mos';latest_file=repo/'snapshots'/'20200102T000000Z.mos'
        old_file.write_bytes(b'ciphertext-1');latest_file.write_bytes(b'ciphertext-2')
        original={key:getattr(b,key) for key in ('config','repo_private','ensure_key','checkout','decrypt_file')}
        b.config=lambda:{'repository':'example/private-db'};b.repo_private=lambda slug:None
        b.ensure_key=lambda slug:('test-key',pathlib.Path(self.tmp.name)/'recovery-key')
        b.checkout=lambda slug:repo
        b.decrypt_file=lambda path,svc:old_archive if path==old_file else latest_archive
        try:
            b.restore_latest()
            with b.mem.connect() as db:
                events={row[0] for row in db.execute('select event_type from activity_events')}
                self.assertEqual(events,{'historic','recent'})
                self.assertEqual(db.execute('select title from tasks where id=?',(task['task_id'],)).fetchone()[0],'restore important task')
                self.assertEqual(db.execute('select summary from decisions where id=?',(decision['decision_id'],)).fetchone()[0],'restore decision')
                self.assertEqual(db.execute('select summary from errors where id=?',(error['error_id'],)).fetchone()[0],'restore error')
            self.assertEqual(json.loads((pathlib.Path(self.tmp.name)/'config/backup.json').read_text())['repository'],'test/private')
        finally:
            for key,value in original.items():setattr(b,key,value)

if __name__=='__main__': unittest.main(verbosity=2)
