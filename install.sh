#!/bin/bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Memory OS setup currently supports macOS only." >&2
  exit 1
fi
command -v python3 >/dev/null || { echo "Install Python 3 first (python.org or Homebrew)." >&2; exit 1; }
SOURCE="$(cd "$(dirname "$0")" && pwd)"
TARGET="$HOME/Documents/MemoryOS"
mkdir -p "$TARGET"
# Update program files while preserving all local memory data and logs.
rsync -a --exclude='/.git/' --exclude='/storage/' --exclude='/logs/' --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.sqlite3*' --exclude='*.jsonl' "$SOURCE/" "$TARGET/"
mkdir -p "$TARGET/storage/journal" "$TARGET/storage/database" "$TARGET/storage/artifacts" "$TARGET/storage/checkpoints" "$TARGET/storage/backups" "$TARGET/logs" "$HOME/.codex"
chmod 700 "$TARGET/storage" "$TARGET/storage/journal" "$TARGET/storage/database" "$TARGET/storage/artifacts" "$TARGET/storage/checkpoints" "$TARGET/storage/backups" "$TARGET/logs"

python3 - "$TARGET" <<'PY'
from pathlib import Path
import datetime, json, os, re, shutil, sys, tomllib
root=Path(sys.argv[1]); home=Path.home(); codex=home/'.codex'; config=codex/'config.toml'; hooks=codex/'hooks.json'
changes=[]
if config.exists():
    text=config.read_text()
else:
    text=''
desired=f'[mcp_servers.memory_os]\ncommand = "/usr/bin/python3"\nargs = ["{root}/engine/memoryos.py", "mcp"]\n'
match=re.search(r'(?ms)^\[mcp_servers\.memory_os\]\s*\n.*?(?=^\[|\Z)',text)
if not match or match.group(0).strip()!=desired.strip():
    changes.append('config')
if hooks.exists():
    hook_data=json.loads(hooks.read_text())
else:
    hook_data={}
if not isinstance(hook_data,dict): raise SystemExit('Codex hooks file must contain a JSON object; left it unchanged.')
h=hook_data.setdefault('hooks',{})
script=root/'integrations/codex/hook.py'
for event in ('SessionStart','UserPromptSubmit','PostToolUse','SessionEnd','Stop'):
    arr=h.setdefault(event,[])
    if not any(any(x.get('command','').endswith('hook.py '+event) for x in group.get('hooks',[])) for group in arr if isinstance(group,dict)):
        changes.append('hooks'); break
stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
if 'config' in changes and config.exists():
    dst=codex/f'config.toml.{stamp}.bak.memoryos'; shutil.copy2(config,dst); os.chmod(dst,0o600)
if 'hooks' in changes and hooks.exists():
    dst=codex/f'hooks.json.{stamp}.bak.memoryos'; shutil.copy2(hooks,dst); os.chmod(dst,0o600)
if 'config' in changes:
    if match: text=text[:match.start()]+desired+'\n'+text[match.end():]
    else: text=text.rstrip()+'\n\n'+desired+'\n'
    config.write_text(text); os.chmod(config,0o600)
hook_data.setdefault('hooks',{})
for event in ('SessionStart','UserPromptSubmit','PostToolUse','SessionEnd','Stop'):
    arr=hook_data['hooks'].setdefault(event,[])
    if not any(any(x.get('command','').endswith('hook.py '+event) for x in group.get('hooks',[])) for group in arr if isinstance(group,dict)):
        arr.append({'hooks':[{'type':'command','command':f'"{script}" {event}'}]})
hooks.write_text(json.dumps(hook_data,indent=2)+'\n'); os.chmod(hooks,0o600)
# Preserve existing global instructions and add Memory OS guidance only once.
agents=codex/'AGENTS.md'; prior=agents.read_text() if agents.exists() else ''
section='''\n\n## Memory OS\n\nAt project session start, call `memory_bootstrap` with the current working directory. Use the returned project recovery context. Record observable decisions, tasks, errors, verification evidence, and checkpoints with Memory OS tools. Never store secrets or private chain-of-thought. Do not claim a task is verified without actual evidence. Continue normally if the MCP server is unavailable.\n'''
if '## Memory OS' not in prior: agents.write_text(prior.rstrip()+section)
PY

python3 "$TARGET/engine/memoryos.py" init
python3 - "$HOME/.codex/config.toml" <<'PY'
import pathlib,sys,tomllib
p=pathlib.Path(sys.argv[1]);tomllib.loads(p.read_text());print('Codex config is valid.')
PY
python3 -m py_compile "$TARGET/engine/memoryos.py" "$TARGET/integrations/codex/hook.py"
printf '\nMemory OS files installed at %s\nRestart Codex to load the MCP tools.\n' "$TARGET"
printf 'Dashboard: %s/scripts/dashboard.sh (then open http://127.0.0.1:8765)\n' "$TARGET"
