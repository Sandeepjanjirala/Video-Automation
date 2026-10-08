import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from .config import DATA, setup
from .models import Project


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@contextmanager
def connection():
    setup()
    db = sqlite3.connect(DATA / 'studio.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init():
    with connection() as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, revision INTEGER, body TEXT, approved INTEGER, updated TEXT);
        CREATE TABLE IF NOT EXISTS revisions(project_id TEXT, revision INTEGER, body TEXT, created TEXT, PRIMARY KEY(project_id, revision));
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, project_id TEXT, revision INTEGER, kind TEXT, snapshot TEXT, state TEXT, progress TEXT, result TEXT, created TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, body TEXT);
        CREATE TABLE IF NOT EXISTS usage(day TEXT, provider TEXT, calls INTEGER, PRIMARY KEY(day, provider));
        ''')


def unpack(row):
    return {'id': row['id'], 'revision': row['revision'], 'approved': row['approved'] == row['revision'],
            'updated': row['updated'], 'project': json.loads(row['body'])}


def get_project(pid):
    with connection() as db:
        row = db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
        if not row:
            raise ValueError('Project not found')
        return unpack(row)


def list_projects():
    with connection() as db:
        return [unpack(r) for r in db.execute('SELECT * FROM projects ORDER BY updated DESC')]


def save_project(project, pid=None, expected=None):
    data = Project.model_validate(project).model_dump()
    pid = pid or uuid.uuid4().hex
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
        if old and old['revision'] != expected:
            raise ValueError('This project changed in another tab. Reload before saving.')
        if old and json.loads(old['body']) == data:
            return unpack(old)
        revision = old['revision'] + 1 if old else 1
        body = json.dumps(data, ensure_ascii=False)
        db.execute('INSERT OR REPLACE INTO projects VALUES(?,?,?,?,?)', (pid, revision, body, None, now()))
        db.execute('INSERT INTO revisions VALUES(?,?,?,?)', (pid, revision, body, now()))
    return get_project(pid)


def approve(pid, revision):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
        if not row or row['revision'] != revision:
            raise ValueError('Save and reload the current revision before approving')
        p = Project.model_validate_json(row['body'])
        if not p.scenes or any(not s.narration.strip() or not s.roman.strip() for s in p.scenes):
            raise ValueError('Every scene needs Telugu narration text and Roman Telugu captions')
        if not p.evidence_notes.strip():
            raise ValueError('Add your factual review notes, or label fictional content, before approval')
        db.execute('UPDATE projects SET approved=? WHERE id=?', (revision, pid))
    return get_project(pid)


def asset(aid):
    with connection() as db:
        row = db.execute('SELECT body FROM assets WHERE id=?', (aid,)).fetchone()
    if not row:
        raise ValueError('Media asset not found')
    return json.loads(row['body'])


def add_asset(body):
    with connection() as db:
        db.execute('INSERT INTO assets VALUES(?,?)', (body['id'], json.dumps(body)))
    return body


def asset_path(aid, kind=None):
    meta = asset(aid)
    if kind and meta['kind'] != kind:
        raise ValueError(f'This asset is not {kind}')
    path = (DATA / 'assets' / meta['filename']).resolve()
    if path.parent != (DATA / 'assets').resolve() or not path.is_file():
        raise ValueError('Media file is missing')
    return path


def enqueue(pid, revision, kind, options=None):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
        if not row or row['revision'] != revision:
            raise ValueError('Save the current revision first')
        if kind in ('voice', 'render') and row['approved'] != revision:
            raise ValueError('Approve this exact script revision first')
        body = {'project': json.loads(row['body']), 'options': options or {}}
        jid = uuid.uuid4().hex
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (jid, pid, revision, kind, json.dumps(body), 'queued', 'Waiting for worker', '{}', now(), now()))
    return job(jid)


def job(jid):
    with connection() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
    if not row:
        raise ValueError('Job not found')
    result = dict(row)
    result['snapshot'] = json.loads(result['snapshot'])
    result['result'] = json.loads(result['result'])
    return result


def jobs(pid):
    with connection() as db:
        ids = [r['id'] for r in db.execute('SELECT id FROM jobs WHERE project_id=? ORDER BY created DESC LIMIT 30', (pid,))]
    return [job(i) for i in ids]


def progress(jid, message, state=None, result=None):
    with connection() as db:
        db.execute('UPDATE jobs SET progress=?,updated=? WHERE id=?', (message, now(), jid))
        if state:
            db.execute('UPDATE jobs SET state=?,result=? WHERE id=?', (state, json.dumps(result or {}), jid))


def claim():
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
        if not row:
            return None
        db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?", (now(), row['id']))
    return job(row['id'])


def retry(jid):
    with connection() as db:
        changed = db.execute("UPDATE jobs SET state='queued', progress='Retry queued', result='{}', updated=? WHERE id=? AND state='failed'", (now(), jid)).rowcount
        if not changed:
            raise ValueError('Only failed jobs can be retried')
    return job(jid)


def reserve_ai_call(limit):
    day = now()[:10]
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT calls FROM usage WHERE day=? AND provider=?', (day, 'gemini')).fetchone()
        count = row['calls'] if row else 0
        if count >= limit:
            raise ValueError('Daily Gemini request limit reached. Change Settings or try tomorrow (UTC).')
        db.execute('INSERT OR REPLACE INTO usage VALUES(?,?,?)', (day, 'gemini', count + 1))
