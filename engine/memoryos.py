#!/usr/bin/env python3
"""Memory OS local-first event and project memory engine (Python stdlib only).

One shared engine for every local AI agent: project-aware memory with
versioning, verification statuses, secret redaction, idempotent capture,
sessions, checkpoints, agent activity, and a loopback-only dashboard.
"""
from __future__ import annotations
import argparse, datetime as dt, fcntl, hashlib, json, os, re, sqlite3, subprocess, sys, tempfile, threading, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT=Path(__file__).resolve().parents[1]
# An explicit store location keeps tests and side-by-side instances isolated
# from the installed database; defaults match the installed layout.
_DB_OVERRIDE=os.environ.get('MEMORYOS_DB')
_JOURNAL_OVERRIDE=os.environ.get('MEMORYOS_JOURNAL')
DB=Path(_DB_OVERRIDE) if _DB_OVERRIDE else ROOT/'storage/database/memoryos.sqlite3'
JOURNAL=Path(_JOURNAL_OVERRIDE) if _JOURNAL_OVERRIDE else ROOT/'storage/journal/events.jsonl'
JOURNAL_LOCK=JOURNAL.parent/'events.lock'
SCHEMA='''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, name TEXT NOT NULL, root_path TEXT NOT NULL, remote_url TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), path TEXT UNIQUE NOT NULL, git_common_dir TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_settings(agent_id TEXT NOT NULL REFERENCES agents(id), key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(agent_id, key));
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), agent_id TEXT NOT NULL REFERENCES agents(id), started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL DEFAULT 'active', summary TEXT);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT REFERENCES sessions(id), title TEXT NOT NULL, status TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, agent_id TEXT);
CREATE TABLE IF NOT EXISTS subtasks(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PLANNED');
CREATE TABLE IF NOT EXISTS task_dependencies(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), depends_on_task TEXT NOT NULL REFERENCES tasks(id), created_at TEXT NOT NULL, UNIQUE(task_id, depends_on_task));
CREATE TABLE IF NOT EXISTS activity_events(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT REFERENCES sessions(id), agent_id TEXT NOT NULL REFERENCES agents(id), event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT UNIQUE NOT NULL, source TEXT NOT NULL DEFAULT 'local');
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), path TEXT NOT NULL, kind TEXT, metadata TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS file_changes(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT, path TEXT NOT NULL, change_type TEXT NOT NULL, occurred_at TEXT NOT NULL, evidence TEXT);
CREATE TABLE IF NOT EXISTS terminal_commands(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT, command TEXT NOT NULL, exit_code INTEGER, occurred_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS errors(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT, summary TEXT NOT NULL, details TEXT, status TEXT NOT NULL DEFAULT 'OPEN', occurred_at TEXT NOT NULL, agent_id TEXT);
CREATE TABLE IF NOT EXISTS error_resolutions(id TEXT PRIMARY KEY, error_id TEXT NOT NULL REFERENCES errors(id), resolution TEXT NOT NULL, evidence TEXT, verified INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, agent_id TEXT);
CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT, summary TEXT NOT NULL, rationale TEXT, created_at TEXT NOT NULL, agent_id TEXT);
CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT, summary TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), category TEXT NOT NULL, content TEXT NOT NULL, importance REAL NOT NULL DEFAULT 0.5, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', source_agent TEXT NOT NULL DEFAULT 'unknown', source_session TEXT, confidence REAL NOT NULL DEFAULT 0.5, verification TEXT NOT NULL DEFAULT 'proposed', status TEXT NOT NULL DEFAULT 'active', tags TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1, content_digest TEXT, supersedes TEXT REFERENCES memories(id), expires_at TEXT, agent_id TEXT);
CREATE TABLE IF NOT EXISTS memory_versions(id TEXT PRIMARY KEY, memory_id TEXT NOT NULL REFERENCES memories(id), version INTEGER NOT NULL, content TEXT NOT NULL, title TEXT, importance REAL, confidence REAL, verification TEXT, status TEXT, replaced_at TEXT NOT NULL, actor TEXT);
CREATE TABLE IF NOT EXISTS memory_relationships(id TEXT PRIMARY KEY, from_memory TEXT NOT NULL REFERENCES memories(id), to_memory TEXT NOT NULL REFERENCES memories(id), relation TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS integrations(id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, status TEXT NOT NULL, details TEXT);
CREATE TABLE IF NOT EXISTS verification_results(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT REFERENCES tasks(id), command TEXT, exit_code INTEGER, evidence TEXT, verified_at TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(memory_id UNINDEXED, project_id UNINDEXED, content, category, title);
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(event_id UNINDEXED, project_id UNINDEXED, event_type, payload);
'''
SCHEMA_INDEXES='''
CREATE INDEX IF NOT EXISTS idx_events_project_time ON activity_events(project_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_agent_time ON activity_events(agent_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_project_status ON memories(project_id, status);
CREATE INDEX IF NOT EXISTS idx_memories_digest ON memories(project_id, content_digest);
CREATE INDEX IF NOT EXISTS idx_memory_versions_memory ON memory_versions(memory_id, version);
CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_errors_project ON errors(project_id, occurred_at DESC);
'''
# Columns added after the first release; existing databases are migrated in
# place (additive only) so installed memory is never recreated.
ADDED_COLUMNS={
  'projects': {'archived': 'INTEGER NOT NULL DEFAULT 0'},
  'tasks': {'agent_id': 'TEXT', 'priority': "TEXT NOT NULL DEFAULT 'normal'", 'description': 'TEXT'},
  'errors': {'agent_id': 'TEXT'},
  'error_resolutions': {'agent_id': 'TEXT'},
  'decisions': {'agent_id': 'TEXT'},
  'memories': {'title': "TEXT NOT NULL DEFAULT ''", 'source_agent': "TEXT NOT NULL DEFAULT 'unknown'", 'source_session': 'TEXT', 'confidence': 'REAL NOT NULL DEFAULT 0.5', 'verification': "TEXT NOT NULL DEFAULT 'proposed'", 'status': "TEXT NOT NULL DEFAULT 'active'", 'tags': "TEXT NOT NULL DEFAULT '[]'", 'version': 'INTEGER NOT NULL DEFAULT 1', 'content_digest': 'TEXT', 'supersedes': 'TEXT REFERENCES memories(id)', 'expires_at': 'TEXT', 'agent_id': 'TEXT'},
  'sessions': {'summary': 'TEXT'},
}
VERIFICATION_STATUSES={'proposed','observed','verified','superseded','rejected','archived'}
MEMORY_STATUSES={'active','archived','superseded','rejected'}
CATEGORIES={'user_preference','project_context','architecture','decision','task','progress','error','solution','technical_configuration','research_finding','session_summary','important_reference'}
# v0.4 canonical categories (PART 5.B). Legacy lowercase names stay valid so
# existing rows keep working; new records may use either set.
CATEGORIES_V4={'GLOBAL_USER_PREFERENCE','PROJECT_CONTEXT','ARCHITECTURE','DECISION','TASK','PROGRESS','ERROR','VERIFIED_SOLUTION','ENVIRONMENT','DEPENDENCY','RESEARCH','SESSION_SUMMARY','REFERENCE','CHECKPOINT'}
CATEGORY_ALIASES={'user_preference':'GLOBAL_USER_PREFERENCE','project_context':'PROJECT_CONTEXT','architecture':'ARCHITECTURE','decision':'DECISION','task':'TASK','progress':'PROGRESS','error':'ERROR','solution':'VERIFIED_SOLUTION','technical_configuration':'ENVIRONMENT','research_finding':'RESEARCH','session_summary':'SESSION_SUMMARY','important_reference':'REFERENCE'}
ALL_CATEGORIES=CATEGORIES | CATEGORIES_V4
# Task statuses: v0.4 canonical set plus every legacy status used so far.
TASK_STATUSES={'TODO','IN_PROGRESS','BLOCKED','COMPLETED','CANCELLED','PLANNED','VERIFIED','REVERTED','waiting_for_approval','running','failed','partially_completed','skipped'}
SECRET_PATTERNS=(
  (re.compile(r'sk-[A-Za-z0-9_-]{20,}'),'api_key'),
  (re.compile(r'ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}'),'github_token'),
  (re.compile(r'AKIA[0-9A-Z]{16}'),'aws_key'),
  (re.compile(r'xox[abp]-[0-9A-Za-z-]{10,}'),'slack_token'),
  (re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',re.DOTALL),'private_key_block'),
  (re.compile(r'(?i)(password|passwd|secret|token)\s*[=:]\s*["\'][^"\']{8,}["\']'),'credential_assignment'),
  (re.compile(r'(?i)bearer\s+[A-Za-z0-9._-]{20,}'),'bearer_token'),
)
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def redact(text):
  """Remove obvious secrets before anything is persisted. Returns (text, kinds)."""
  kinds=[]
  for pattern,kind in SECRET_PATTERNS:
    text,count=pattern.subn(f'[REDACTED:{kind}]',text)
    if count: kinds.append(kind)
  return text,kinds
def importance_score(memory, references=0, project_stats=None):
    """Explainable retrieval/retention score in [0,1]. Importance is a
    ranking signal only — it never authorizes deletion of a record."""
    factors={}
    base={'GLOBAL_USER_PREFERENCE':0.8,'ARCHITECTURE':0.75,'DECISION':0.7,'VERIFIED_SOLUTION':0.7,'CHECKPOINT':0.6,'ERROR':0.5,'PROJECT_CONTEXT':0.5,'REFERENCE':0.5,'DEPENDENCY':0.5,'ENVIRONMENT':0.5,'TASK':0.5,'PROGRESS':0.45,'RESEARCH':0.4,'SESSION_SUMMARY':0.35}.get((memory.get('category') or '').upper(),0.4)
    factors['category_base']=round(base,3)
    verification=(memory.get('verification') or '').lower()
    factors['verification']=0.15 if verification=='verified' else (0.05 if verification=='observed' else 0.0)
    try: user_importance=float(memory.get('importance') or 0.5)
    except (TypeError,ValueError): user_importance=0.5
    factors['explicit_importance']=round(0.25*user_importance,3)
    try: confidence=float(memory.get('confidence') or 0.5)
    except (TypeError,ValueError): confidence=0.5
    factors['confidence']=round(0.1*confidence,3)
    factors['references']=round(min(0.15, 0.05*max(0,int(references))),3)
    status=(memory.get('status') or 'active').lower()
    if status=='superseded': factors['superseded_penalty']=-0.2
    if verification=='rejected': factors['rejected_penalty']=-0.2
    score=base+factors['verification']+factors['explicit_importance']+factors['confidence']+factors['references']+factors.get('superseded_penalty',0)+factors.get('rejected_penalty',0)
    return {'score':round(max(0.0,min(1.0,score)),3),'factors':factors}

def _tokens(text_value):
    return {t for t in re.findall(r'[a-z0-9]{3,}', (text_value or '').lower()) if len(t)>=3}

def similarity(a_text, b_text):
    """Jaccard similarity over content tokens, for near-duplicate review."""
    a,b=_tokens(a_text),_tokens(b_text)
    if not a or not b: return 0.0
    return len(a & b)/len(a | b)

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
    DB.parent.mkdir(parents=True,exist_ok=True); os.chmod(DB.parent,0o700); c=sqlite3.connect(DB,timeout=15,factory=ClosingConnection); os.chmod(DB,0o600); c.row_factory=sqlite3.Row; c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA foreign_keys=ON')
    c.executescript(SCHEMA)
    _migrate(c)
    c.executescript(SCHEMA_INDEXES)
    return c
def _migrate(c):
    """Additive in-place migration; never recreates or drops user data.

    Multiple agent processes can start simultaneously on a fresh or upgraded
    database, so migration is serialized with an advisory file lock and each
    step re-checks before acting (a racing ALTER is a no-op, not an error).
    """
    lock_path=DB.parent/'migration.lock'
    DB.parent.mkdir(parents=True,exist_ok=True)
    import fcntl as _fcntl
    lock_fd=os.open(lock_path,os.O_CREAT|os.O_RDWR,0o600)
    try:
        _fcntl.flock(lock_fd,_fcntl.LOCK_EX)
        _migrate_inner(c)
    finally:
        _fcntl.flock(lock_fd,_fcntl.LOCK_UN); os.close(lock_fd)

def _migrate_inner(c):
    for table,columns in ADDED_COLUMNS.items():
        existing={row[1] for row in c.execute(f'PRAGMA table_info({table})')}
        if not existing: continue
        for name,decl in columns.items():
            if name not in existing:
                try:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {decl}')
                except sqlite3.OperationalError as exc:
                    # A concurrent process may have added the column between
                    # our check and our alter; that outcome is fine.
                    if 'duplicate column name' not in str(exc): raise
    # The FTS index gained a title column in 0.3.0. FTS5 tables cannot be
    # altered in place, so an index built by an older release is rebuilt from
    # the authoritative memories rows; no memory content is touched.
    fts_columns=[row[1] for row in c.execute('PRAGMA table_info(memories_fts)')]
    if fts_columns and 'title' not in fts_columns:
        c.executescript('''
        CREATE VIRTUAL TABLE memories_fts_rebuild USING fts5(memory_id UNINDEXED, project_id UNINDEXED, content, category, title);
        INSERT INTO memories_fts_rebuild(memory_id, project_id, content, category, title)
            SELECT m.id, m.project_id, m.content, m.category, COALESCE(m.title, '') FROM memories m;
        DROP TABLE memories_fts;
        ALTER TABLE memories_fts_rebuild RENAME TO memories_fts;
        ''')
    c.commit()
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
            # Distinct local clones of same remote get separate workspaces, stable
            # project identity is shared by remote. Concurrent agents may race on
            # the same identity; the conflict loser adopts the winner's row.
            pid=str(uuid.uuid4())
            c.execute('INSERT INTO projects VALUES(?,?,?,?,?,?,?,0) ON CONFLICT(identity) DO NOTHING',(pid,identity,name,str(root),remote,ts,ts))
            row=c.execute('SELECT id FROM projects WHERE identity=?',(identity,)).fetchone()
            pid=row['id']
        else: pid=row['id']; c.execute('UPDATE projects SET updated_at=?,root_path=?,remote_url=? WHERE id=?',(ts,str(root),remote,pid))
        wid=str(uuid.uuid5(uuid.NAMESPACE_URL,str(p))); c.execute('INSERT OR IGNORE INTO workspaces VALUES(?,?,?,?,?)',(wid,pid,str(p),common,ts))
        c.execute('INSERT OR IGNORE INTO agents VALUES(?,?,?)',('codex','Codex','codex'))
    return {'project_id':pid,'name':name,'root':str(root),'workspace':str(p),'remote_url':remote,'identity':identity}
def ensure_agent(c,agent):
    agent=str(agent or 'generic')[:64]
    c.execute('INSERT OR IGNORE INTO agents VALUES(?,?,?)',(agent,agent,agent))
    return agent
def event(payload):
    payload=dict(payload); path=payload.pop('cwd',None) or payload.pop('working_directory',None) or os.getcwd(); proj=project(path)
    eid=str(payload.get('id') or uuid.uuid4()); typ=str(payload.get('event_type') or payload.get('type') or 'activity'); ts=str(payload.get('occurred_at') or now()); agent=str(payload.get('agent') or 'codex'); sid=payload.get('session_id')
    payload_json=json.dumps(payload,sort_keys=True,ensure_ascii=False,default=str)
    cleaned,kinds=redact(payload_json)
    if kinds: payload=json.loads(cleaned)
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
    return {'event_id':eid,'project':proj,'persisted':True,'redaction_kinds':kinds}
def ensure_session(c,project_id,agent_id,external_session_id,occurred_at):
    if external_session_id in (None,''): return None
    external=str(external_session_id)
    # Scope client session IDs to a project so a reused external ID cannot join projects.
    sid=str(uuid.uuid5(uuid.NAMESPACE_URL,project_id+':'+external))
    # The caller may pass an internal session id (e.g. the sid returned by
    # start_session); accept it directly instead of deriving a phantom session.
    row=c.execute('SELECT id,project_id,agent_id FROM sessions WHERE id=? OR id=?',(sid,external)).fetchone()
    if not row:
        c.execute('INSERT OR IGNORE INTO sessions VALUES(?,?,?,?,?,?,NULL)',(sid,project_id,agent_id,occurred_at,None,'active'))
        return sid
    if row['project_id']!=project_id or row['agent_id']!=agent_id:
        raise ValueError('Session identity conflicts with this project.')
    return row['id']
def ingest(ev=None,digest=None):
    if ev:
        with connect() as c:
            agent=ensure_agent(c,ev.get('agent'))
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
            sql="SELECT m.*,bm25(memories_fts) rank FROM memories_fts JOIN memories m ON m.id=memories_fts.memory_id WHERE memories_fts MATCH ? AND m.status='active'"; args=[q]
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
def _memory_row(row):
    data=dict(row); data['tags']=json.loads(data.get('tags') or '[]'); keys=('id','project_id','category','title','content','importance','confidence','verification','status','tags','version','source_agent','source_session','supersedes','expires_at','created_at','updated_at')
    return {k:data[k] for k in keys if k in data}
def _fts_sync(c,memory_id,project_id,content,category,title):
    c.execute('DELETE FROM memories_fts WHERE memory_id=?',(memory_id,))
    c.execute('INSERT INTO memories_fts VALUES(?,?,?,?,?)',(memory_id,project_id,content,category,title or ''))
def _get_memory(c,memory_id,project_id=None):
    sql='SELECT * FROM memories WHERE id=?'; args=[memory_id]
    if project_id: sql+=' AND project_id=?'; args.append(project_id)
    row=c.execute(sql,args).fetchone()
    if not row: raise ValueError(f'Memory not found: {memory_id}')
    return row
def remember(a):
    """Capture one memory. Idempotent per (project, category, content digest);
    content is redacted before persistence; creation is versioned."""
    p=project(a.get('cwd') or os.getcwd())
    content=a.get('content')
    if not isinstance(content,str) or not content.strip(): raise ValueError('Memory content is required.')
    category=_canonical_category(a.get('category') or 'project_context')
    if not category: raise ValueError(f'Category must be one of: {", ".join(sorted(ALL_CATEGORIES))}.')
    verification=a.get('verification') or 'proposed'
    if verification not in VERIFICATION_STATUSES: raise ValueError(f'Verification must be one of: {", ".join(sorted(VERIFICATION_STATUSES))}.')
    importance=max(0.0,min(1.0,float(a.get('importance',0.5)))); confidence=max(0.0,min(1.0,float(a.get('confidence',0.5))))
    title=str(a.get('title') or '')[:300]; tags=sorted({str(t)[:64] for t in (a.get('tags') or []) if str(t).strip()})
    redacted,kinds=redact(content)
    digest=hashlib.sha256((p['project_id']+'|'+category+'|'+' '.join(redacted.split())).encode()).hexdigest()
    agent=a.get('agent') or 'generic'; ts=now()
    with connect() as c:
        agent_id=ensure_agent(c,agent)
        existing=c.execute("SELECT * FROM memories WHERE project_id=? AND content_digest=? AND status='active' ORDER BY updated_at DESC LIMIT 1",(p['project_id'],digest)).fetchone()
        if existing:
            c.execute('UPDATE memories SET importance=?,confidence=?,updated_at=? WHERE id=?',(importance,confidence,ts,existing['id']))
            row=_get_memory(c,existing['id'])
            result={'memory':_memory_row(row),'deduplicated':True,'redaction_kinds':kinds}
        else:
            mid=str(uuid.uuid4()); supersedes=a.get('supersedes')
            if supersedes:
                prior=_get_memory(c,str(supersedes),p['project_id'])
                c.execute('INSERT INTO memory_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),prior['id'],prior['version'],prior['content'],prior['title'],prior['importance'],prior['confidence'],prior['verification'],prior['status'],ts,agent_id))
                c.execute("UPDATE memories SET status='superseded',verification='superseded',updated_at=? WHERE id=?",(ts,prior['id']))
            c.execute('INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(mid,p['project_id'],category,redacted,importance,ts,ts,title,agent_id,a.get('session_id'),confidence,verification,'active',json.dumps(tags),1,digest,str(supersedes) if supersedes else None,a.get('expires_at'),agent_id))
            _fts_sync(c,mid,p['project_id'],redacted,category,title)
            row=_get_memory(c,mid)
            result={'memory':_memory_row(row),'deduplicated':False,'superseded':str(supersedes) if supersedes else None,'redaction_kinds':kinds}
        c.commit()
    event({'cwd':p['root'],'event_type':'memory.written','agent':agent,'session_id':a.get('session_id'),'payload':{'memory_id':result['memory']['id'],'category':category,'deduplicated':result['deduplicated'],'verification':verification,'redaction_kinds':kinds}})
    return result
def update_memory(a):
    """Mutate one memory and keep the previous state as a version record."""
    ts=now(); agent=a.get('agent') or 'generic'
    with connect() as c:
        agent_id=ensure_agent(c,agent)
        row=_get_memory(c,a['memory_id'])
        fields={}
        if 'content' in a and isinstance(a['content'],str) and a['content'].strip():
            redacted,kinds=redact(a['content']); fields['content']=redacted
            fields['content_digest']=hashlib.sha256((row['project_id']+'|'+row['category']+'|'+' '.join(redacted.split())).encode()).hexdigest()
        if 'title' in a and isinstance(a['title'],str): fields['title']=a['title'][:300]
        if 'importance' in a: fields['importance']=max(0.0,min(1.0,float(a['importance'])))
        if 'confidence' in a: fields['confidence']=max(0.0,min(1.0,float(a['confidence'])))
        if 'verification' in a:
            if a['verification'] not in VERIFICATION_STATUSES: raise ValueError(f'Verification must be one of: {", ".join(sorted(VERIFICATION_STATUSES))}.')
            fields['verification']=a['verification']
        if 'status' in a:
            if a['status'] not in MEMORY_STATUSES: raise ValueError(f'Status must be one of: {", ".join(sorted(MEMORY_STATUSES))}.')
            fields['status']=a['status']
        if 'tags' in a and isinstance(a['tags'],list): fields['tags']=json.dumps(sorted({str(t)[:64] for t in a['tags'] if str(t).strip()}))
        if not fields: raise ValueError('No supported fields to update.')
        c.execute('INSERT INTO memory_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),row['id'],row['version'],row['content'],row['title'],row['importance'],row['confidence'],row['verification'],row['status'],ts,agent_id))
        assignments=','.join(f'{k}=?' for k in fields)
        c.execute(f'UPDATE memories SET {assignments},version=version+1,updated_at=? WHERE id=?',(*fields.values(),ts,row['id']))
        updated=_get_memory(c,row['id'])
        _fts_sync(c,row['id'],row['project_id'],updated['content'],updated['category'],updated['title'])
        c.commit()
        result={'memory':_memory_row(updated),'previous_version':row['version']}
    event({'cwd':a.get('cwd') or os.getcwd(),'event_type':'memory.updated','agent':agent,'payload':{'memory_id':row['id'],'fields':sorted(fields)}})
    return result
def link_memories(a):
    relation=str(a.get('relation') or 'related')
    with connect() as c:
        _get_memory(c,a['from_memory']); _get_memory(c,a['to_memory'])
        rid=str(uuid.uuid5(uuid.NAMESPACE_URL,'|'.join(sorted([a['from_memory'],a['to_memory']]))+f'|{relation}'))
        c.execute('INSERT OR IGNORE INTO memory_relationships VALUES(?,?,?,?)',(rid,a['from_memory'],a['to_memory'],relation))
        c.commit()
    return {'relationship_id':rid,'relation':relation}
def archive_memory(a):
    return update_memory({'memory_id':a['memory_id'],'status':'archived','agent':a.get('agent'),'cwd':a.get('cwd')})
def memory_history(a):
    with connect() as c:
        row=_get_memory(c,a['memory_id'])
        versions=[dict(v) for v in c.execute('SELECT version,content,title,importance,confidence,verification,status,replaced_at,actor FROM memory_versions WHERE memory_id=? ORDER BY version DESC',(row['id'],))]
        relations=[dict(r) for r in c.execute('SELECT * FROM memory_relationships WHERE from_memory=? OR to_memory=?',(row['id'],row['id']))]
    return {'memory_id':row['id'],'current_version':row['version'],'versions':versions,'relationships':relations}
def list_memories_tool(a):
    pid=project(a['cwd'])['project_id'] if a.get('cwd') else a.get('project_id')
    with connect() as c:
        sql="SELECT * FROM memories WHERE status='active'"; args=[]
        if pid: sql+=' AND project_id=?'; args.append(pid)
        if a.get('category'):
            if a['category'] not in ALL_CATEGORIES: raise ValueError('Unknown category.')
            sql+=' AND category=?'; args.append(a['category'])
        sql+=' ORDER BY importance DESC, updated_at DESC LIMIT ?'; args.append(int(a.get('limit',20)))
        return [_memory_row(r) for r in c.execute(sql,args)]
def list_tasks_tool(a):
    pid=project(a['cwd'])['project_id'] if a.get('cwd') else a.get('project_id')
    with connect() as c:
        sql='SELECT * FROM tasks'; args=[]
        if pid: sql+=' WHERE project_id=?'; args.append(pid)
        if a.get('status'): sql+=' AND status=?'; args.append(a['status'])
        sql+=' ORDER BY CASE priority WHEN \'critical\' THEN 0 WHEN \'high\' THEN 1 WHEN \'normal\' THEN 2 ELSE 3 END, updated_at DESC LIMIT ?'; args.append(int(a.get('limit',50)))
        return [dict(r) for r in c.execute(sql,args)]
def get_project_progress(a):
    p=project(a.get('cwd') or os.getcwd())
    with connect() as c:
        counts={row[0]:row[1] for row in c.execute('SELECT status,count(*) FROM tasks WHERE project_id=? GROUP BY status',(p['project_id'],))}
        decisions=c.execute('SELECT count(*) FROM decisions WHERE project_id=?',(p['project_id'],)).fetchone()[0]
        open_errors=c.execute("SELECT count(*) FROM errors WHERE project_id=? AND status='OPEN'",(p['project_id'],)).fetchone()[0]
        memories=c.execute("SELECT count(*) FROM memories WHERE project_id=? AND status='active'",(p['project_id'],)).fetchone()[0]
        last_event=c.execute('SELECT occurred_at FROM activity_events WHERE project_id=? ORDER BY occurred_at DESC LIMIT 1',(p['project_id'],)).fetchone()
    total=sum(counts.values())
    return {'project':p,'tasks':counts,'total_tasks':total,'decisions':decisions,'open_errors':open_errors,'active_memories':memories,'last_activity':last_event[0] if last_event else None,'incomplete':counts.get('PLANNED',0)+counts.get('IN_PROGRESS',0)+counts.get('BLOCKED',0)}
def record_solution(a):
    p=project(a.get('cwd') or os.getcwd()); ts=now(); agent=a.get('agent') or 'generic'
    with connect() as c:
        agent_id=ensure_agent(c,agent)
        error_row=c.execute('SELECT * FROM errors WHERE id=?',(a['error_id'],)).fetchone()
        if not error_row: raise ValueError(f'Error not found: {a["error_id"]}')
        redacted,kinds=redact(str(a.get('resolution','')))
        rid=str(uuid.uuid4())
        c.execute('INSERT INTO error_resolutions VALUES(?,?,?,?,?,?,?)',(rid,a['error_id'],redacted,a.get('evidence'),1 if a.get('verified') else 0,ts,agent_id))
        c.execute("UPDATE errors SET status='RESOLVED' WHERE id=?",(a['error_id'],))
        c.commit()
    event({'cwd':p['root'],'event_type':'error.solution_recorded','agent':agent,'payload':{'error_id':a['error_id'],'resolution_id':rid,'verified':bool(a.get('verified')),'redaction_kinds':kinds}})
    return {'resolution_id':rid,'verified':bool(a.get('verified')),'redaction_kinds':kinds}
def search_solutions(a):
    q=str(a.get('query','')).strip()
    with connect() as c:
        rows=[dict(r) for r in c.execute('SELECT r.*,e.summary AS error_summary,e.project_id FROM error_resolutions r JOIN errors e ON e.id=r.error_id ORDER BY r.created_at DESC LIMIT 100')]
    if q:
        ql=q.lower()
        rows=[r for r in rows if ql in (r['resolution'] or '').lower() or ql in (r['error_summary'] or '').lower()]
    if a.get('cwd'):
        pid=project(a['cwd'])['project_id']
        rows=[r for r in rows if r['project_id']==pid]
    return rows[:int(a.get('limit',20))]
def start_session(a):
    p=project(a.get('cwd') or os.getcwd()); ts=now(); agent=a.get('agent') or 'generic'
    with connect() as c:
        agent_id=ensure_agent(c,agent)
        external=a.get('session_id')
        sid=str(uuid.uuid5(uuid.NAMESPACE_URL,p['project_id']+':'+str(external))) if external else str(uuid.uuid4())
        row=c.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
        if not (row and row['status']=='active'):
            c.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?,?,?,?,NULL)',(sid,p['project_id'],agent_id,ts,None,'active'))
        c.execute('INSERT OR REPLACE INTO agent_settings VALUES(?,?,?,?)',(agent_id,'current_project',p['project_id'],ts))
        c.commit()
    event({'cwd':p['root'],'event_type':'session.started','agent':agent,'session_id':sid,'payload':{'explicit':bool(external)}})
    return {'session_id':sid,'project_id':p['project_id'],'resumed':bool(row and row['status']=='active')}
def end_session(a):
    ts=now(); kinds=[]
    with connect() as c:
        # The session owner ends the session: inherit its agent unless the
        # caller states one explicitly, so the identity check stays consistent.
        if a.get('session_id'):
            row=c.execute('SELECT agent_id FROM sessions WHERE id=?',(a['session_id'],)).fetchone()
            agent=a.get('agent') or (row['agent_id'] if row else 'generic')
        else:
            agent=a.get('agent') or 'generic'
    summary_text=''
    if a.get('summary'):
        summary_text,kinds=redact(str(a['summary']))
        result=remember({'cwd':a.get('cwd'),'category':'session_summary','content':a['summary'],'title':'Session summary','verification':'observed','importance':0.6,'agent':agent,'session_id':a.get('session_id')})
    with connect() as c:
        if a.get('session_id'):
            c.execute("UPDATE sessions SET ended_at=?,status='ended',summary=? WHERE id=? AND status='active'",(ts,summary_text[:2000] or None,a['session_id']))
            ended=c.execute('SELECT changes()').fetchone()[0]
        else:
            ended=0
        c.commit()
    checkpoint_id=None
    if a.get('checkpoint_state') is not None and ended:
        cp=call('memory_save_checkpoint',{'cwd':a.get('cwd'),'summary':'Auto-checkpoint at session end','state':a['checkpoint_state'],'session_id':a.get('session_id'),'agent':agent})
        checkpoint_id=cp['checkpoint_id']
    return {'ended':bool(ended),'summary_memory':result['memory']['id'] if a.get('summary') else None,'checkpoint_id':checkpoint_id,'redaction_kinds':kinds}
def get_recent_sessions(a):
    pid=project(a['cwd'])['project_id'] if a.get('cwd') else a.get('project_id')
    with connect() as c:
        sql='SELECT s.*,a.name AS agent_name FROM sessions s JOIN agents a ON a.id=s.agent_id'; args=[]
        if pid: sql+=' WHERE s.project_id=?'; args.append(pid)
        sql+=' ORDER BY s.started_at DESC LIMIT ?'; args.append(int(a.get('limit',10)))
        return [dict(r) for r in c.execute(sql,args)]
def restore_checkpoint(a):
    with connect() as c:
        if a.get('checkpoint_id'):
            row=c.execute('SELECT * FROM checkpoints WHERE id=?',(a['checkpoint_id'],)).fetchone()
        else:
            pid=project(a['cwd'])['project_id'] if a.get('cwd') else a.get('project_id')
            if not pid: raise ValueError('Provide a checkpoint_id, cwd, or project_id.')
            row=c.execute('SELECT * FROM checkpoints WHERE project_id=? ORDER BY created_at DESC LIMIT 1',(pid,)).fetchone()
        if not row: raise ValueError('No checkpoint found.')
        try: state=json.loads(row['state'])
        except (ValueError,TypeError): raise ValueError('Checkpoint state is corrupted; refusing to restore.')
    event({'cwd':a.get('cwd') or os.getcwd(),'event_type':'checkpoint.restored','agent':a.get('agent') or 'generic','payload':{'checkpoint_id':row['id']}})
    return {'checkpoint_id':row['id'],'summary':row['summary'],'created_at':row['created_at'],'state':state}
def record_agent_activity(a):
    p=project(a.get('cwd') or os.getcwd()); agent=a.get('agent') or 'generic'
    payload={'operation':str(a.get('operation') or 'unknown')[:120],'target':str(a.get('target') or '')[:300],'result':str(a.get('result') or '')[:120],'status':str(a.get('status') or 'completed')[:40]}
    if a.get('details'): payload['details']=str(a['details'])[:2000]
    ev=event({'cwd':p['root'],'event_type':'agent.activity','agent':agent,'session_id':a.get('session_id'),'payload':payload})
    return {'event_id':ev['event_id'],'redaction_kinds':ev['redaction_kinds']}
def get_agent_activity(a):
    pid=a.get('project_id') or (project(a['cwd'])['project_id'] if a.get('cwd') else None)
    with connect() as c:
        sql='SELECT e.event_type,e.occurred_at,e.payload,a.name AS agent,p.name AS project_name FROM activity_events e JOIN agents a ON a.id=e.agent_id JOIN projects p ON p.id=e.project_id'; args=[]
        clauses=[]
        if a.get('agent'):
            aid=ensure_agent(c,a['agent']); clauses.append('e.agent_id=?'); args.append(aid)
        if pid: clauses.append('e.project_id=?'); args.append(pid)
        if clauses: sql+=' WHERE '+' AND '.join(clauses)
        sql+=' ORDER BY e.occurred_at DESC LIMIT ?'; args.append(int(a.get('limit',30)))
        rows=[]
        for r in c.execute(sql,args):
            item=dict(r)
            try:
                parsed=json.loads(item['payload'])
            except (ValueError,TypeError):
                parsed=item['payload']
            # Historical rows wrap the caller payload in an event envelope;
            # unwrap it so consumers see the structured operation record.
            if isinstance(parsed,dict) and isinstance(parsed.get('payload'),dict):
                parsed=parsed['payload']
            item['payload']=parsed
            rows.append(item)
        return rows
def register_project(a):
    path=Path(a['path']).expanduser().resolve(strict=True)
    if not path.is_dir(): raise ValueError('Project path must be a directory.')
    p=project(str(path))
    if a.get('name'):
        with connect() as c:
            c.execute('UPDATE projects SET name=?,updated_at=? WHERE id=?',(str(a['name'])[:200],now(),p['project_id'])); c.commit()
    return project(str(path))
def switch_project(a):
    agent=a.get('agent') or 'generic'; ts=now()
    if a.get('project_id'):
        with connect() as c:
            row=c.execute('SELECT id FROM projects WHERE id=?',(a['project_id'],)).fetchone()
            if not row: raise ValueError(f'Unknown project: {a["project_id"]}')
            agent_id=ensure_agent(c,agent)
            c.execute('INSERT OR REPLACE INTO agent_settings VALUES(?,?,?,?)',(agent_id,'current_project',a['project_id'],ts)); c.commit()
        return {'agent':agent,'current_project':a['project_id'],'explicit':True}
    if a.get('cwd'):
        p=project(a['cwd'])
        with connect() as c:
            agent_id=ensure_agent(c,agent)
            c.execute('INSERT OR REPLACE INTO agent_settings VALUES(?,?,?,?)',(agent_id,'current_project',p['project_id'],ts)); c.commit()
        return {'agent':agent,'current_project':p['project_id'],'explicit':True}
    raise ValueError('Provide project_id or cwd.')
def current_project(a):
    agent=a.get('agent') or 'generic'
    with connect() as c:
        agent_id=ensure_agent(c,agent)
        row=c.execute("SELECT value FROM agent_settings WHERE agent_id=? AND key='current_project'",(agent_id,)).fetchone()
        if row:
            proj=c.execute('SELECT * FROM projects WHERE id=? AND archived=0',(row['value'],)).fetchone()
            if proj:
                return {'project_id':proj['id'],'name':proj['name'],'root':proj['root_path'],'remote_url':proj['remote_url'],'source':'explicit_selection'}
    p=project(a.get('cwd') or os.getcwd())
    p['source']='detected_from_working_directory'
    return p
def get_project_context(a):
    p=project(a.get('cwd') or os.getcwd()) if a.get('cwd') else current_project(a)
    s=summary(p['root'])
    with connect() as c:
        context_memories=[_memory_row(r) for r in c.execute("SELECT * FROM memories WHERE project_id=? AND status='active' AND category IN ('project_context','architecture','user_preference') ORDER BY importance DESC, updated_at DESC LIMIT 15",(p['project_id'],))]
        decisions=[dict(r) for r in c.execute('SELECT id,summary,rationale,created_at FROM decisions WHERE project_id=? ORDER BY created_at DESC LIMIT 10',(p['project_id'],))]
        open_errors=[dict(r) for r in c.execute("SELECT id,summary,status,occurred_at FROM errors WHERE project_id=? AND status='OPEN' ORDER BY occurred_at DESC LIMIT 10",(p['project_id'],))]
        sessions=[dict(r) for r in c.execute('SELECT id,started_at,ended_at,status FROM sessions WHERE project_id=? ORDER BY started_at DESC LIMIT 5',(p['project_id'],))]
    return {'project':p,'incomplete_tasks':[t for t in s['tasks'] if t['status'] not in ('VERIFIED','REVERTED','COMPLETED')],'latest_checkpoint':s['checkpoint'],'recent_activity':s['recent_activity'][:10],'context_memories':context_memories,'recent_decisions':decisions,'open_errors':open_errors,'recent_sessions':sessions}
def _canonical_category(category):
    """Accept legacy and v0.4 category names; return the stored value.
    Legacy names map onto their canonical v0.4 equivalents."""
    value=str(category or '')
    if value in CATEGORY_ALIASES: return CATEGORY_ALIASES[value]
    if value in ALL_CATEGORIES: return value
    return None

def find_duplicate_memories(a):
    """Report exact and near-duplicate memory candidates without changing them."""
    pid=project(a['cwd'])['project_id'] if a.get('cwd') else a.get('project_id')
    threshold=float(a.get('threshold',0.7))
    pairs=[]
    with connect() as c:
        rows=[dict(r) for r in c.execute("SELECT * FROM memories WHERE status='active'" + (" AND project_id=?" if pid else ""), (pid,) if pid else ())]
    exact_seen={}
    for row in rows:
        key=(row['project_id'], row['category'], row.get('content_digest') or hashlib.sha256(' '.join((row['content'] or '').split()).encode()).hexdigest())
        if key in exact_seen:
            pairs.append({'type':'exact','ids':[exact_seen[key],row['id']],'similarity':1.0,'titles':[None,row['title']]})
        else:
            exact_seen[key]=row['id']
    # Near-duplicates: compare within project+category groups only.
    groups={}
    for row in rows: groups.setdefault((row['project_id'],row['category']),[]).append(row)
    for (project_id,category),members in groups.items():
        for i in range(len(members)):
            for j in range(i+1,len(members)):
                sim=similarity(members[i]['content'],members[j]['content'])
                if threshold<=sim<1.0:
                    pairs.append({'type':'near_duplicate','ids':[members[i]['id'],members[j]['id']],'similarity':round(sim,3),'titles':[members[i]['title'],members[j]['title']],'project_id':project_id,'category':category})
    return {'candidates':pairs,'count':len(pairs),'threshold':threshold,'note':'Detection never changes records; review then consolidate or update manually.'}

def consolidate_memories(a):
    """Merge a secondary memory into a primary one safely:
    the primary gains a new version containing both contents; the secondary is
    superseded and linked, preserving every original ID and version."""
    primary_id,secondary_id=a['primary_id'],a['secondary_id']
    if primary_id==secondary_id: raise ValueError('Cannot consolidate a memory with itself.')
    ts=now(); agent=a.get('agent') or 'generic'
    with connect() as c:
        agent_id=ensure_agent(c,agent)
        primary=_get_memory(c,primary_id); secondary=_get_memory(c,secondary_id)
        if primary['project_id']!=secondary['project_id']:
            raise ValueError('Consolidation refused: the memories belong to different projects.')
        merged=(primary['content'].rstrip()+'\n\n[consolidated from '+secondary_id[:8]+'] '+secondary['content'].strip())
        merged_redacted,kinds=redact(merged)
        c.execute('INSERT INTO memory_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),primary['id'],primary['version'],primary['content'],primary['title'],primary['importance'],primary['confidence'],primary['verification'],primary['status'],ts,agent_id))
        new_digest=hashlib.sha256((primary['project_id']+'|'+primary['category']+'|'+' '.join(merged_redacted.split())).encode()).hexdigest()
        c.execute('UPDATE memories SET content=?,content_digest=?,version=version+1,updated_at=? WHERE id=?',(merged_redacted,new_digest,ts,primary['id']))
        # Secondary keeps its ID and history but is retired with a link.
        c.execute("UPDATE memories SET status='superseded',verification='superseded',supersedes=?,updated_at=? WHERE id=?",(primary_id,ts,secondary_id))
        rid=str(uuid.uuid5(uuid.NAMESPACE_URL,'|'.join(sorted([primary_id,secondary_id]))+'|consolidated'))
        c.execute('INSERT OR IGNORE INTO memory_relationships VALUES(?,?,?,?)',(rid,secondary_id,primary_id,'consolidated_into'))
        updated=_get_memory(c,primary_id)
        _fts_sync(c,primary['id'],primary['project_id'],updated['content'],updated['category'],updated['title'])
        c.commit()
        result={'memory':_memory_row(updated),'consolidated_from':secondary_id,'redaction_kinds':kinds}
    event({'cwd':a.get('cwd') or os.getcwd(),'event_type':'memory.consolidated','agent':agent,'payload':{'primary':primary_id,'secondary':secondary_id}})
    return result

def verify_memory(a):
    """Mark a memory verified with evidence; claims alone never verify."""
    evidence=a.get('evidence')
    if not evidence or not str(evidence).strip():
        raise ValueError('Verification requires evidence (test run, command result, reproduction check, or human confirmation).')
    return update_memory({'memory_id':a['memory_id'],'verification':'verified','agent':a.get('agent'),'cwd':a.get('cwd'),'content':a.get('content') if a.get('content') else None}) | {'evidence':str(evidence)[:500]}

def get_project_health(a):
    """Project health: progress, staleness, isolation-safe counts, storage use."""
    p=project(a.get('cwd') or os.getcwd())
    with connect() as c:
        tasks={row[0]:row[1] for row in c.execute('SELECT status,count(*) FROM tasks WHERE project_id=? GROUP BY status',(p['project_id'],))}
        open_errors=c.execute("SELECT count(*) FROM errors WHERE project_id=? AND status='OPEN'",(p['project_id'],)).fetchone()[0]
        verified_solutions=c.execute("SELECT count(*) FROM error_resolutions WHERE verified=1 AND error_id IN (SELECT id FROM errors WHERE project_id=?)",(p['project_id'],)).fetchone()[0]
        memory_count=c.execute("SELECT count(*) FROM memories WHERE project_id=? AND status='active'",(p['project_id'],)).fetchone()[0]
        superseded=c.execute("SELECT count(*) FROM memories WHERE project_id=? AND status='superseded'",(p['project_id'],)).fetchone()[0]
        last_event=c.execute('SELECT occurred_at FROM activity_events WHERE project_id=? ORDER BY occurred_at DESC LIMIT 1',(p['project_id'],)).fetchone()
        unverified_claims=c.execute("SELECT count(*) FROM memories WHERE project_id=? AND verification='proposed'",(p['project_id'],)).fetchone()[0]
        session_count=c.execute("SELECT count(*) FROM sessions WHERE project_id=? AND status='active'",(p['project_id'],)).fetchone()[0]
    now_dt=dt.datetime.now(dt.timezone.utc)
    stale_days=None
    if last_event:
        try: stale_days=round((now_dt-dt.datetime.fromisoformat(last_event[0])).total_seconds()/86400,1)
        except ValueError: stale_days=None
    incomplete=tasks.get('PLANNED',0)+tasks.get('IN_PROGRESS',0)+tasks.get('TODO',0)+tasks.get('BLOCKED',0)
    health='active' if stale_days is not None and stale_days<7 else ('stale' if stale_days is not None else 'unknown')
    return {'project':p,'health':health,'days_since_last_activity':stale_days,'tasks':tasks,'incomplete_tasks':incomplete,'open_errors':open_errors,'verified_solutions':verified_solutions,'active_memories':memory_count,'superseded_memories':superseded,'unverified_claims':unverified_claims,'active_sessions':session_count}

def build_project_context_manifest(a):
    """Write human-readable project projections under the Memory OS projects
    directory; budgets keep each projection bounded and relevant."""
    p=project(a.get('cwd') or os.getcwd())
    budget=int(a.get('budget',40))
    base=ROOT/'projects'/p['project_id']
    for sub in ('context','decisions','tasks','errors','solutions','sessions','checkpoints','reports'):
        (base/sub).mkdir(parents=True,exist_ok=True,mode=0o700)
    written=[]
    def write(rel, title, lines):
        path=base/rel
        body='# '+title+'\n\n'+'\n'.join(lines[:budget])+('\n' if lines else '\n(no records yet)\n')
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,prefix='.proj-',delete=False) as stream:
            stream.write(body); stream.flush(); os.fsync(stream.fileno()); tmp=Path(stream.name)
        tmp.chmod(0o600); os.replace(tmp,path)
        written.append(str(path))
    with connect() as c:
        overview=[f"- Name: {p['name']}", f"- Root: {p['root']}", f"- Remote: {p.get('remote_url') or 'none'}", f"- Identity: {p['identity']}", f"- Workspace: {p['workspace']}"]
        write('context/overview.md','Project overview',overview)
        health=get_project_health({'cwd':p['root']})
        write('context/current-state.md','Current state',[f"- Health: {health['health']} (last activity {health['days_since_last_activity']} days ago)", f"- Incomplete tasks: {health['incomplete_tasks']}", f"- Open errors: {health['open_errors']}", f"- Active memories: {health['active_memories']}"])
        important=[f"- [{m['verification']}/{m['status']}] ({m['id'][:8]}) {m['title'] or ''}: {m['content'][:200]}" for m in c.execute("SELECT * FROM memories WHERE project_id=? AND status='active' ORDER BY importance DESC, updated_at DESC LIMIT ?",(p['project_id'],budget))]
        write('context/important-context.md','Important context (ranked by importance, then recency)',important)
        write('decisions/index.md','Decisions',[f"- ({r['created_at'][:10]}) {r['summary'][:200]}" for r in c.execute('SELECT * FROM decisions WHERE project_id=? ORDER BY created_at DESC LIMIT ?',(p['project_id'],budget))])
        write('tasks/index.md','Tasks',[f"- [{r['status']}] {r['title'][:160]} ({r['updated_at'][:10]})" for r in c.execute('SELECT * FROM tasks WHERE project_id=? ORDER BY updated_at DESC LIMIT ?',(p['project_id'],budget))])
        write('errors/index.md','Errors',[f"- [{r['status']}] {r['summary'][:160]} ({r['occurred_at'][:10]})" for r in c.execute('SELECT * FROM errors WHERE project_id=? ORDER BY occurred_at DESC LIMIT ?',(p['project_id'],budget))])
        write('solutions/index.md','Solutions',[f"- [verified={r['verified']}] {r['resolution'][:180]}" for r in c.execute('SELECT r.*,e.project_id FROM error_resolutions r JOIN errors e ON e.id=r.error_id WHERE e.project_id=? ORDER BY r.created_at DESC LIMIT ?',(p['project_id'],budget))])
        write('sessions/index.md','Sessions',[f"- [{r['status']}] {r['agent_id']} {r['started_at'][:19]}" for r in c.execute('SELECT * FROM sessions WHERE project_id=? ORDER BY started_at DESC LIMIT ?',(p['project_id'],budget))])
        write('checkpoints/index.md','Checkpoints',[f"- {r['summary'][:160]} ({r['created_at'][:19]})" for r in c.execute('SELECT * FROM checkpoints WHERE project_id=? ORDER BY created_at DESC LIMIT ?',(p['project_id'],budget))])
    return {'project_id':p['project_id'],'files':written,'budget':budget}

def recover_interrupted_sessions(a):
    """Mark long-running active sessions as interrupted so the next agent can
    distinguish incomplete work from completed sessions."""
    hours=int(a.get('stale_after_hours',12))
    cutoff=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=hours)).isoformat()
    with connect() as c:
        rows=[dict(r) for r in c.execute("SELECT id,project_id,agent_id,started_at FROM sessions WHERE status='active' AND started_at < ?",(cutoff,))]
        for row in rows:
            c.execute("UPDATE sessions SET status='interrupted',ended_at=? WHERE id=?",(now(),row['id']))
        c.commit()
    return {'interrupted':[r['id'] for r in rows],'count':len(rows),'note':'Interrupted sessions keep their checkpoint and unfinished tasks visible for safe resumption.'}

def get_session_summary(a):
    with connect() as c:
        row=c.execute('SELECT * FROM sessions WHERE id=?',(a['session_id'],)).fetchone()
        if not row: raise ValueError(f'Session not found: {a["session_id"]}')
        task_events=[dict(r) for r in c.execute("SELECT event_type,occurred_at,payload FROM activity_events WHERE session_id=? AND event_type LIKE 'memory.task%' ORDER BY occurred_at DESC LIMIT 20",(row['id'],))]
        errors=[dict(r) for r in c.execute("SELECT id,summary,status FROM errors WHERE session_id=? ORDER BY occurred_at DESC LIMIT 10",(row['id'],))]
        checkpoint=c.execute('SELECT * FROM checkpoints WHERE session_id=? ORDER BY created_at DESC LIMIT 1',(row['id'],)).fetchone()
        memories=[_memory_row(r) for r in c.execute("SELECT * FROM memories WHERE source_session=? ORDER BY created_at DESC LIMIT 10",(row['id'],))]
    return {'session':dict(row),'task_activity':task_events,'errors':errors,'checkpoint':dict(checkpoint) if checkpoint else None,'memories_from_session':memories}

def complete_task(a):
    """Complete a task only with evidence; claims never complete tasks."""
    evidence=a.get('evidence')
    if not evidence or not str(evidence).strip():
        raise ValueError('Task completion requires evidence (test result, command output, or verification record).')
    with connect() as c:
        row=c.execute('SELECT p.root_path, p.id FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.id=?',(a['task_id'],)).fetchone()
        if not row: raise ValueError('Task not found.')
        project_id=row['id']
        c.execute("UPDATE tasks SET status='COMPLETED',updated_at=? WHERE id=?",(now(),a['task_id']))
        c.execute('INSERT INTO verification_results VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid4()),project_id,a['task_id'],a.get('command'),1 if a.get('exit_code')==0 else None,str(evidence)[:500],now()))
        task_id=a['task_id']
    event({'cwd':row['root_path'],'event_type':'memory.task.completed','agent':a.get('agent') or 'generic','payload':{'task_id':task_id,'evidence':str(evidence)[:200]}})
    return {'task_id':task_id,'status':'COMPLETED','evidence_recorded':True}

def add_task_dependency(a):
    tid,dep=a['task_id'],a['depends_on_task']
    if tid==dep: raise ValueError('A task cannot depend on itself.')
    ts=now()
    with connect() as c:
        for task_id in (tid,dep):
            if not c.execute('SELECT 1 FROM tasks WHERE id=?',(task_id,)).fetchone(): raise ValueError(f'Task not found: {task_id}')
        # Cycle check: following dependencies from dep must never reach tid.
        seen=set(); frontier=[dep]
        while frontier:
            current=frontier.pop()
            if current==tid: raise ValueError('Refusing dependency cycle.')
            if current in seen: continue
            seen.add(current)
            frontier.extend(r[0] for r in c.execute('SELECT depends_on_task FROM task_dependencies WHERE task_id=?',(current,)))
        did=str(uuid.uuid5(uuid.NAMESPACE_URL,tid+'|'+dep))
        c.execute('INSERT OR IGNORE INTO task_dependencies VALUES(?,?,?,?)',(did,tid,dep,ts))
        c.commit()
    return {'dependency_id':did,'task_id':tid,'depends_on_task':dep}

def get_agent_history(a):
    """Aggregated per-agent history: sessions, activity kinds, projects touched."""
    with connect() as c:
        agent_id=ensure_agent(c,a.get('agent') or 'generic')
        sessions=[dict(r) for r in c.execute('SELECT s.*,p.name AS project_name FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.agent_id=? ORDER BY s.started_at DESC LIMIT 20',(agent_id,))]
        kinds={row[0]:row[1] for row in c.execute('SELECT event_type,count(*) FROM activity_events WHERE agent_id=? GROUP BY event_type ORDER BY count(*) DESC LIMIT 15',(agent_id,))}
        projects=[dict(r) for r in c.execute('SELECT DISTINCT p.id,p.name FROM activity_events e JOIN projects p ON p.id=e.project_id WHERE e.agent_id=? LIMIT 20',(agent_id,))]
    return {'agent':a.get('agent') or 'generic','sessions':sessions,'activity_kinds':kinds,'projects':projects}

def storage_status():
    """Local storage intelligence (read-only)."""
    def size(path):
        try:
            if path.is_file(): return path.stat().st_size
            return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
        except OSError: return 0
    data={'database_bytes':size(DB),'journal_bytes':size(JOURNAL),'checkpoints_bytes':size(ROOT/'storage/checkpoints'),'backups_bytes':size(ROOT/'storage/backups'),'projects_projections_bytes':size(ROOT/'projects')}
    data['total_local_bytes']=sum(v for k,v in data.items() if k.endswith('_bytes'))
    try:
        with connect() as c:
            data['database_integrity']=c.execute('PRAGMA integrity_check').fetchone()[0]
            data['memory_rows']=c.execute('SELECT count(*) FROM memories').fetchone()[0]
            data['event_rows']=c.execute('SELECT count(*) FROM activity_events').fetchone()[0]
    except Exception as exc:
        data['database_integrity']='unavailable ('+type(exc).__name__+')'
    backup_cfg=ROOT/'config/backup.json'
    data['backup']={'configured':backup_cfg.exists(),'repository':None,'visibility_note':'uploads refuse public repositories'}
    if backup_cfg.exists():
        try: data['backup']['repository']=json.loads(backup_cfg.read_text()).get('repository')
        except ValueError: pass
    r2=_r2().get_r2_config()
    data['r2']={'enabled':bool(r2.get('MEMORYOS_R2_ENABLED')=='true'),'configured':bool(r2.get('MEMORYOS_R2_BUCKET')),'note':'credentials never displayed'}
    return data

def _r2():
    """Lazy R2 module access; the storage layer is optional by design."""
    import sys as _sys
    if str(Path(__file__).resolve().parent) not in _sys.path:
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import memoryos_r2
    return memoryos_r2

def verify_storage_integrity(a):
    """Read-only integrity pass over the local store."""
    storage_report=storage_status()
    report=storage_report
    checks={'database_integrity':report.get('database_integrity')}
    journal_ok=JOURNAL.exists()
    journal_lines=0
    if journal_ok:
        try: journal_lines=sum(1 for line in JOURNAL.read_text(encoding='utf-8').splitlines() if line.strip())
        except OSError: journal_ok=False
    checks['journal_present']=journal_ok
    checks['journal_lines']=journal_lines
    # A fresh installation legitimately has no journal until the first event.
    try:
        event_count=storage_report.get('event_rows',0)
    except (NameError,KeyError):
        event_count=0
    healthy=checks['database_integrity']=='ok' and (journal_ok or event_count==0)
    return {'healthy':healthy,'checks':checks,'recommendations':build_retention_recommendations(report)}

def build_retention_recommendations(report):
    """Recommendations only — nothing is deleted automatically."""
    recs=[]
    total=report.get('total_local_bytes',0)
    if total>200*1024*1024: recs.append('Local memory store exceeds 200 MiB; consider archiving old session summaries (opt-in, not automatic).')
    if report.get('journal_bytes',0)>50*1024*1024: recs.append('Journal exceeds 50 MiB; consider a verified encrypted snapshot, which safely prunes ingested prefix events.')
    recs.append('Retain: verified solutions, unresolved tasks, checkpoints, decisions. Archive candidates: old superseded memories and stale session summaries (manual or opt-in only).')
    return recs

def backup_status():
    """Local backup state without uploading anything."""
    snapshots=sorted((ROOT/'storage/backups/github-repo/snapshots').glob('*.mos')) if (ROOT/'storage/backups/github-repo/snapshots').exists() else []
    last_hash=(ROOT/'storage/backups/last-snapshot.sha256')
    cfg=ROOT/'config/backup.json'
    status={'snapshots_local':len(snapshots),'latest_snapshot':snapshots[-1].name if snapshots else None,'latest_snapshot_bytes':snapshots[-1].stat().st_size if snapshots else 0,'last_uploaded_fingerprint':last_hash.read_text().strip() if last_hash.exists() else None,'configured_repository':None,'upload_policy':'encrypted AES-256-GCM only; refuses public repositories; never uploads keys'}
    if cfg.exists():
        try: status['configured_repository']=json.loads(cfg.read_text()).get('repository')
        except ValueError: pass
    return status

def call(name,a):
    if name=='memory_bootstrap':
        s=summary(a.get('cwd') or os.getcwd()); return {'project':s,'recovery_context':{'incomplete_tasks':[t for t in s['tasks'] if t['status'] not in ('VERIFIED','REVERTED')],'latest_checkpoint':s['checkpoint'],'recent_activity':s['recent_activity'][:10]},'next_action':'Review incomplete tasks and latest project changes.'}
    if name in ('memory_search','search_memory'):return {'results':search(a['query'],a.get('cwd'),int(a.get('limit',20)))}
    if name in ('memory_get_project','memory_get_project_state','memory_get_project_summary'):return summary(a.get('cwd') or os.getcwd())
    if name=='memory_get_recent_activity':return summary(a.get('cwd') or os.getcwd())['recent_activity']
    if name=='memory_list_projects':
        with connect() as c:return [dict(r) for r in c.execute('SELECT * FROM projects ORDER BY updated_at DESC')]
    if name=='memory_record_event':return event(a)
    if name=='memory_create_task':
        p=project(a.get('cwd') or os.getcwd()); ts=now(); tid=str(uuid.uuid4())
        status=a.get('status') or 'PLANNED'
        if status not in TASK_STATUSES: raise ValueError(f'Task status must be one of: {", ".join(sorted(TASK_STATUSES))}.')
        priority=str(a.get('priority') or 'normal').lower()
        if priority not in {'low','normal','high','critical'}: raise ValueError('Task priority must be low, normal, high, or critical.')
        redacted,kinds=redact(str(a.get('details') or ''))
        redacted_desc,kinds2=redact(str(a.get('description') or ''))
        with connect() as c:
            agent_id=ensure_agent(c,a.get('agent') or 'codex')
            sid=ensure_session(c,p['project_id'],agent_id,a.get('session_id'),ts)
            c.execute('INSERT INTO tasks (id,project_id,session_id,title,status,details,created_at,updated_at,agent_id,priority,description) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(tid,p['project_id'],sid,a['title'],status,redacted or None,ts,ts,agent_id,priority,redacted_desc or None))
        event({'cwd':p['root'],'event_type':'memory.task.created','agent':a.get('agent') or 'codex','payload':{'task_id':tid,'status':status,'priority':priority}})
        return {'task_id':tid,'status':status,'priority':priority,'redaction_kinds':sorted(set(kinds+kinds2))}
    if name=='memory_update_task':
        if a['status'] not in TASK_STATUSES: raise ValueError(f'Task status must be one of: {", ".join(sorted(TASK_STATUSES))}.')
        with connect() as c:
            row=c.execute('SELECT p.root_path FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.id=?',(a['task_id'],)).fetchone()
            if not row: raise ValueError('Task not found.')
            c.execute('UPDATE tasks SET status=?,updated_at=? WHERE id=?',(a['status'],now(),a['task_id']))
        event({'cwd':row['root_path'],'event_type':'memory.task.updated','agent':a.get('agent') or 'codex','payload':{'task_id':a['task_id'],'status':a['status']}})
        return {'updated':True}
    if name=='memory_record_decision':
        p=project(a.get('cwd') or os.getcwd()); did=str(uuid.uuid4()); agent=a.get('agent') or 'generic'
        redacted_sum,kinds=redact(str(a['summary'])); redacted_rat,kinds2=redact(str(a.get('rationale') or ''))
        with connect() as c:
            agent_id=ensure_agent(c,agent); sid=ensure_session(c,p['project_id'],agent_id,a.get('session_id'),now())
            c.execute('INSERT INTO decisions VALUES(?,?,?,?,?,?,?)',(did,p['project_id'],sid,redacted_sum,redacted_rat or None,now(),agent_id))
        event({'cwd':p['root'],'event_type':'memory.decision.recorded','agent':agent,'payload':{'decision_id':did,'redaction_kinds':sorted(set(kinds+kinds2))}})
        return {'decision_id':did,'redaction_kinds':sorted(set(kinds+kinds2))}
    if name=='memory_record_error':
        p=project(a.get('cwd') or os.getcwd()); eid=str(uuid.uuid4()); agent=a.get('agent') or 'generic'
        redacted_sum,kinds=redact(str(a['summary'])); redacted_det,kinds2=redact(str(a.get('details') or ''))
        with connect() as c:
            agent_id=ensure_agent(c,agent); sid=ensure_session(c,p['project_id'],agent_id,a.get('session_id'),now())
            c.execute('INSERT INTO errors VALUES(?,?,?,?,?,?,?,?)',(eid,p['project_id'],sid,redacted_sum,redacted_det or None,'OPEN',now(),agent_id))
        event({'cwd':p['root'],'event_type':'memory.error.recorded','agent':agent,'payload':{'error_id':eid,'status':'OPEN','redaction_kinds':sorted(set(kinds+kinds2))}})
        return {'error_id':eid,'status':'OPEN','redaction_kinds':sorted(set(kinds+kinds2))}
    if name=='memory_verify_task':
        with connect() as c:
            c.execute('INSERT INTO verification_results VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid4()),a['project_id'],a.get('task_id'),a.get('command'),a.get('exit_code'),a.get('evidence'),now()))
            if a.get('task_id') and a.get('exit_code')==0:c.execute("UPDATE tasks SET status='VERIFIED',updated_at=? WHERE id=?",(now(),a['task_id']))
            project_row=c.execute('SELECT root_path FROM projects WHERE id=?',(a['project_id'],)).fetchone()
        event({'cwd':project_row['root_path'] if project_row else os.getcwd(),'event_type':'memory.task.verified' if a.get('exit_code')==0 else 'memory.task.verification_recorded','agent':a.get('agent') or 'codex','payload':{'task_id':a.get('task_id'),'exit_code':a.get('exit_code')}})
        return {'recorded':True,'verified':a.get('exit_code')==0}
    if name=='memory_save_checkpoint':
        p=project(a.get('cwd') or os.getcwd()); cid=str(uuid.uuid4())
        state=json.dumps(a.get('state',{}),ensure_ascii=False)
        with connect() as c:
            agent_id=ensure_agent(c,a.get('agent') or 'codex')
            sid=ensure_session(c,p['project_id'],agent_id,a.get('session_id'),now())
            c.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?)',(cid,p['project_id'],sid,redact(str(a['summary']))[0],state,now()))
        cpfile=ROOT/'storage/checkpoints'/f'{cid}.json'; cpfile.parent.mkdir(parents=True,exist_ok=True); os.chmod(cpfile.parent,0o700); cpfile.write_text(state); os.chmod(cpfile,0o600)
        event({'cwd':p['root'],'event_type':'memory.checkpoint.saved','agent':a.get('agent') or 'codex','payload':{'checkpoint_id':cid,'summary':redact(str(a['summary']))[0]}})
        return {'checkpoint_id':cid}
    if name=='memory_get_next_action':
        s=summary(a.get('cwd') or os.getcwd()); return {'incomplete_tasks':[t for t in s['tasks'] if t['status'] not in ('VERIFIED','REVERTED')],'suggestion':'Inspect the current Git diff, then continue the highest priority incomplete task.'}
    if name=='memory_compare_project_state':return {'project':summary(a.get('cwd') or os.getcwd()),'git_status':git(a.get('cwd') or os.getcwd(),'status','--short')}
    if name=='remember':return remember(a)
    if name=='get_memory':
        with connect() as c:return _memory_row(_get_memory(c,a['memory_id'],a.get('project_id')))
    if name=='update_memory':return update_memory(a)
    if name=='link_memories':return link_memories(a)
    if name=='archive_memory':return archive_memory(a)
    if name=='get_memory_history':return memory_history(a)
    if name=='list_memories':return list_memories_tool(a)
    if name=='list_tasks':return list_tasks_tool(a)
    if name=='get_project_progress':return get_project_progress(a)
    if name=='record_solution':return record_solution(a)
    if name=='search_solutions':return search_solutions(a)
    if name=='start_session':return start_session(a)
    if name=='end_session':return end_session(a)
    if name=='get_recent_sessions':return get_recent_sessions(a)
    if name=='restore_checkpoint':return restore_checkpoint(a)
    if name=='record_agent_activity':return record_agent_activity(a)
    if name=='get_agent_activity':return get_agent_activity(a)
    if name=='register_project':return register_project(a)
    if name=='switch_project':return switch_project(a)
    if name=='get_current_project':return current_project(a)
    if name=='get_project_context':return get_project_context(a)
    if name=='find_duplicate_memories':return find_duplicate_memories(a)
    if name=='consolidate_memories':return consolidate_memories(a)
    if name=='verify_memory':return verify_memory(a)
    if name=='get_project_health':return get_project_health(a)
    if name=='build_project_context':return build_project_context_manifest(a)
    if name=='recover_interrupted_sessions':return recover_interrupted_sessions(a)
    if name=='get_session_summary':return get_session_summary(a)
    if name=='complete_task':return complete_task(a)
    if name=='add_task_dependency':return add_task_dependency(a)
    if name=='get_agent_history':return get_agent_history(a)
    if name=='get_storage_status':return storage_status()
    if name=='get_sync_status':return _r2().get_sync_status(_r2().get_r2_config())
    if name=='request_sync':
        cfg=_r2().get_r2_config()
        if cfg.get('MEMORYOS_R2_ENABLED')!='true':
            return {'status':'disabled','note':'R2 is not enabled; set MEMORYOS_R2_ENABLED=true with bucket credentials. Local memory is unaffected.'}
        return _r2().sync(cfg, ROOT, dry_run=bool(a.get('dry_run', True)), audit=lambda payload: event({'cwd':ROOT,'event_type':'storage.sync','agent':'memoryos','payload':payload}))
    if name=='verify_storage_integrity':return verify_storage_integrity(a)
    if name=='get_backup_status':return backup_status()
    if name=='create_task':return call('memory_create_task',a)
    if name=='update_task':return call('memory_update_task',a)
    if name=='record_decision':return call('memory_record_decision',a)
    if name=='record_error':return call('memory_record_error',a)
    if name=='create_checkpoint':return call('memory_save_checkpoint',a)
    if name=='list_projects':return call('memory_list_projects',{})
    if name=='list_decisions':
        pid=project(a['cwd'])['project_id'] if a.get('cwd') else a.get('project_id')
        with connect() as c:
            sql='SELECT id,summary,rationale,created_at,agent_id FROM decisions'; args=[]
            if pid: sql+=' WHERE project_id=?'; args.append(pid)
            sql+=' ORDER BY created_at DESC LIMIT ?'; args.append(int(a.get('limit',20)))
            return [dict(r) for r in c.execute(sql,args)]
    if name=='get_decision_history':
        did=a['decision_id']
        with connect() as c:
            row=c.execute('SELECT * FROM decisions WHERE id=?',(did,)).fetchone()
            if not row: raise ValueError(f'Decision not found: {did}')
            related=[dict(r) for r in c.execute("SELECT * FROM activity_events WHERE event_type='memory.decision.recorded' AND payload LIKE ? ORDER BY occurred_at DESC LIMIT 20",(f'%{did}%',))]
        return {'decision':dict(row),'related_events':related}
    raise ValueError('Unknown tool: '+name)
TOOLS=[('memory_bootstrap','Identify project and prepare recovery context',{'cwd':'string'}),
('memory_search','Search project memory and events',{'query':'string','cwd':'string','limit':'number'}),
('memory_get_project','Get detected project details',{'cwd':'string'}),
('memory_get_project_state','Get project tasks and current state',{'cwd':'string'}),
('memory_get_recent_activity','Get recent captured events',{'cwd':'string'}),
('memory_record_event','Persist an observable activity event',{'cwd':'string','event_type':'string','payload':'object'}),
('memory_create_task','Create a project task',{'cwd':'string','title':'string','status':'string','details':'string'}),
('memory_update_task','Update a task status',{'task_id':'string','status':'string'}),
('memory_record_decision','Record an architectural or technical decision',{'cwd':'string','summary':'string','rationale':'string'}),
('memory_record_error','Record an encountered error',{'cwd':'string','summary':'string','details':'string'}),
('memory_verify_task','Store verification evidence; exit code 0 verifies task',{'project_id':'string','task_id':'string','command':'string','exit_code':'number','evidence':'string'}),
('memory_save_checkpoint','Save recovery checkpoint',{'cwd':'string','summary':'string','state':'object'}),
('memory_get_next_action','Get incomplete tasks and a suggested next action',{'cwd':'string'}),
('memory_get_project_summary','Get project summary',{'cwd':'string'}),
('memory_compare_project_state','Compare stored state with current Git status',{'cwd':'string'}),
('memory_list_projects','List known projects',{}),
('remember','Capture one versioned, redacted, idempotent memory record',{'cwd':'string','category':'string','content':'string','title':'string','importance':'number','confidence':'number','verification':'string','tags':'array','supersedes':'string'}),
('get_memory','Get one memory by ID',{'memory_id':'string','project_id':'string'}),
('update_memory','Update content, verification, status, importance, confidence, or tags; previous state is versioned',{'memory_id':'string','content':'string','title':'string','verification':'string','status':'string','importance':'number','confidence':'number','tags':'array'}),
('link_memories','Relate two memories with a relation label',{'from_memory':'string','to_memory':'string','relation':'string'}),
('archive_memory','Archive a memory without deleting it',{'memory_id':'string'}),
('get_memory_history','Get version history and relationships for one memory',{'memory_id':'string'}),
('list_memories','List active memories, optionally by project and category',{'cwd':'string','project_id':'string','category':'string','limit':'number'}),
('list_tasks','List tasks, optionally filtered by project and status',{'cwd':'string','project_id':'string','status':'string'}),
('get_project_progress','Aggregate task, decision, error, and memory counts for a project',{'cwd':'string'}),
('record_solution','Attach a verified or proposed solution to a recorded error',{'cwd':'string','error_id':'string','resolution':'string','evidence':'string','verified':'boolean'}),
('search_solutions','Search recorded error solutions',{'query':'string','cwd':'string','limit':'number'}),
('start_session','Start or resume an agent session and select the current project',{'cwd':'string','agent':'string','session_id':'string'}),
('end_session','End a session and optionally store its summary as a memory',{'cwd':'string','session_id':'string','summary':'string','agent':'string'}),
('get_recent_sessions','List recent sessions for a project',{'cwd':'string','limit':'number'}),
('restore_checkpoint','Restore the latest or a specific checkpoint',{'cwd':'string','checkpoint_id':'string','project_id':'string'}),
('record_agent_activity','Record one meaningful agent operation with its result',{'cwd':'string','agent':'string','operation':'string','target':'string','result':'string','status':'string','details':'string'}),
('get_agent_activity','List recorded agent activity, optionally filtered',{'agent':'string','project_id':'string','cwd':'string','limit':'number'}),
('register_project','Register a directory as a Memory OS project',{'path':'string','name':'string'}),
('switch_project','Explicitly select the current project for an agent',{'agent':'string','project_id':'string','cwd':'string'}),
('get_current_project','Resolve the current project: explicit selection first, then working-directory detection',{'cwd':'string','agent':'string'}),
('get_project_context','Load full authorized context for the current project',{'cwd':'string'}),
('search_memory','Search project memory and events (canonical name)',{'query':'string','cwd':'string','limit':'number'}),
('create_task','Create a project task (canonical name)',{'cwd':'string','title':'string','status':'string','details':'string'}),
('update_task','Update a task status (canonical name)',{'task_id':'string','status':'string'}),
('record_decision','Record a decision (canonical name)',{'cwd':'string','summary':'string','rationale':'string'}),
('list_decisions','List recent decisions for a project',{'cwd':'string','project_id':'string','limit':'number'}),
('get_decision_history','Get one decision with its related activity events',{'decision_id':'string'}),
('record_error','Record an error (canonical name)',{'cwd':'string','summary':'string','details':'string'}),
('list_projects','List known projects (canonical name)',{}),
('create_checkpoint','Save recovery checkpoint (canonical name)',{'cwd':'string','summary':'string','state':'object'}),
('get_project_health','Project health: activity staleness, task/error/memory counts',{'cwd':'string'}),
('build_project_context','Write ranked, budgeted project context projections',{'cwd':'string','budget':'number'}),
('verify_memory','Mark a memory verified; requires evidence',{'memory_id':'string','evidence':'string','content':'string','agent':'string','cwd':'string'}),
('find_duplicate_memories','Detect exact and near-duplicate memory candidates (read-only)',{'cwd':'string','project_id':'string','threshold':'number'}),
('consolidate_memories','Safely merge a secondary memory into a primary one',{'primary_id':'string','secondary_id':'string','agent':'string','cwd':'string'}),
('complete_task','Complete a task; requires evidence',{'task_id':'string','evidence':'string','command':'string','exit_code':'number','agent':'string'}),
('add_task_dependency','Declare that one task depends on another (cycle-safe)',{'task_id':'string','depends_on_task':'string'}),
('recover_interrupted_sessions','Mark stale active sessions as interrupted',{'stale_after_hours':'number'}),
('get_session_summary','Summarize one session: activity, errors, checkpoint, memories',{'session_id':'string'}),
('get_agent_history','Aggregate one agent sessions, activity kinds, and projects',{'agent':'string'}),
('get_storage_status','Local storage usage, integrity, and backup/R2 readiness',{}),
('get_backup_status','Local snapshot state and upload policy (no upload)',{}),
('get_sync_status','Remote synchronization readiness (R2 disabled until configured)',{}),
('request_sync','Request a synchronization run; dry_run reports without writing',{'dry_run':'boolean'}),
('verify_storage_integrity','Read-only integrity pass over database and journal',{})]
REQUIRED_BY_TOOL={('memory_search','query'),('search_memory','query'),('memory_create_task','title'),('memory_update_task','task_id'),('memory_update_task','status'),('memory_record_decision','summary'),('memory_record_error','summary'),('memory_record_event','event_type'),('memory_save_checkpoint','summary'),('remember','content'),('get_memory','memory_id'),('update_memory','memory_id'),('link_memories','from_memory'),('link_memories','to_memory'),('archive_memory','memory_id'),('get_memory_history','memory_id'),('register_project','path'),('record_solution','error_id'),('record_solution','resolution'),('search_memory','query'),('create_task','title'),('update_task','task_id'),('update_task','status'),('record_decision','summary'),('record_error','summary'),('create_checkpoint','summary'),('get_decision_history','decision_id'),('verify_memory','memory_id'),('verify_memory','evidence'),('consolidate_memories','primary_id'),('consolidate_memories','secondary_id'),('complete_task','task_id'),('complete_task','evidence'),('add_task_dependency','task_id'),('add_task_dependency','depends_on_task'),('get_session_summary','session_id')}
def mcp():
    def send(obj): sys.stdout.write(json.dumps(obj)+'\n'); sys.stdout.flush()
    for line in sys.stdin:
        try:
            msg=json.loads(line); method=msg.get('method'); i=msg.get('id')
            if method=='notifications/initialized':continue
            if method=='initialize':send({'jsonrpc':'2.0','id':i,'result':{'protocolVersion':msg.get('params',{}).get('protocolVersion','2024-11-05'),'capabilities':{'tools':{}},'serverInfo':{'name':'memory-os','version':'0.4.0'}}})
            elif method=='ping':send({'jsonrpc':'2.0','id':i,'result':{}})
            elif method=='tools/list': send({'jsonrpc':'2.0','id':i,'result':{'tools':[{'name':n,'description':d,'inputSchema':{'type':'object','properties':{k:{'type':v} for k,v in props.items()},'required':[k for k in props if (n,k) in REQUIRED_BY_TOOL],'additionalProperties':True}} for n,d,props in TOOLS]}})
            elif method=='tools/call':
                p=msg['params']; args=p.get('arguments',{}); result=call(p['name'],args);send({'jsonrpc':'2.0','id':i,'result':{'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}]}})
            elif i is not None:send({'jsonrpc':'2.0','id':i,'result':{}})
        except Exception as e:
            try:send({'jsonrpc':'2.0','id':msg.get('id'),'error':{'code':-32000,'message':str(e)}})
            except Exception:pass
def health():
    with connect() as c:
        integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
        counts={'projects':c.execute('SELECT count(*) FROM projects').fetchone()[0],'memories':c.execute("SELECT count(*) FROM memories WHERE status='active'").fetchone()[0],'events':c.execute('SELECT count(*) FROM activity_events').fetchone()[0],'tasks':c.execute('SELECT count(*) FROM tasks').fetchone()[0]}
    backup_cfg=ROOT/'config/backup.json'
    backup={'configured':backup_cfg.exists(),'upload_policy':'refuses public repositories; no upload from this dashboard'}
    if backup_cfg.exists():
        try: backup['repository']=json.loads(backup_cfg.read_text()).get('repository','')
        except ValueError: backup['repository']=None
    return {'status':'ok' if integrity=='ok' else 'degraded','integrity':integrity,'counts':counts,'backup':backup,'version':'0.4.0'}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        u=urlparse(self.path)
        if u.path=='/health':data=health()
        elif u.path=='/projects':data=call('memory_list_projects',{})
        elif u.path=='/summary':data=summary(parse_qs(u.query).get('cwd',[os.getcwd()])[0])
        elif u.path=='/search':qs=parse_qs(u.query);data={'results':search(qs.get('q',[''])[0],qs.get('cwd',[None])[0],int(qs.get('limit',['20'])[0]))}
        elif u.path=='/memories':qs=parse_qs(u.query);data={'memories':list_memories_tool({'cwd':qs.get('cwd',[None])[0],'category':qs.get('category',[None])[0],'limit':qs.get('limit',['50'])[0]})}
        elif u.path=='/tasks':data={'tasks':list_tasks_tool({'cwd':parse_qs(u.query).get('cwd',[None])[0],'limit':100})}
        elif u.path=='/decisions':
            cwd=parse_qs(u.query).get('cwd',[None])[0]
            with connect() as c:
                sql='SELECT id,summary,rationale,created_at,agent_id FROM decisions'; args=[]
                if cwd: sql+=' WHERE project_id=?'; args.append(project(cwd)['project_id'])
                sql+=' ORDER BY created_at DESC LIMIT 50'; data={'decisions':[dict(r) for r in c.execute(sql,args)]}
        elif u.path=='/sessions':data={'sessions':get_recent_sessions({'cwd':parse_qs(u.query).get('cwd',[None])[0],'limit':20})}
        elif u.path=='/activity':data={'activity':get_agent_activity({'cwd':parse_qs(u.query).get('cwd',[None])[0],'limit':50})}
        elif u.path=='/progress':data=get_project_progress({'cwd':parse_qs(u.query).get('cwd',[os.getcwd()])[0]})
        elif u.path=='/storage':
            data={'storage':storage_status(),'sync':_r2().get_sync_status(_r2().get_r2_config()),'config':_r2().redacted_config(_r2().get_r2_config()),'backup':backup_status(),'integrity':verify_storage_integrity({})}
        else:
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(DASHBOARD_HTML.encode());return
        body=json.dumps(data,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
    def do_POST(self):
        try:
            n=int(self.headers.get('Content-Length',0)); obj=json.loads(self.rfile.read(n)); data=event(obj); b=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b)
        except Exception as e:self.send_error(400,str(e))
DASHBOARD_HTML='''<!doctype html><meta charset="utf-8"><title>Memory OS</title>
<style>body{font:15px -apple-system;background:#10141c;color:#e7edf6;margin:32px;max-width:1100px}h1{color:#8ab4ff}h2{margin-top:22px}section{background:#1a2230;border-radius:12px;padding:18px;margin:14px 0}select,input,button{padding:9px;border-radius:8px;border:0;background:#0d1117;color:#e7edf6}button{background:#2563eb;cursor:pointer}pre{white-space:pre-wrap;font-size:13px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.ok{color:#4ade80}.warn{color:#fbbf24}</style>
<h1>Memory OS</h1><p>Local project memory dashboard — loopback only</p>
<section><b>Health:</b> <span id="health"></span></section>
<section><h2>Projects</h2><select id="project" onchange="loadProject()"></select><pre id="progress"></pre></section>
<div class="grid">
<section><h2>Memory search</h2><input id="q" placeholder="Search memories and events"><button onclick="runSearch()">Search</button><pre id="results"></pre></section>
<section><h2>Recent memories</h2><pre id="memories"></pre></section>
</div>
<div class="grid">
<section><h2>Tasks</h2><pre id="tasks"></pre></section>
<section><h2>Decisions</h2><pre id="decisions"></pre></section>
</div>
<div class="grid">
<section><h2>Sessions</h2><pre id="sessions"></pre></section>
<section><h2>Agent activity</h2><pre id="activity"></pre></section>
</div>
<section><h2>Storage, backup and R2</h2><pre id="storage"></pre></section>
<script>
let cwd=null;
async function j(url){return (await fetch(url)).json()}
async function init(){
  let h=await j('/health');
  document.querySelector('#health').innerHTML=(h.status==='ok'?'<span class="ok">ok</span>':'<span class="warn">'+h.status+'</span>')+' — integrity: '+h.integrity+' — counts: '+JSON.stringify(h.counts)+' — backup: '+JSON.stringify(h.backup);
  let p=await j('/projects');
  let sel=document.querySelector('#project'); sel.innerHTML='';
  for(let item of p){let o=document.createElement('option');o.value=item.root_path;o.textContent=item.name;sel.appendChild(o)}
  if(p.length)loadProject();
}
async function loadProject(){
  cwd=document.querySelector('#project').value; if(!cwd)return;
  let e=encodeURIComponent(cwd);
  let prog=await j('/progress?cwd='+e); document.querySelector('#progress').textContent=JSON.stringify(prog,null,1).slice(0,1200);
  let m=await j('/memories?cwd='+e); document.querySelector('#memories').textContent=(m.memories||[]).slice(0,20).map(x=>x.category+' · '+x.verification+' · '+(x.title||x.content.slice(0,80))).join('\\n')||'(none)';
  let t=await j('/tasks?cwd='+e); document.querySelector('#tasks').textContent=(t.tasks||[]).slice(0,20).map(x=>x.status+' — '+x.title).join('\\n')||'(none)';
  let d=await j('/decisions?cwd='+e); document.querySelector('#decisions').textContent=(d.decisions||[]).slice(0,15).map(x=>'• '+x.summary).join('\\n')||'(none)';
  let s=await j('/sessions?cwd='+e); document.querySelector('#sessions').textContent=(s.sessions||[]).map(x=>x.agent_name+' · '+x.status+' · '+x.started_at).join('\\n')||'(none)';
  let a=await j('/activity?cwd='+e); document.querySelector('#activity').textContent=(a.activity||[]).slice(0,25).map(x=>x.agent+' · '+x.event_type+' · '+x.occurred_at).join('\\n')||'(none)';
}
async function runSearch(){
  if(!cwd)return; let r=await j('/search?q='+encodeURIComponent(document.querySelector('#q').value)+'&cwd='+encodeURIComponent(cwd));
  document.querySelector('#results').textContent=JSON.stringify(r.results,null,1).slice(0,4000);
}
init();
</script>'''
def doctor(as_json=False):
    """Read-only health check of the whole MEMORY OS installation."""
    checks=[]
    def add(name, ok, detail):
        checks.append({'check':name,'ok':bool(ok),'detail':detail})
    add('installation',ROOT.is_dir(),'runtime at '+str(ROOT))
    try:
        with connect() as c:
            integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
            counts={'projects':c.execute('SELECT count(*) FROM projects').fetchone()[0],'memories':c.execute('SELECT count(*) FROM memories').fetchone()[0],'events':c.execute('SELECT count(*) FROM activity_events').fetchone()[0],'tasks':c.execute('SELECT count(*) FROM tasks').fetchone()[0]}
            fts_columns=[row[1] for row in c.execute('PRAGMA table_info(memories_fts)')]
        add('database',integrity=='ok',f'integrity {integrity}; {counts}')
        add('journal',JOURNAL.exists(),f'{JOURNAL} ({JOURNAL.stat().st_size if JOURNAL.exists() else 0} bytes)')
        add('fts_schema','title' in fts_columns,'memories_fts columns: '+','.join(fts_columns))
    except Exception as exc:
        add('database',False,type(exc).__name__)
        add('journal',False,'skipped')
        add('fts_schema',False,'skipped')
    tool_count=len(TOOLS)
    add('mcp_tools',tool_count>=46,f'{tool_count} tools registered')
    try:
        result=call('memory_search',{'query':'probe'})
        add('search',True,'memory_search executed')
    except Exception as exc:
        add('search',False,type(exc).__name__)
    storage=storage_status()
    add('storage',storage.get('database_integrity')=='ok',f"{storage.get('total_local_bytes',0)} bytes local")
    add('backup',storage['backup']['configured'],f"repository: {storage['backup'].get('repository')}; uploads refuse public repositories")
    r2=_r2().get_r2_config()
    add('r2',not r2.get('problems'),('disabled by default; '+('config valid' if r2.get('valid') else 'problems: '+','.join(r2['problems']))) if r2.get('MEMORYOS_R2_ENABLED')!='true' else ('valid' if r2.get('valid') else 'invalid'))
    codex_cfg=Path.home()/'.codex/config.toml'
    add('codex_integration',codex_cfg.exists(),'memory_os registered in ~/.codex/config.toml' if codex_cfg.exists() and 'memory_os' in codex_cfg.read_text() else 'not registered')
    claude_cfg=Path.home()/'.claude.json'
    add('claude_integration',claude_cfg.exists(),'config exists; MCP registration pending CLI install' if claude_cfg.exists() else 'CLI not installed (pending user authorization)')
    healthy=all(item['ok'] for item in checks)
    report={'healthy':healthy,'checks':checks,'generated_at':now(),'version':'0.4.0'}
    if as_json: return report
    lines=[('PASS' if item['ok'] else 'FAIL')+'  '+item['check']+': '+item['detail'] for item in checks]
    return ('MEMORY OS doctor — '+('HEALTHY' if healthy else 'PROBLEMS FOUND')+'\n'+'\n'.join(lines))

def run_test_suite(groups):
    """Run the project test suite with isolated temporary databases."""
    import unittest
    loader=unittest.TestLoader()
    suite=unittest.TestSuite()
    all_map={'unit':'tests/test_engine.py tests/test_memory_tools.py','integration':'tests/test_memory_tools.py','mcp':'tests/test_memory_tools.py','storage':'tests/test_v04_storage.py','r2':'tests/test_v04_storage.py','regression':'tests/test_backup.py tests/test_engine.py'}
    if not groups: groups=['unit','integration','mcp','storage','r2','regression']
    for group in groups:
        for target in all_map.get(group,[]).split():
            if Path(target).exists():
                suite.addTests(loader.discover(Path(target).parent, pattern=Path(target).name))
    runner=unittest.TextTestRunner(verbosity=1)
    result=runner.run(suite)
    return {'ran':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'success':result.wasSuccessful()}

def main():
    ap=argparse.ArgumentParser();sp=ap.add_subparsers(dest='cmd');sp.add_parser('mcp');sp.add_parser('init');d=sp.add_parser('detect');d.add_argument('path',nargs='?',default=os.getcwd());e=sp.add_parser('event');e.add_argument('--json',required=True);sp.add_parser('ingest');sp.add_parser('health');s=sp.add_parser('serve');s.add_argument('--port',type=int,default=8765);sp.add_parser('doctor');sp.add_parser('doctor-json');t=sp.add_parser('test');t.add_argument('--unit',action='store_true');t.add_argument('--integration',action='store_true');t.add_argument('--mcp',action='store_true');t.add_argument('--storage',action='store_true');t.add_argument('--r2',action='store_true');t.add_argument('--regression',action='store_true');st=sp.add_parser('storage');sts=st.add_subparsers(dest='storage_cmd',required=True);sts.add_parser('status');sts.add_parser('test');sts.add_parser('verify');syncp=sts.add_parser('sync');syncp.add_argument('--execute',action='store_true');sts.add_parser('list');dlp=sts.add_parser('download');dlp.add_argument('key');args=ap.parse_args()
    if args.cmd=='mcp':mcp()
    elif args.cmd=='init':connect().close();print(json.dumps({'database':str(DB),'journal':str(JOURNAL),'version':'0.4.0'}))
    elif args.cmd=='detect':print(json.dumps(project(args.path),indent=2))
    elif args.cmd=='event':print(json.dumps(event(json.loads(args.json))))
    elif args.cmd=='ingest':print(ingest())
    elif args.cmd=='health':print(json.dumps(health(),indent=2))
    elif args.cmd=='doctor':print(doctor())
    elif args.cmd=='doctor-json':print(json.dumps(doctor(as_json=True),indent=2))
    elif args.cmd=='test':
        groups=[name for name in ('unit','integration','mcp','storage','r2','regression') if getattr(args,name)]
        print(json.dumps(run_test_suite(groups),indent=2))
    elif args.cmd=='storage':
        cfg=_r2().get_r2_config()
        if args.storage_cmd=='status':print(json.dumps({'storage':storage_status(),'sync':_r2().get_sync_status(cfg),'config':_r2().redacted_config(cfg)},indent=2))
        elif args.storage_cmd=='test':
            adapter=_r2().build_adapter(cfg,local_root=ROOT/'storage/r2-local-test' if cfg.get('MEMORYOS_R2_ENABLED')!='true' else None)
            print(json.dumps(adapter.connectivity_test() if adapter else {'status':'disabled','note':'R2 is disabled; enable with MEMORYOS_R2_ENABLED=true'},indent=2))
        elif args.storage_cmd=='verify':print(json.dumps(verify_storage_integrity({}),indent=2))
        elif args.storage_cmd=='sync':
            execute=args.execute
            result=_r2().sync(cfg,ROOT,dry_run=not execute,audit=lambda payload: event({'cwd':str(ROOT),'event_type':'storage.sync','agent':'memoryos','payload':payload}))
            print(json.dumps(result,indent=2))
        elif args.storage_cmd=='list':
            adapter=_r2().build_adapter(cfg,local_root=ROOT/'storage/r2-local-test' if cfg.get('MEMORYOS_R2_ENABLED')!='true' else None)
            print(json.dumps({'objects':adapter.list() if adapter else []},indent=2))
        elif args.storage_cmd=='download':
            adapter=_r2().build_adapter(cfg,local_root=ROOT/'storage/r2-local-test' if cfg.get('MEMORYOS_R2_ENABLED')!='true' else None)
            if not adapter: print(json.dumps({'status':'disabled'})); raise SystemExit(2)
            blob=adapter.download(args.key)
            plain=_r2().decrypt_for_r2(blob,cfg['bucket']) if blob.startswith(_r2().R2_MAGIC) else blob
            target=ROOT/'storage/restored'/Path(args.key).name
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700); target.write_bytes(plain); target.chmod(0o600)
            print(json.dumps({'restored_to':str(target),'bytes':len(plain)},indent=2))
    elif args.cmd=='serve':ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
    else:ap.print_help()
if __name__=='__main__':main()
