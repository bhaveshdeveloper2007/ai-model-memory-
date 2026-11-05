#!/usr/bin/env python3
"""Encrypted, private GitHub snapshot and recovery support for Memory OS."""
from __future__ import annotations
import argparse, datetime as dt, hashlib, io, json, os, re, shutil, sqlite3, subprocess, sys, tarfile, tempfile, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CFG=ROOT/'config/backup.json'
MEMORY=ROOT/'engine/memoryos.py'
CRYPTO_SRC=ROOT/'scripts/backup_crypto.swift'
CRYPTO=ROOT/'bin/memoryos-crypto'
BRANCH='memoryos-backups'
MAGIC=b'MEMORYOS-AESGCM-1\n'
sys.path.insert(0,str(MEMORY.parent))
import memoryos as mem

def run(args, *, check=True, capture=True, input=None):
    return subprocess.run(args,check=check,input=input,stdout=subprocess.PIPE if capture else None,stderr=subprocess.PIPE if capture else None)
def config():
    if not CFG.exists(): raise RuntimeError('Encrypted backups are not configured. Run scripts/enable-backups.sh OWNER/PRIVATE-REPOSITORY.')
    data=json.loads(CFG.read_text()); slug=data.get('repository','')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',slug): raise RuntimeError('Invalid backup repository setting.')
    return data
def repo_url(slug): return f'https://github.com/{slug}.git'
def repo_private(slug):
    r=run(['gh','repo','view',slug,'--json','visibility','--jq','.visibility'])
    if r.stdout.decode().strip()!='PRIVATE': raise RuntimeError('Backup stopped: the selected GitHub repository is not private.')
def compile_crypto():
    CRYPTO.parent.mkdir(parents=True,exist_ok=True); os.chmod(CRYPTO.parent,0o700)
    if not CRYPTO.exists() or CRYPTO_SRC.stat().st_mtime>CRYPTO.stat().st_mtime:
        run(['swiftc','-O',str(CRYPTO_SRC),'-o',str(CRYPTO)])
        os.chmod(CRYPTO,0o700)
def service(slug): return 'com.memoryos.backup.'+hashlib.sha256(slug.encode()).hexdigest()[:16]
def recovery_file(slug):
    d=ROOT/'storage/backups';d.mkdir(parents=True,exist_ok=True);os.chmod(d,0o700)
    safe=re.sub(r'[^A-Za-z0-9_.-]','_',slug)
    return d/f'recovery-key-{safe}.txt'
def ensure_key(slug):
    compile_crypto(); keyfile=recovery_file(slug); svc=service(slug)
    key_result=run([str(CRYPTO),'key',svc],check=False)
    key=key_result.stdout.decode().strip() if key_result.returncode==0 else None
    if keyfile.exists():
        saved=keyfile.read_text().strip()
        if key and key!=saved: raise RuntimeError('Keychain key and recovery key differ; refusing to encrypt or upload.')
        if not key:
            run([str(CRYPTO),'import',svc],input=saved.encode())
            key=run([str(CRYPTO),'key',svc]).stdout.decode().strip()
    elif key:
        keyfile.write_text(key+'\n');os.chmod(keyfile,0o600)
    else:
        # Never silently create a new key for an existing encrypted backup history.
        branch=run(['git','ls-remote','--heads',repo_url(slug),BRANCH],check=False)
        if branch.returncode==0 and branch.stdout.strip():
            raise RuntimeError(f'Existing backups need their recovery key. Place it at {keyfile} before continuing.')
        key=run([str(CRYPTO),'init',svc]).stdout.decode().strip()
        keyfile.write_text(key+'\n');os.chmod(keyfile,0o600)
    return svc,keyfile

