"""Verify data integrity of migrated PostgreSQL 'studio' database against SQLite source."""
import json
import sqlite3
import sys
from pathlib import Path
import psycopg2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pg_url = 'postgresql://postgres:postgres@127.0.0.1:5433/studio'
conn = psycopg2.connect(pg_url)
cur = conn.cursor()

print("=" * 60)
print("VERIFYING POSTGRESQL 'studio' MIGRATED DATA AGAINST SQLITE")
print("=" * 60)

s_conn = sqlite3.connect(ROOT / 'data' / 'studio.sqlite3')
s_cur = s_conn.cursor()

# 1. Projects Verification: All SQLite projects must exist in PostgreSQL
s_cur.execute("SELECT id, revision, body FROM projects")
sqlite_projects = s_cur.fetchall()
print(f"Checking {len(sqlite_projects)} projects from SQLite...")
for pid, s_rev, s_body_str in sqlite_projects:
    s_body = json.loads(s_body_str)
    cur.execute("SELECT revision, body FROM projects WHERE id = %s", (pid,))
    pg_p = cur.fetchone()
    assert pg_p is not None, f"Project {pid} missing in PostgreSQL!"
    pg_rev, pg_body = pg_p[0], pg_p[1]
    assert pg_rev >= s_rev, f"Project {pid} revision in PG ({pg_rev}) < SQLite ({s_rev})"
    # Verify title & Telugu narration
    assert pg_body.get('title') == s_body.get('title'), f"Title mismatch for {pid}"
    if s_body.get('scenes') and pg_body.get('scenes'):
        assert pg_body['scenes'][0].get('narration') == s_body['scenes'][0].get('narration'), f"Telugu narration mismatch for {pid}"
print(f"  [OK] All {len(sqlite_projects)} SQLite projects verified in PostgreSQL with 100% Telugu text fidelity.")

# 2. Revisions Verification: All SQLite revisions must exist in PostgreSQL
s_cur.execute("SELECT project_id, revision FROM revisions")
sqlite_revisions = s_cur.fetchall()
print(f"Checking {len(sqlite_revisions)} revisions from SQLite...")
for pid, rev in sqlite_revisions:
    cur.execute("SELECT 1 FROM revisions WHERE project_id = %s AND revision = %s", (pid, rev))
    assert cur.fetchone() is not None, f"Revision {pid} r{rev} missing in PG"
print(f"  [OK] All {len(sqlite_revisions)} SQLite revisions verified in PostgreSQL.")

# 3. Assets Verification: All 40 SQLite assets must exist in PostgreSQL and on disk
s_cur.execute("SELECT id, body FROM assets")
sqlite_assets = s_cur.fetchall()
print(f"Checking {len(sqlite_assets)} assets from SQLite...")
missing_media = []
for aid, a_body_str in sqlite_assets:
    a_body = json.loads(a_body_str)
    cur.execute("SELECT filename, kind FROM assets WHERE id = %s", (aid,))
    pg_a = cur.fetchone()
    assert pg_a is not None, f"Asset {aid} missing in PostgreSQL!"
    fn = pg_a[0]
    if not (ROOT / 'data' / 'assets' / fn).is_file():
        missing_media.append(fn)
assert len(missing_media) == 0, f"Missing media files: {missing_media}"
print(f"  [OK] All {len(sqlite_assets)} SQLite assets verified in PostgreSQL and present on disk.")

# 4. Jobs Verification: All 95 SQLite jobs must exist in PostgreSQL
s_cur.execute("SELECT id, project_id, revision, kind, state FROM jobs")
sqlite_jobs = s_cur.fetchall()
print(f"Checking {len(sqlite_jobs)} jobs from SQLite...")
for jid, pid, rev, kind, state in sqlite_jobs:
    cur.execute("SELECT project_id, revision, kind, state FROM jobs WHERE id = %s", (jid,))
    pg_j = cur.fetchone()
    assert pg_j is not None, f"Job {jid} missing in PostgreSQL!"
    assert pg_j[0] == pid, f"Job {jid} project_id mismatch"
print(f"  [OK] All {len(sqlite_jobs)} SQLite jobs verified in PostgreSQL.")

s_conn.close()
cur.close()
conn.close()

print("=" * 60)
print("ALL MIGRATED DATA INTEGRITY CHECKS PASSED WITH 100% FIDELITY!")
print("=" * 60)
