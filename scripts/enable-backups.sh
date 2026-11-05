#!/bin/bash
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 OWNER/PRIVATE-REPOSITORY" >&2
  exit 2
fi
command -v gh >/dev/null || { echo "GitHub CLI is required; authenticate it with: gh auth login" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$(command -v python3)"
"$PYTHON_BIN" "$ROOT/scripts/backup.py" configure "$1"
# Perform and verify the first private encrypted snapshot before enabling the schedule.
"$PYTHON_BIN" "$ROOT/scripts/backup.py" run
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS"
"$PYTHON_BIN" - "$ROOT" "$HOME" "$PYTHON_BIN" <<'PY'
from pathlib import Path
import os,plistlib,sys
root=Path(sys.argv[1]);home=Path(sys.argv[2]);python_bin=sys.argv[3];label='com.memoryos.backup'
plist={'Label':label,'ProgramArguments':[python_bin,str(root/'scripts/backup.py'),'run'],'RunAtLoad':True,'StartInterval':900,'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'},'StandardOutPath':str(root/'logs/backup.log'),'StandardErrorPath':str(root/'logs/backup-error.log'),'WorkingDirectory':str(root)}
p=home/'Library/LaunchAgents'/f'{label}.plist';p.write_bytes(plistlib.dumps(plist));os.chmod(p,0o600)
PY
"$PYTHON_BIN" - "$AGENTS/com.memoryos.backup.plist" <<'PY'
import os,subprocess,sys
domain=f'gui/{os.getuid()}';label='com.memoryos.backup';plist=sys.argv[1]
subprocess.run(['launchctl','bootout',f'{domain}/{label}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
subprocess.run(['launchctl','bootstrap',domain,plist],check=True)
PY
echo "Encrypted daily backups are enabled. Keep the recovery key in a separate safe place."
echo "Restore after a crash with: python3 ~/Documents/MemoryOS/scripts/backup.py restore-latest"
