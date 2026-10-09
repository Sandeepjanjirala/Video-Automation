"""Inspect migrated projects in PostgreSQL studio database."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import studio.store as store

store.init()
projects = store.list_projects()
print(f"Total projects in PostgreSQL 'studio': {len(projects)}")

for p in projects:
    pid = p['id']
    rev = p['revision']
    proj_body = p['project']
    title = proj_body.get('title', 'Untitled')
    scenes = proj_body.get('scenes', [])
    pf = store.preflight(proj_body)
    print(f"Project ID: {pid} | Rev: {rev} | Title: '{title}' | Scenes: {len(scenes)} | Preflight Ready: {pf['ready']}")
    if not pf['ready']:
        errors = [e['message'] for e in pf['errors'][:3]]
        print(f"   Errors ({len(pf['errors'])}): {errors}")
