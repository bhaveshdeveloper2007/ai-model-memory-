#!/usr/bin/env python3
"""Memory OS local-first event and project memory engine (Python stdlib only)."""
from __future__ import annotations
import argparse, datetime as dt, fcntl, hashlib, json, os, re, sqlite3, subprocess, sys, threading, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'storage/database/memoryos.sqlite3'; JOURNAL=ROOT/'storage/journal/events.jsonl'
JOURNAL_LOCK=ROOT/'storage/journal/events.lock'
SCHEMA='''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, name TEXT NOT NULL, root_path TEXT NOT NULL, remote_url TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), path TEXT UNIQUE NOT NULL, git_common_dir TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), agent_id TEXT NOT NULL REFERENCES agents(id), started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT REFERENCES sessions(id), title TEXT NOT NULL, status TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS subtasks(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PLANNED');
CREATE TABLE IF NOT EXISTS activity_events(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT REFERENCES sessions(id), agent_id TEXT NOT NULL REFERENCES agents(id), event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT UNIQUE NOT NULL, source TEXT NOT NULL DEFAULT 'local');
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), path TEXT NOT NULL, kind TEXT, metadata TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS file_changes(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT, path TEXT NOT NULL, change_type TEXT NOT NULL, occurred_at TEXT NOT NULL, evidence TEXT);
CREATE TABLE IF NOT EXISTS terminal_commands(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT, command TEXT NOT NULL, exit_code INTEGER, occurred_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS errors(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT, summary TEXT NOT NULL, details TEXT, status TEXT NOT NULL DEFAULT 'OPEN', occurred_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS error_resolutions(id TEXT PRIMARY KEY, error_id TEXT NOT NULL REFERENCES errors(id), resolution TEXT NOT NULL, evidence TEXT, verified INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT, summary TEXT NOT NULL, rationale TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT, summary TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), category TEXT NOT NULL, content TEXT NOT NULL, importance REAL NOT NULL DEFAULT 0.5, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory_relationships(id TEXT PRIMARY KEY, from_memory TEXT NOT NULL REFERENCES memories(id), to_memory TEXT NOT NULL REFERENCES memories(id), relation TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS integrations(id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, status TEXT NOT NULL, details TEXT);
CREATE TABLE IF NOT EXISTS verification_results(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT REFERENCES tasks(id), command TEXT, exit_code INTEGER, evidence TEXT, verified_at TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(memory_id UNINDEXED, project_id UNINDEXED, content, category);
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(event_id UNINDEXED, project_id UNINDEXED, event_type, payload);
CREATE INDEX IF NOT EXISTS idx_events_project_time ON activity_events(project_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, started_at DESC);
'''
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def journal_lock():
    JOURNAL_LOCK.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(JOURNAL_LOCK,os.O_CREAT|os.O_RDWR,0o600); fcntl.flock(fd,fcntl.LOCK_EX); return fd
def journal_unlock(fd):
    fcntl.flock(fd,fcntl.LOCK_UN); os.close(fd)
class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try: return super().__exit__(exc_type, exc, tb)
        finally: self.close()
def connect():
    DB.parent.mkdir(parents=True,exist_ok=True); os.chmod(DB.parent,0o700); c=sqlite3.connect(DB,timeout=15,factory=ClosingConnection); os.chmod(DB,0o600); c.row_factory=sqlite3.Row; c.execute('PRAGMA journal_mode=WAL'); c.executescript(SCHEMA); return c
def git(path,*args):
    try:return subprocess.check_output(['git','-C',str(path),*args],stderr=subprocess.DEVNULL,text=True,timeout=2).strip()
    except Exception:return ''
def detect(path):
    p=Path(path).expanduser().resolve(); root=git(p,'rev-parse','--show-toplevel')
    if root:
        root=Path(root); remote=git(root,'config','--get','remote.origin.url') or None
        common=git(p,'rev-parse','--git-common-dir') or None
        stable='git:'+remote.lower().rstrip('/') if remote else 'gitpath:'+str(root)
        name=root.name
    else:
        root=p; remote=None; common=None; stable='path:'+hashlib.sha256(str(root).encode()).hexdigest(); name=root.name or str(root)
    return p,root,remote,common,stable,name