def configure(slug):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',slug): raise RuntimeError('Use OWNER/REPOSITORY format.')
    repo_private(slug)
    # Validate Git access before saving the destination.
    run(['git','ls-remote',repo_url(slug)])
    CFG.parent.mkdir(parents=True,exist_ok=True);os.chmod(CFG.parent,0o700)
    CFG.write_text(json.dumps({'repository':slug,'retention_days':30},indent=2)+'\n');os.chmod(CFG,0o600)
    ensure_key(slug)
    print(f'Private encrypted backup is configured for {slug}.')
    print(f'Keep a copy of the recovery key from {recovery_file(slug)} in a separate safe place.')

def checkout(slug):
    repo_private(slug)
    path=ROOT/'storage/backups/github-repo'; path.parent.mkdir(parents=True,exist_ok=True);os.chmod(path.parent,0o700)
    if not (path/'.git').exists():
        if path.exists() and any(path.iterdir()): raise RuntimeError('Backup checkout path exists and is not empty; refusing to use it.')
        path.mkdir(parents=True,exist_ok=True);run(['git','init',str(path)])
        run(['git','-C',str(path),'remote','add','origin',repo_url(slug)])
        run(['git','-C',str(path),'config','user.name','Memory OS Backup'])
        run(['git','-C',str(path),'config','user.email','memory-os@users.noreply.github.com'])
    origin=run(['git','-C',str(path),'remote','get-url','origin']).stdout.decode().strip()
    if origin.rstrip('.git')!=repo_url(slug).rstrip('.git'): raise RuntimeError('The configured backup checkout points to a different repository.')
    remote=run(['git','ls-remote','--heads',repo_url(slug),BRANCH],check=False)
    local=run(['git','-C',str(path),'show-ref','--verify','--quiet',f'refs/heads/{BRANCH}'],check=False).returncode==0
    if remote.returncode!=0: raise RuntimeError('Could not check the private backup branch.')
    if remote.stdout.strip():
        run(['git','-C',str(path),'fetch','origin',BRANCH])
        if local:
            run(['git','-C',str(path),'checkout',BRANCH])
            run(['git','-C',str(path),'merge','--ff-only','FETCH_HEAD'])
        else: run(['git','-C',str(path),'checkout','-b',BRANCH,'FETCH_HEAD'])
    elif not local:
        run(['git','-C',str(path),'checkout','--orphan',BRANCH])
    else: run(['git','-C',str(path),'checkout',BRANCH])
    return path

def snapshot_archive():
    # Lock journal briefly so the database and captured journal represent a safe point.
    lock=mem.journal_lock()
    try:
        journal=mem.JOURNAL.read_bytes() if mem.JOURNAL.exists() else b''
        expected=sum(1 for line in journal.splitlines() if line.strip())
        processed=mem.ingest()
        if processed!=expected:
            raise RuntimeError(f'Journal ingestion incomplete ({processed}/{expected}); refusing to archive or prune local history.')
        temp_base=ROOT/'storage/backups';temp_base.mkdir(parents=True,exist_ok=True);os.chmod(temp_base,0o700)
        # A hard crash can leave a plaintext SQLite staging copy; remove stale ones
        # while holding the journal lock before creating the next private snapshot.
        for stale in temp_base.glob('memoryos-snapshot-*'):
            if stale.is_dir(): shutil.rmtree(stale,ignore_errors=True)
        tempdir=Path(tempfile.mkdtemp(prefix='memoryos-snapshot-',dir=temp_base));os.chmod(tempdir,0o700)
        try:
            source=mem.connect();target=sqlite3.connect(tempdir/'memoryos.sqlite3')
            try: source.backup(target);target.commit()
            finally: target.close();source.close()
            database=(tempdir/'memoryos.sqlite3').read_bytes()
        finally: shutil.rmtree(tempdir,ignore_errors=True)
        files=[]
        config_file=ROOT/'config/backup.json'
        if config_file.exists(): files.append(('config/backup.json',config_file.read_bytes()))
        for folder in (ROOT/'storage/checkpoints',ROOT/'storage/artifacts'):
            if folder.exists():
                for p in folder.rglob('*'):
                    if p.is_file(): files.append((p.relative_to(ROOT).as_posix(),p.read_bytes()))
    finally: mem.journal_unlock(lock)
    fingerprint=hashlib.sha256()
    for name,data in sorted([('memoryos.sqlite3',database),('events.jsonl',journal),*files]):
        fingerprint.update(name.encode());fingerprint.update(b'\0');fingerprint.update(len(data).to_bytes(8,'big'));fingerprint.update(data)
    after_flush=hashlib.sha256()
    for name,data in sorted([('memoryos.sqlite3',database),('events.jsonl',b''),*files]):
        after_flush.update(name.encode());after_flush.update(b'\0');after_flush.update(len(data).to_bytes(8,'big'));after_flush.update(data)
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode='w:gz') as tar:
        entries=[('memoryos.sqlite3',database),('events.jsonl',journal),*files]
        for name,data in entries:
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o600;info.mtime=int(time.time());tar.addfile(info,io.BytesIO(data))
    return buf.getvalue(),journal,fingerprint.hexdigest(),after_flush.hexdigest()

