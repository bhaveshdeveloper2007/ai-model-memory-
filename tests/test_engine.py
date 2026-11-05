import importlib.util, tempfile, unittest, pathlib, json, os
spec=importlib.util.spec_from_file_location('memoryos',pathlib.Path(__file__).parents[1]/'engine/memoryos.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class EngineTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.old=(m.DB,m.JOURNAL); base=pathlib.Path(self.tmp.name);m.DB=base/'db.sqlite';m.JOURNAL=base/'events.jsonl';self.base=base
 def tearDown(self):m.DB,m.JOURNAL=self.old;self.tmp.cleanup()
 def repo(self,name):
  p=self.base/name;p.mkdir();os.system('git -C '+repr(str(p))+' init -q');return p
 def test_three_git_projects_and_non_git_isolation(self):
  a,b,c=(self.repo(x) for x in ('a','b','c')); plain=self.base/'plain';plain.mkdir()
  ids=[m.project(x)['project_id'] for x in (a,b,c,plain)]
  self.assertEqual(len(set(ids)),4)
  m.event({'cwd':str(a),'event_type':'one','note':'alpha'});m.event({'cwd':str(b),'event_type':'two','note':'beta'})
  self.assertEqual(len(m.summary(str(a))['recent_activity']),1);self.assertEqual(len(m.summary(str(b))['recent_activity']),1)
 def test_journal_ingest_and_dedupe(self):
  p=self.repo('repo');m.event({'cwd':str(p),'event_type':'observed','message':'works'});self.assertEqual(m.ingest(),1)
  with m.connect() as db:self.assertEqual(db.execute('select count(*) from activity_events').fetchone()[0],1)
 def test_task_checkpoint_and_error(self):
  p=self.repo('repo');t=m.call('memory_create_task',{'cwd':str(p),'title':'verify'});e=m.call('memory_record_error',{'cwd':str(p),'summary':'failure'});cp=m.call('memory_save_checkpoint',{'cwd':str(p),'summary':'resume','state':{'next':'inspect'}})
  self.assertTrue(t['task_id']);self.assertEqual(e['status'],'OPEN');self.assertTrue(cp['checkpoint_id'])
  m.call('memory_verify_task',{'project_id':m.project(p)['project_id'],'task_id':t['task_id'],'exit_code':0,'evidence':'test command passed'})
  with m.connect() as db:self.assertEqual(db.execute('select status from tasks where id=?',(t['task_id'],)).fetchone()[0],'VERIFIED')
if __name__=='__main__':unittest.main(verbosity=2)