def project(path):
    p,root,remote,common,identity,name=detect(path); ts=now()
    with connect() as c:
        row=c.execute('SELECT * FROM projects WHERE identity=?',(identity,)).fetchone()
        if not row:
            # Distinct local clones of same remote get separate workspaces, stable project identity is shared by remote.
            pid=str(uuid.uuid4()); c.execute('INSERT INTO projects VALUES(?,?,?,?,?,?,?)',(pid,identity,name,str(root),remote,ts,ts))
        else: pid=row['id']; c.execute('UPDATE projects SET updated_at=?,root_path=?,remote_url=? WHERE id=?',(ts,str(root),remote,pid))
        wid=str(uuid.uuid5(uuid.NAMESPACE_URL,str(p))); c.execute('INSERT OR IGNORE INTO workspaces VALUES(?,?,?,?,?)',(wid,pid,str(p),common,ts))
        c.execute('INSERT OR IGNORE INTO agents VALUES(?,?,?)',('codex','Codex','codex'))
    return {'project_id':pid,'name':name,'root':str(root),'workspace':str(p),'remote_url':remote,'identity':identity}
def event(payload):
    payload=dict(payload); path=payload.pop('cwd',None) or payload.pop('working_directory',None) or os.getcwd(); proj=project(path)
    eid=str(payload.get('id') or uuid.uuid4()); typ=str(payload.get('event_type') or payload.get('type') or 'activity'); ts=str(payload.get('occurred_at') or now()); agent=str(payload.get('agent') or 'codex'); sid=payload.get('session_id')
    raw=json.dumps(payload,sort_keys=True,ensure_ascii=False); dig=hashlib.sha256((proj['project_id']+typ+ts+raw).encode()).hexdigest()
    ev={'id':eid,'project_id':proj['project_id'],'session_id':sid,'agent':agent,'event_type':typ,'occurred_at':ts,'payload':payload,'source':payload.get('source','hook')}
    JOURNAL.parent.mkdir(parents=True,exist_ok=True)
    line=json.dumps(ev,ensure_ascii=False)+'\n'
    lockfd=journal_lock()
    try:
        fd=os.open(JOURNAL,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        with os.fdopen(fd,'a',encoding='utf-8') as f:f.write(line); f.flush(); os.fsync(f.fileno())
        ingest(ev,dig)
    finally: journal_unlock(lockfd)
    return {'event_id':eid,'project':proj,'persisted':True}
def ensure_session(c,project_id,agent_id,external_session_id,occurred_at):
    if external_session_id in (None,''): return None
    external=str(external_session_id)
    # Scope client session IDs to a project so a reused external ID cannot join projects.
    sid=str(uuid.uuid5(uuid.NAMESPACE_URL,project_id+':'+external))
    c.execute('INSERT OR IGNORE INTO sessions VALUES(?,?,?,?,?,?)',(sid,project_id,agent_id,occurred_at,None,'active'))
    row=c.execute('SELECT project_id,agent_id FROM sessions WHERE id=?',(sid,)).fetchone()
    if not row or row['project_id']!=project_id or row['agent_id']!=agent_id:
        raise ValueError('Session identity conflicts with this project.')
    return sid
def ingest(ev=None,digest=None):
    if ev:
        with connect() as c:
            agent=str(ev.get('agent') or 'generic');c.execute('INSERT OR IGNORE INTO agents VALUES(?,?,?)',(agent,agent,agent))
            session_id=ensure_session(c,ev['project_id'],agent,ev.get('session_id'),ev['occurred_at'])
            if str(ev['event_type']).lower().endswith('sessionend') and session_id:
                c.execute("UPDATE sessions SET ended_at=?,status='ended' WHERE id=?",(ev['occurred_at'],session_id))
            cur=c.execute('INSERT OR IGNORE INTO activity_events VALUES(?,?,?,?,?,?,?,?,?)',(ev['id'],ev['project_id'],session_id,agent,ev['event_type'],ev['occurred_at'],json.dumps(ev['payload'],ensure_ascii=False),digest,ev.get('source','local')))
            if cur.rowcount: c.execute('INSERT INTO events_fts VALUES(?,?,?,?)',(ev['id'],ev['project_id'],ev['event_type'],json.dumps(ev['payload'],ensure_ascii=False)))
        return
    if not JOURNAL.exists():return 0
    n=0
    for line in JOURNAL.read_text(encoding='utf-8').splitlines():
        try:
            x=json.loads(line); d=hashlib.sha256((x['project_id']+x['event_type']+x['occurred_at']+json.dumps(x['payload'],sort_keys=True,ensure_ascii=False)).encode()).hexdigest(); ingest(x,d); n+=1
        except Exception: continue
    return n
def search(q, path=None, limit=20):
    ingest(); pid=project(path)['project_id'] if path else None
    with connect() as c:
        try:
            sql='SELECT m.*,bm25(memories_fts) rank FROM memories_fts JOIN memories m ON m.id=memories_fts.memory_id WHERE memories_fts MATCH ?'; args=[q]
            if pid:sql+=' AND m.project_id=?';args.append(pid)
            sql+=' ORDER BY rank, m.updated_at DESC LIMIT ?';args.append(limit)
            rows=[dict(x) for x in c.execute(sql,args)]
        except sqlite3.OperationalError: rows=[]
        if not rows:
            sql='SELECT * FROM activity_events WHERE (payload LIKE ? OR event_type LIKE ?)';args=[f'%{q}%',f'%{q}%']
            if pid:sql+=' AND project_id=?';args.append(pid)
            sql+=' ORDER BY occurred_at DESC LIMIT ?';args.append(limit)
            rows=[dict(x) for x in c.execute(sql,args)]
    return rows
def summary(path):
    p=project(path); ingest()
    with connect() as c:
        tasks=[dict(x) for x in c.execute('SELECT * FROM tasks WHERE project_id=? ORDER BY updated_at DESC',(p['project_id'],))]
        events=[dict(x) for x in c.execute('SELECT event_type,occurred_at,payload FROM activity_events WHERE project_id=? ORDER BY occurred_at DESC LIMIT 30',(p['project_id'],))]
        checkpoint=c.execute('SELECT * FROM checkpoints WHERE project_id=? ORDER BY created_at DESC LIMIT 1',(p['project_id'],)).fetchone()
    return {**p,'tasks':tasks,'recent_activity':events,'checkpoint':dict(checkpoint) if checkpoint else None}
def call(name,a):
    if name=='memory_bootstrap':
        s=summary(a.get('cwd') or os.getcwd()); return {'project':s,'recovery_context':{'incomplete_tasks':[t for t in s['tasks'] if t['status'] not in ('VERIFIED','REVERTED')],'latest_checkpoint':s['checkpoint'],'recent_activity':s['recent_activity'][:10]},'next_action':'Review incomplete tasks and latest project changes.'}
    if name=='memory_search':return {'results':search(a['query'],a.get('cwd'),a.get('limit',20))}
    if name in ('memory_get_project','memory_get_project_state','memory_get_project_summary'):return summary(a.get('cwd') or os.getcwd())
    if name=='memory_get_recent_activity':return summary(a.get('cwd') or os.getcwd())['recent_activity']
    if name=='memory_list_projects':
        with connect() as c:return [dict(r) for r in c.execute('SELECT * FROM projects ORDER BY updated_at DESC')]
    if name=='memory_record_event':return event(a)
    if name=='memory_create_task':
        p=project(a.get('cwd') or os.getcwd()); ts=now(); tid=str(uuid.uuid4())
        with connect() as c:
            c.execute('INSERT OR IGNORE INTO agents VALUES(?,?,?)',('codex','Codex','codex'))
            sid=ensure_session(c,p['project_id'],'codex',a.get('session_id'),ts)
            c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)',(tid,p['project_id'],sid,a['title'],a.get('status','PLANNED'),a.get('details'),ts,ts))
        return {'task_id':tid,'status':a.get('status','PLANNED')}
    if name=='memory_update_task':
        with connect() as c:c.execute('UPDATE tasks SET status=?,updated_at=? WHERE id=?',(a['status'],now(),a['task_id']))
        return {'updated':True}
    if name=='memory_record_decision':
        p=project(a.get('cwd') or os.getcwd()); did=str(uuid.uuid4())
        with connect() as c:c.execute('INSERT INTO decisions VALUES(?,?,?,?,?,?)',(did,p['project_id'],a.get('session_id'),a['summary'],a.get('rationale'),now()))
        return {'decision_id':did}
    if name=='memory_record_error':
        p=project(a.get('cwd') or os.getcwd()); eid=str(uuid.uuid4())
        with connect() as c:c.execute('INSERT INTO errors VALUES(?,?,?,?,?,?,?)',(eid,p['project_id'],a.get('session_id'),a['summary'],a.get('details'),'OPEN',now()))
        return {'error_id':eid,'status':'OPEN'}
    if name=='memory_verify_task':
        with connect() as c:
            c.execute('INSERT INTO verification_results VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid4()),a['project_id'],a.get('task_id'),a.get('command'),a.get('exit_code'),a.get('evidence'),now()))
            if a.get('task_id') and a.get('exit_code')==0:c.execute("UPDATE tasks SET status='VERIFIED',updated_at=? WHERE id=?",(now(),a['task_id']))
        return {'recorded':True,'verified':a.get('exit_code')==0}
    if name=='memory_save_checkpoint':
        p=project(a.get('cwd') or os.getcwd()); cid=str(uuid.uuid4()); state=json.dumps(a.get('state',{}),ensure_ascii=False)
        with connect() as c:c.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?)',(cid,p['project_id'],a.get('session_id'),a['summary'],state,now()))
        cpfile=ROOT/'storage/checkpoints'/f'{cid}.json'; cpfile.parent.mkdir(parents=True,exist_ok=True); os.chmod(cpfile.parent,0o700); cpfile.write_text(state); os.chmod(cpfile,0o600)
        return {'checkpoint_id':cid}
    if name=='memory_get_next_action':
        s=summary(a.get('cwd') or os.getcwd()); return {'incomplete_tasks':[t for t in s['tasks'] if t['status'] not in ('VERIFIED','REVERTED')],'suggestion':'Inspect the current Git diff, then continue the highest priority incomplete task.'}
    if name=='memory_compare_project_state':return {'project':summary(a.get('cwd') or os.getcwd()),'git_status':git(a.get('cwd') or os.getcwd(),'status','--short')}
    raise ValueError('Unknown tool: '+name)