def encrypt(archive,svc):
    r=run([str(CRYPTO),'encrypt',svc],input=archive)
    if not r.stdout.startswith(MAGIC): raise RuntimeError('Encryption did not return a valid Memory OS snapshot.')
    return r.stdout

def run_backup():
    cfg=config();slug=cfg['repository'];repo_private(slug);clear,journal,fingerprint,after_flush=snapshot_archive()
    state_file=ROOT/'storage/backups/last-snapshot.sha256'
    if state_file.exists() and state_file.read_text().strip()==fingerprint:
        print(json.dumps({'status':'unchanged','repository':slug,'local_retention_days':int(cfg.get('retention_days',30))}));return
    svc,keyfile=ensure_key(slug);local=checkout(slug);sealed=encrypt(clear,svc)
    snapshots=local/'snapshots';snapshots.mkdir(exist_ok=True)
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ');dest=snapshots/f'{stamp}.mos'
    seq=1
    while dest.exists(): seq+=1;dest=snapshots/f'{stamp}-{seq}.mos'
    dest.write_bytes(sealed);os.chmod(dest,0o600)
    digest=hashlib.sha256(sealed).hexdigest()
    run(['git','-C',str(local),'add',str(dest.relative_to(local))])
    run(['git','-C',str(local),'commit','-m',f'Encrypted Memory OS snapshot {stamp}'])
    run(['git','-C',str(local),'push','--set-upstream','origin',BRANCH])
    local_sha=run(['git','-C',str(local),'rev-parse','HEAD']).stdout.decode().strip()
    remote=run(['git','ls-remote','--heads',repo_url(slug),BRANCH]).stdout.decode().split()
    if not remote or remote[0]!=local_sha: raise RuntimeError('Remote verification failed; local memory was not pruned.')
    prune_after_backup(journal,int(cfg.get('retention_days',30)))
    state_file.write_text(after_flush+'\n');os.chmod(state_file,0o600)
    print(json.dumps({'status':'uploaded_and_verified','repository':slug,'branch':BRANCH,'snapshot':dest.name,'sha256':digest,'remote_commit':local_sha,'local_retention_days':int(cfg.get('retention_days',30))}))

def prune_after_backup(captured_journal,days):
    # Drop only old raw activity after the encrypted snapshot has been confirmed remotely.
    cutoff=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=max(1,days))).isoformat()
    pruned=0
    with mem.connect() as db:
        db.execute('DELETE FROM events_fts WHERE event_id IN (SELECT id FROM activity_events WHERE occurred_at < ?)',(cutoff,))
        pruned=db.execute('DELETE FROM activity_events WHERE occurred_at < ?',(cutoff,)).rowcount
        db.commit()
    # Remove only the journal prefix that was included in the verified snapshot.
    lock=mem.journal_lock()
    try:
        current=mem.JOURNAL.read_bytes() if mem.JOURNAL.exists() else b''
        if captured_journal and current.startswith(captured_journal):
            remainder=current[len(captured_journal):]
            tmp=mem.JOURNAL.with_suffix('.jsonl.tmp');tmp.write_bytes(remainder);os.chmod(tmp,0o600)
            with tmp.open('rb') as f:os.fsync(f.fileno())
            os.replace(tmp,mem.JOURNAL)
        elif not captured_journal and not current:
            mem.JOURNAL.touch(mode=0o600,exist_ok=True)
    finally: mem.journal_unlock(lock)
    # Reclaim SQLite pages locally after retention cleanup; important tasks, memories,
    # decisions, errors, and checkpoints remain in the database.
    if pruned:
        db=sqlite3.connect(mem.DB,timeout=30)
        try: db.execute('VACUUM')
        finally: db.close()

