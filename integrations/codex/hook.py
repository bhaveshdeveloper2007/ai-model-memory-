#!/usr/bin/env python3
"""Best-effort Codex lifecycle adapter. Captures metadata only, never prompt/tool output text."""
import json,sys,subprocess,os
from pathlib import Path
engine=Path.home()/'Documents/MemoryOS/engine/memoryos.py'
kind=sys.argv[1] if len(sys.argv)>1 else 'activity'
try: data=json.load(sys.stdin)
except Exception: data={}
payload={'cwd':data.get('cwd') or data.get('working_directory') or os.getcwd(),'event_type':'codex.'+kind.lower(),'agent':'codex','source':'codex_hook','session_id':data.get('session_id')}
for key in ('tool_name','tool','call_id','turn_id','reason','hook_event_name'):
    if isinstance(data.get(key),(str,int,float,bool)):payload[key]=data[key]
# Paths are useful evidence; avoid recording free-form prompt, command, or tool output text.
for key in ('file_path','path'):
    if isinstance(data.get(key),str):payload[key]=data[key]
try: subprocess.run([sys.executable,str(engine),'event','--json',json.dumps(payload)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3,check=False)
except Exception:pass
