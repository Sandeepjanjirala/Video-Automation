import hashlib
import os
import subprocess
import time
from datetime import datetime, timezone
import psycopg2
from psycopg2.extras import RealDictCursor

timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup_dir = os.path.abspath("backups")
os.makedirs(backup_dir, exist_ok=True)
dump_filename = f"studio_pre_m9_{timestamp}.dump"
dump_path = os.path.join(backup_dir, dump_filename)

print("=== 1. Creating PostgreSQL Backup for Stage M9 ===")
cmd_dump = [
    "docker", "exec", "-i", "studio-postgres",
    "pg_dump", "-U", "postgres", "-Fc", "studio"
]

with open(dump_path, "wb") as f:
    res = subprocess.run(cmd_dump, stdout=f, stderr=subprocess.PIPE)
    if res.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {res.stderr.decode()}")

dump_size = os.path.getsize(dump_path)
hasher = hashlib.sha256()
with open(dump_path, "rb") as f:
    while chunk := f.read(65536):
        hasher.update(chunk)
dump_sha256 = hasher.hexdigest()

print(f"Source Database: studio")
print(f"Dump Timestamp: {timestamp}")
print(f"Dump Path: {dump_path}")
print(f"Dump Size: {dump_size} bytes")
print(f"SHA-256 Checksum: {dump_sha256}")

print("\n=== 2. Creating Disposable Database for Verification ===")
conn_admin = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5433/postgres")
conn_admin.autocommit = True
cur_admin = conn_admin.cursor()

# Terminate existing connections if any
cur_admin.execute("""
    SELECT pg_terminate_backend(pg_stat_activity.pid)
    FROM pg_stat_activity
    WHERE pg_stat_activity.datname = 'studio_m9_disposable'
      AND pid <> pg_backend_pid();
""")
cur_admin.execute("DROP DATABASE IF EXISTS studio_m9_disposable;")
cur_admin.execute("CREATE DATABASE studio_m9_disposable;")
print("Created disposable database: studio_m9_disposable")

print("\n=== 3. Restoring Dump into Disposable Database ===")
cmd_restore = [
    "docker", "exec", "-i", "studio-postgres",
    "pg_restore", "-U", "postgres", "-d", "studio_m9_disposable"
]

with open(dump_path, "rb") as f:
    res_restore = subprocess.run(cmd_restore, stdin=f, stderr=subprocess.PIPE)
    print(f"pg_restore completed with code {res_restore.returncode}")

print("\n=== 4. Comparing Table Counts & Representative Data ===")
tables = [
    "workspaces",
    "projects",
    "revisions",
    "assets",
    "jobs",
    "job_stages",
    "drafts",
    "application_receipts",
    "outbox",
    "usage",
    "voice_takes",
    "alembic_version"
]

conn_src = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5433/studio")
cur_src = conn_src.cursor(cursor_factory=RealDictCursor)

conn_restored = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5433/studio_m9_disposable")
cur_restored = conn_restored.cursor(cursor_factory=RealDictCursor)

match_all = True
for t in tables:
    cur_src.execute(f'SELECT count(*) as c FROM "{t}";')
    cnt_src = cur_src.fetchone()['c']
    cur_restored.execute(f'SELECT count(*) as c FROM "{t}";')
    cnt_rest = cur_restored.fetchone()['c']
    matched = (cnt_src == cnt_rest)
    if not matched:
        match_all = False
    print(f"  Table `{t}`: Source={cnt_src}, Restored={cnt_rest} -> {'MATCH' if matched else 'MISMATCH'}")

# Verify representative data integrity
print("\n--- Verifying Representative Records ---")

# 1. Projects representative record
cur_src.execute('SELECT id, revision, body FROM projects ORDER BY created_at DESC LIMIT 1;')
p_src = cur_src.fetchone()
cur_restored.execute('SELECT id, revision, body FROM projects WHERE id = %s;', [p_src['id']])
p_rest = cur_restored.fetchone()
assert p_src['body'] == p_rest['body'], "Project body mismatch!"
print(f"  Project {p_src['id']} (revision v{p_src['revision']}) verified identical.")

# 2. Telugu script & Unicode fidelity check
cur_src.execute("SELECT id, project_id, body FROM revisions WHERE body::text LIKE '%నమస్కారం%' LIMIT 1;")
r_src = cur_src.fetchone()
if r_src:
    cur_restored.execute("SELECT id, project_id, body FROM revisions WHERE id = %s;", [r_src['id']])
    r_rest = cur_restored.fetchone()
    assert r_src['body'] == r_rest['body'], "Telugu revision body mismatch!"
    print(f"  Telugu revision {r_src['id']} verified with exact Telugu Unicode fidelity.")

# 3. Voice takes
cur_src.execute("SELECT id, project_id, spoken_text, provider, accepted FROM voice_takes LIMIT 1;")
vt_src = cur_src.fetchone()
if vt_src:
    cur_restored.execute("SELECT id, project_id, spoken_text, provider, accepted FROM voice_takes WHERE id = %s;", [vt_src['id']])
    vt_rest = cur_restored.fetchone()
    assert vt_src == vt_rest, "Voice take mismatch!"
    print(f"  Voice take {vt_src['id']} verified identical.")

# 4. Outbox
cur_src.execute("SELECT id, event_type, processed FROM outbox ORDER BY id DESC LIMIT 1;")
ob_src = cur_src.fetchone()
if ob_src:
    cur_restored.execute("SELECT id, event_type, processed FROM outbox WHERE id = %s;", [ob_src['id']])
    ob_rest = cur_restored.fetchone()
    assert ob_src == ob_rest, "Outbox event mismatch!"
    print(f"  Outbox event {ob_src['id']} verified identical.")

cur_src.close()
conn_src.close()
cur_restored.close()
conn_restored.close()

if not match_all:
    raise RuntimeError("Backup verification failed due to table count mismatch!")

print("\n=== 5. Retaining studio_m9_disposable for Schema Testing ===")
cur_admin.close()
conn_admin.close()
print("Disposable database `studio_m9_disposable` is retained for Section 15 testing.")
print(f"VERIFIED BACKUP COMPLETED: {dump_filename}")