def decrypt_file(path,svc):
    data=path.read_bytes()
    r=run([str(CRYPTO),'decrypt',svc],input=data)
    return r.stdout

def archive_db(archive):
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tar:
        member=tar.getmember('memoryos.sqlite3');f=tar.extractfile(member)
        if f is None: raise RuntimeError('Snapshot has no database.')
        return f.read(),tar

def merge_database(base,old):
    # Merge all durable rows, not only events. A fresh installation can therefore
    # recover tasks, decisions, errors and checkpoints from earlier snapshots.
    tables=('projects','agents','workspaces','sessions','tasks','subtasks','artifacts','file_changes','terminal_commands','errors','error_resolutions','decisions','checkpoints','memories','memory_relationships','integrations','verification_results','activity_events')
    newest_column={'projects':'updated_at','sessions':('ended_at','started_at'),'tasks':'updated_at','memories':'updated_at'}
    for table in tables:
        info=list(old.execute(f'PRAGMA table_info({table})'));columns=[r[1] for r in info]
        if not columns: continue
        primary=[r[1] for r in info if r[5]]
        if not primary: continue
        for row in old.execute(f'SELECT * FROM {table}'):
            values=tuple(row);lookup=tuple(values[columns.index(k)] for k in primary)
            where=' AND '.join(f'{k}=?' for k in primary)
            present=base.execute(f'SELECT * FROM {table} WHERE {where}',lookup).fetchone()
            if present is None:
                cur=base.execute(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",values)
                if table=='activity_events' and cur.rowcount:
                    item=dict(zip(columns,values));base.execute('INSERT INTO events_fts VALUES(?,?,?,?)',(item['id'],item['project_id'],item['event_type'],item['payload']))
                continue
            version=newest_column.get(table)
            if version:
                versions=(version,) if isinstance(version,str) else version
                old_version=tuple(present[columns.index(k)] or '' for k in versions)
                new_version=tuple(values[columns.index(k)] or '' for k in versions)
                if new_version>old_version:
                    assignments=','.join(f'{k}=?' for k in columns if k not in primary)
                    if assignments:base.execute(f'UPDATE {table} SET {assignments} WHERE {where}',tuple(values[i] for i,k in enumerate(columns) if k not in primary)+lookup)
    # FTS tables are derived indexes. Copy any missing memory index rows after records.
    tables_in_old={r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'memories_fts' in tables_in_old:
        for row in old.execute('SELECT memory_id,project_id,content,category FROM memories_fts'):
            if not base.execute('SELECT 1 FROM memories_fts WHERE memory_id=?',(row[0],)).fetchone():
                base.execute('INSERT INTO memories_fts VALUES(?,?,?,?)',tuple(row))

def restore_latest():
    cfg=config();slug=cfg['repository'];repo_private(slug);svc,keyfile=ensure_key(slug);local=checkout(slug)
    files=sorted((local/'snapshots').glob('*.mos'))
    if not files: raise RuntimeError('No encrypted snapshots found in the private backup branch.')
    temp_root=Path(tempfile.mkdtemp(prefix='memoryos-restore-',dir=ROOT/'storage/backups'));os.chmod(temp_root,0o700)
    latest_bytes=decrypt_file(files[-1],svc);latest_db,_=archive_db(latest_bytes)
    base_path=temp_root/'latest.sqlite3';base_path.write_bytes(latest_db);os.chmod(base_path,0o600)
    base=sqlite3.connect(base_path);base.row_factory=sqlite3.Row
    # Merge every prior snapshot so local retention and a fresh install cannot drop data.
    for index,path in enumerate(files[:-1]):
        old_bytes=decrypt_file(path,svc);old_db,_=archive_db(old_bytes);old_path=temp_root/f'old-{index}.sqlite3';old_path.write_bytes(old_db);os.chmod(old_path,0o600)
        old=sqlite3.connect(old_path);old.row_factory=sqlite3.Row
        merge_database(base,old)
        old.close()
    base.commit()
    recovered_path=temp_root/'recovered.sqlite3';outdb=sqlite3.connect(recovered_path);base.backup(outdb);outdb.close();base.close()
    recovered=recovered_path.read_bytes()
    # Keep a local pre-restore database copy before changing anything.
    mem.DB.parent.mkdir(parents=True,exist_ok=True)
    pre=ROOT/'storage/backups'/('pre-restore-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3')
    if mem.DB.exists():
        current=sqlite3.connect(mem.DB,timeout=30);copy=sqlite3.connect(pre);current.backup(copy);copy.close();current.close();os.chmod(pre,0o600)
    check_path=temp_root/'integrity.sqlite3';check_path.write_bytes(recovered);check=sqlite3.connect(check_path)
    integrity=check.execute('PRAGMA integrity_check').fetchone();check.close()
    if not integrity or integrity[0]!='ok': raise RuntimeError('Recovered database failed its integrity check.')
    # Checkpoint sidecars before atomically replacing the database.
    if mem.DB.exists():
        c=sqlite3.connect(mem.DB,timeout=30)
        try:
            result=c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if result and result[0]: raise RuntimeError('Database is busy. Close Codex and the Memory OS dashboard, then retry restore.')
        finally:c.close()
    temp=mem.DB.with_suffix('.restore.tmp');temp.write_bytes(recovered);os.chmod(temp,0o600)
    for suffix in ('-wal','-shm'):
        side=Path(str(mem.DB)+suffix)
        if side.exists(): side.unlink()
    os.replace(temp,mem.DB)
    # Merge checkpoint/artifact files from the latest snapshot only; preserve existing copies.
    with tarfile.open(fileobj=io.BytesIO(latest_bytes),mode='r:gz') as tar:
        for member in tar.getmembers():
            if member.name=='config/backup.json' and member.isfile():
                destination=ROOT/'config/backup.json';destination.parent.mkdir(parents=True,exist_ok=True);f=tar.extractfile(member)
                if f:destination.write_bytes(f.read());os.chmod(destination,0o600)
            if member.name.startswith(('storage/checkpoints/','storage/artifacts/')) and member.isfile():
                destination=ROOT/member.name
                if destination.resolve().is_relative_to(ROOT.resolve()):
                    destination.parent.mkdir(parents=True,exist_ok=True);f=tar.extractfile(member)
                    if f:destination.write_bytes(f.read());os.chmod(destination,0o600)
    mem.JOURNAL.parent.mkdir(parents=True,exist_ok=True);mem.JOURNAL.write_bytes(b'');os.chmod(mem.JOURNAL,0o600)
    shutil.rmtree(temp_root,ignore_errors=True)
    print(json.dumps({'status':'restored','snapshot':files[-1].name,'snapshots_merged':len(files),'database':str(mem.DB),'pre_restore_copy':str(pre) if pre.exists() else None}))

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
    c=sub.add_parser('configure');c.add_argument('repository')
    sub.add_parser('run');sub.add_parser('restore-latest')
    args=parser.parse_args()
    try:
        if args.action=='configure':configure(args.repository)
        elif args.action=='run':run_backup()
        else:restore_latest()
    except Exception as e:
        print('Memory OS backup error: '+str(e),file=sys.stderr);sys.exit(1)
if __name__=='__main__':main()
