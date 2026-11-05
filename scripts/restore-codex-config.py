#!/usr/bin/env python3
"""Restore the newest configuration snapshots created by Memory OS."""
from pathlib import Path
import shutil, os, sys
codex=Path.home()/'.codex'
for name in ('config.toml','hooks.json'):
    files=sorted(codex.glob(name+'.*.bak.memoryos'), key=lambda p:p.stat().st_mtime, reverse=True)
    if not files:
        print('No Memory OS backup found for '+name,file=sys.stderr);sys.exit(1)
    shutil.copy2(files[0],codex/name);os.chmod(codex/name,0o600)
    print('Restored '+name+' from '+files[0].name)