TOOLS=[('memory_bootstrap','Identify project and prepare recovery context',{'cwd':'string'}),('memory_search','Search project memory and events',{'query':'string','cwd':'string','limit':'number'}),('memory_get_project','Get detected project details',{'cwd':'string'}),('memory_get_project_state','Get project tasks and current state',{'cwd':'string'}),('memory_get_recent_activity','Get recent captured events',{'cwd':'string'}),('memory_record_event','Persist an observable activity event',{'cwd':'string','event_type':'string','payload':'object'}),('memory_create_task','Create a project task',{'cwd':'string','title':'string','status':'string','details':'string'}),('memory_update_task','Update a task status',{'task_id':'string','status':'string'}),('memory_record_decision','Record an architectural or technical decision',{'cwd':'string','summary':'string','rationale':'string'}),('memory_record_error','Record an encountered error',{'cwd':'string','summary':'string','details':'string'}),('memory_verify_task','Store verification evidence; exit code 0 verifies task',{'project_id':'string','task_id':'string','command':'string','exit_code':'number','evidence':'string'}),('memory_save_checkpoint','Save recovery checkpoint',{'cwd':'string','summary':'string','state':'object'}),('memory_get_next_action','Get incomplete tasks and a suggested next action',{'cwd':'string'}),('memory_get_project_summary','Get project summary',{'cwd':'string'}),('memory_compare_project_state','Compare stored state with current Git status',{'cwd':'string'}),('memory_list_projects','List known projects',{})]
def mcp():
    def send(obj): sys.stdout.write(json.dumps(obj)+'\n'); sys.stdout.flush()
    for line in sys.stdin:
        try:
            msg=json.loads(line); method=msg.get('method'); i=msg.get('id')
            if method=='notifications/initialized':continue
            if method=='initialize':send({'jsonrpc':'2.0','id':i,'result':{'protocolVersion':msg.get('params',{}).get('protocolVersion','2024-11-05'),'capabilities':{'tools':{}},'serverInfo':{'name':'memory-os','version':'0.1.0'}}})
            elif method=='tools/list': send({'jsonrpc':'2.0','id':i,'result':{'tools':[{'name':n,'description':d,'inputSchema':{'type':'object','properties':{k:{'type':v} for k,v in props.items()},'required':[k for k in props if (n,k) in {('memory_search','query'),('memory_create_task','title'),('memory_update_task','task_id'),('memory_update_task','status'),('memory_record_decision','summary'),('memory_record_error','summary'),('memory_record_event','event_type'),('memory_save_checkpoint','summary')}],'additionalProperties':True}} for n,d,props in TOOLS]}})
            elif method=='tools/call':
                p=msg['params']; args=p.get('arguments',{}); result=call(p['name'],args);send({'jsonrpc':'2.0','id':i,'result':{'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}]}})
            elif i is not None:send({'jsonrpc':'2.0','id':i,'result':{}})
        except Exception as e:
            try:send({'jsonrpc':'2.0','id':msg.get('id'),'error':{'code':-32000,'message':str(e)}})
            except Exception:pass
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        u=urlparse(self.path)
        if u.path=='/health':data={'status':'ok','database':str(DB),'journal':str(JOURNAL)}
        elif u.path=='/projects':data=call('memory_list_projects',{})
        elif u.path=='/summary':data=summary(parse_qs(u.query).get('cwd',[os.getcwd()])[0])
        elif u.path=='/search':data={'results':search(parse_qs(u.query).get('q',[''])[0],parse_qs(u.query).get('cwd',[None])[0])}
        else:
            html='''<!doctype html><meta charset="utf-8"><title>Memory OS</title><style>body{font:15px -apple-system;background:#10141c;color:#e7edf6;margin:40px;max-width:1000px}h1{color:#8ab4ff}section{background:#1a2230;border-radius:12px;padding:20px;margin:16px 0}input,button{padding:10px;border-radius:8px;border:0}pre{white-space:pre-wrap}</style><h1>Memory OS</h1><p>Local project memory dashboard</p><section id="overview">Loading status…</section><section><h2>Projects</h2><pre id="projects"></pre></section><section><h2>Search memory and activity</h2><input id="q" placeholder="Search"><button onclick="run()">Search</button><pre id="results"></pre></section><script>async function load(){let h=await(await fetch('/health')).json(),p=await(await fetch('/projects')).json();document.querySelector('#overview').innerHTML='<b>Engine:</b> '+h.status+' &nbsp; <b>Projects:</b> '+p.length+'<br><b>Database:</b> '+h.database;document.querySelector('#projects').textContent=JSON.stringify(p,null,2)}async function run(){let r=await(await fetch('/search?q='+encodeURIComponent(document.querySelector('#q').value))).json();document.querySelector('#results').textContent=JSON.stringify(r,null,2)}load()</script>'''
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(html.encode());return
        body=json.dumps(data,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
    def do_POST(self):
        try:
            n=int(self.headers.get('Content-Length',0)); obj=json.loads(self.rfile.read(n)); data=event(obj); b=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b)
        except Exception as e:self.send_error(400,str(e))
def main():
    ap=argparse.ArgumentParser();sp=ap.add_subparsers(dest='cmd');sp.add_parser('mcp');sp.add_parser('init');d=sp.add_parser('detect');d.add_argument('path',nargs='?',default=os.getcwd());e=sp.add_parser('event');e.add_argument('--json',required=True);sp.add_parser('ingest');s=sp.add_parser('serve');s.add_argument('--port',type=int,default=8765);args=ap.parse_args()
    if args.cmd=='mcp':mcp()
    elif args.cmd=='init':connect().close();print(json.dumps({'database':str(DB),'journal':str(JOURNAL)}))
    elif args.cmd=='detect':print(json.dumps(project(args.path),indent=2))
    elif args.cmd=='event':print(json.dumps(event(json.loads(args.json))))
    elif args.cmd=='ingest':print(ingest())
    elif args.cmd=='serve':ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
    else:ap.print_help()
if __name__=='__main__':main()
