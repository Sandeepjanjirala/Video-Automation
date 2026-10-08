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
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, project_id TEXT, revision INTEGER, kind TEXT, snapshot TEXT, state TEXT, progress TEXT, result TEXT, created TEXT, updated TEXT, applied INTEGER DEFAULT 0, applied_at TEXT);
        CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, body TEXT, sha256 TEXT, provider TEXT, external_id TEXT);
        CREATE TABLE IF NOT EXISTS usage(day TEXT, provider TEXT, calls INTEGER, PRIMARY KEY(day, provider));
        CREATE TABLE IF NOT EXISTS drafts(project_id TEXT PRIMARY KEY, body TEXT, updated TEXT);
        ''')
        # Backwards compatibility: add columns if table was created in an earlier revision
        job_cols = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
        if 'applied' not in job_cols:
            db.execute('ALTER TABLE jobs ADD COLUMN applied INTEGER DEFAULT 0')
        if 'applied_at' not in job_cols:
            db.execute('ALTER TABLE jobs ADD COLUMN applied_at TEXT')
        asset_cols = {row['name'] for row in db.execute('PRAGMA table_info(assets)')}
        if 'sha256' not in asset_cols:
            db.execute('ALTER TABLE assets ADD COLUMN sha256 TEXT')
        if 'provider' not in asset_cols:
            db.execute('ALTER TABLE assets ADD COLUMN provider TEXT')
        if 'external_id' not in asset_cols:
            db.execute('ALTER TABLE assets ADD COLUMN external_id TEXT')


def unpack(row):
    return {'id': row['id'], 'revision': row['revision'], 'approved': row['approved'] == row['revision'],
            'updated': row['updated'], 'project': json.loads(row['body'])}


def compute_script_hash(project_dict):
    scenes = project_dict.get('scenes', [])
    content = [{'narration': s.get('narration', ''), 'roman': s.get('roman', '')} for s in scenes]
    content.append({'notes': project_dict.get('evidence_notes', '')})
    return digest(content)


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
        if old:
            old_data = json.loads(old['body'])
            if compute_script_hash(old_data) == compute_script_hash(data):
                data['script_approved_hash'] = old_data.get('script_approved_hash', '')
            if old_data == data:
                return unpack(old)
        revision = old['revision'] + 1 if old else 1
        body = json.dumps(data, ensure_ascii=False)
        db.execute('INSERT OR REPLACE INTO projects VALUES(?,?,?,?,?)', (pid, revision, body, None, now()))
        db.execute('INSERT INTO revisions VALUES(?,?,?,?)', (pid, revision, body, now()))
        db.execute('DELETE FROM drafts WHERE project_id=?', (pid,))
    return get_project(pid)


def save_draft(pid, draft_data):
    body = json.dumps(draft_data, ensure_ascii=False)
    ts = now()
    with connection() as db:
        db.execute('INSERT OR REPLACE INTO drafts VALUES(?,?,?)', (pid, body, ts))
    return {'project_id': pid, 'updated': ts}


def get_draft(pid):
    with connection() as db:
        row = db.execute('SELECT * FROM drafts WHERE project_id=?', (pid,)).fetchone()
        if not row:
            return None
        return {'project_id': pid, 'body': json.loads(row['body']), 'updated': row['updated']}


def delete_draft(pid):
    with connection() as db:
        db.execute('DELETE FROM drafts WHERE project_id=?', (pid,))


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
        p_dict = p.model_dump()
        p_dict['script_approved_hash'] = compute_script_hash(p_dict)
        body = json.dumps(p_dict, ensure_ascii=False)
        db.execute('UPDATE projects SET approved=?, body=? WHERE id=?', (revision, body, pid))
    return get_project(pid)


def asset(aid):
    with connection() as db:
        row = db.execute('SELECT body FROM assets WHERE id=?', (aid,)).fetchone()
    if not row:
        raise ValueError('Media asset not found')
    return json.loads(row['body'])


def add_asset(body):
    sha = body.get('sha256') or ''
    prov = body.get('provider') or ''
    ext_id = str(body.get('external_id') or '')
    with connection() as db:
        db.execute('INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?)', (body['id'], json.dumps(body), sha, prov, ext_id))
    return body


def find_asset(sha256=None, provider=None, external_id=None):
    with connection() as db:
        if sha256:
            row = db.execute('SELECT body FROM assets WHERE sha256=?', (sha256,)).fetchone()
            if row:
                res = json.loads(row['body'])
                if (DATA / 'assets' / res['filename']).is_file():
                    return res
        if provider and external_id:
            row = db.execute('SELECT body FROM assets WHERE provider=? AND external_id=?', (provider, str(external_id))).fetchone()
            if row:
                res = json.loads(row['body'])
                if (DATA / 'assets' / res['filename']).is_file():
                    return res
    return None


def list_assets(kind=None):
    with connection() as db:
        rows = db.execute('SELECT body FROM assets ORDER BY rowid DESC').fetchall()
    results = []
    for r in rows:
        item = json.loads(r['body'])
        if not kind or item.get('kind') == kind:
            results.append(item)
    return results


def asset_path(aid, kind=None):
    if not aid:
        raise ValueError('No media asset ID specified')
    meta = asset(aid)
    if kind and meta['kind'] != kind:
        raise ValueError(f'This asset is not {kind}')
    path = (DATA / 'assets' / meta['filename']).resolve()
    if path.parent != (DATA / 'assets').resolve() or not path.is_file():
        raise ValueError(f'Media file {meta.get("name") or aid} is missing on disk')
    return path


def preflight(project_data):
    if isinstance(project_data, str):
        project_data = json.loads(project_data)
    errors = []
    warnings = []
    scenes = project_data.get('scenes', [])
    if not scenes:
        errors.append({'scene_index': 0, 'scene_id': '', 'field': 'scenes', 'message': 'Add at least one scene before rendering'})

    for i, s in enumerate(scenes):
        idx = i + 1
        sid = s.get('id', '')
        if not s.get('narration', '').strip() or not s.get('roman', '').strip():
            errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'narration',
                           'message': f'Scene {idx}: Missing Telugu narration or Roman captions'})
        if s.get('visual') == 'graphic':
            if not s.get('graphic_title', '').strip():
                errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'graphic_title',
                               'message': f'Scene {idx} (graphic title card): Needs a title'})
        else:
            vid = s.get('video_asset')
            if not vid:
                errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'video_asset',
                               'message': f'Scene {idx}: Missing video footage (upload footage, pick stock, or choose title card)'})
            else:
                try:
                    vpath = asset_path(vid, 'video')
                    from .media import duration as media_duration
                    dur = media_duration(vpath)
                    trim = float(s.get('trim_start', 0))
                    if trim < 0:
                        errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'trim_start',
                                       'message': f'Scene {idx}: Invalid negative trim start ({trim}s)'})
                    elif trim >= dur:
                        errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'trim_start',
                                       'message': f'Scene {idx}: Trim start ({trim:.1f}s) exceeds clip duration ({dur:.1f}s)'})
                except Exception as exc:
                    errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'video_asset',
                                   'message': f'Scene {idx}: Video footage error ({exc})'})

        if not s.get('rights_reviewed'):
            errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'rights_reviewed',
                           'message': f'Scene {idx}: Visual relevance and rights review checkbox not confirmed'})

        if s.get('audio_asset'):
            try:
                asset_path(s['audio_asset'], 'audio')
                if s.get('audio_text_hash') != digest(s.get('narration', '')):
                    errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'audio_text_hash',
                                   'message': f'Scene {idx}: Uploaded narration does not match current script text'})
            except Exception as exc:
                errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'audio_asset',
                               'message': f'Scene {idx}: Audio narration file error ({exc})'})

    if project_data.get('music_asset'):
        try:
            asset_path(project_data['music_asset'], 'audio')
            if not project_data.get('music_rights_reviewed'):
                errors.append({'scene_index': 0, 'scene_id': '', 'field': 'music_rights_reviewed',
                               'message': 'Confirm background music rights before rendering'})
        except Exception as exc:
            errors.append({'scene_index': 0, 'scene_id': '', 'field': 'music_asset',
                           'message': f'Background music file error ({exc})'})

    # Script approval check
    expected_hash = compute_script_hash(project_data)
    approved_hash = project_data.get('script_approved_hash', '')
    if approved_hash != expected_hash:
        warnings.append({'field': 'script_approved_hash',
                         'message': 'Script has changed since last approval. Re-approve before final export.'})

    return {'ready': len(errors) == 0, 'errors': errors, 'warnings': warnings}


def enqueue(pid, revision, kind, options=None):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        # Check for duplicate pending job of the same kind on this project
        pending = db.execute("SELECT id FROM jobs WHERE project_id=? AND kind=? AND state IN ('queued', 'running')",
                             (pid, kind)).fetchone()
        if pending:
            raise ValueError(f'A {kind} job is already in progress for this project')

        row = db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
        if not row or row['revision'] != revision:
            raise ValueError('Save the current revision first')
        if kind in ('voice', 'render') and row['approved'] != revision:
            raise ValueError('Approve this exact script revision first')

        proj = json.loads(row['body'])
        if kind == 'render':
            pf = preflight(proj)
            if not pf['ready']:
                msg = pf['errors'][0]['message'] if pf['errors'] else 'Preflight checks failed'
                raise ValueError(f'Cannot queue render: {msg}')

        body = {'project': proj, 'options': options or {}}
        jid = uuid.uuid4().hex
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (jid, pid, revision, kind, json.dumps(body), 'queued', 'Waiting for worker', '{}', now(), now(), 0, None))
    return job(jid)


def job(jid):
    with connection() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
    if not row:
        raise ValueError('Job not found')
    result = dict(row)
    result['snapshot'] = json.loads(result['snapshot'])
    result['result'] = json.loads(result['result'])
    result['applied'] = bool(row['applied']) if 'applied' in row.keys() else False
    result['applied_at'] = row['applied_at'] if 'applied_at' in row.keys() else None
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


def retry(jid, mode='original'):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise ValueError('Job not found')
        if row['state'] != 'failed':
            raise ValueError('Only failed jobs can be retried')

        if mode == 'current':
            p_row = db.execute("SELECT * FROM projects WHERE id=?", (row['project_id'],)).fetchone()
            if not p_row:
                raise ValueError('Project not found')
            current_proj = json.loads(p_row['body'])
            current_rev = p_row['revision']
            snap = json.loads(row['snapshot'])
            snap['project'] = current_proj
            db.execute("UPDATE jobs SET snapshot=?, revision=?, state='queued', progress='Retry queued (current project)', result='{}', updated=? WHERE id=?",
                       (json.dumps(snap), current_rev, now(), jid))
        else:
            db.execute("UPDATE jobs SET state='queued', progress='Retry queued (original inputs)', result='{}', updated=? WHERE id=?",
                       (now(), jid))
    return job(jid)


def apply_job(pid, jid, force=False):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        j_row = db.execute('SELECT * FROM jobs WHERE id=? AND project_id=?', (jid, pid)).fetchone()
        if not j_row:
            raise ValueError('Job not found for this project')
        j = dict(j_row)
        j['snapshot'] = json.loads(j['snapshot'])
        j['result'] = json.loads(j['result'])
        applied = bool(j_row['applied']) if 'applied' in j_row.keys() else False

        p_row = db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
        if not p_row:
            raise ValueError('Project not found')
        current_project = json.loads(p_row['body'])
        current_rev = p_row['revision']

        if applied:
            return {'project': unpack(p_row), 'applied': True, 'conflicts': [],
                    'revision': current_rev,
                    'message': 'Job results were already applied.'}

        if j['state'] != 'succeeded':
            raise ValueError(f"Job is in state '{j['state']}'; only succeeded jobs can be applied")

        kind = j['kind']
        conflicts = []
        modified = False
        snap_project = j['snapshot']['project']
        base_scenes = {s['id']: s for s in snap_project.get('scenes', [])}
        curr_scenes = {s['id']: s for s in current_project.get('scenes', [])}

        if kind == 'stock':
            target_scene_id = j['snapshot']['options'].get('scene_id')
            asset_info = j['result'].get('asset')
            if not target_scene_id or not asset_info:
                raise ValueError('Invalid stock job result')
            if target_scene_id not in curr_scenes:
                conflicts.append({'scene_id': target_scene_id, 'reason': 'Scene was deleted from the project'})
            else:
                curr_s = curr_scenes[target_scene_id]
                base_s = base_scenes.get(target_scene_id, {})
                base_asset = base_s.get('video_asset', '')
                current_asset = curr_s.get('video_asset', '')
                if current_asset and current_asset != base_asset and current_asset != asset_info['id'] and not force:
                    conflicts.append({'scene_id': target_scene_id,
                                      'reason': 'Scene has a newer manual video assignment; edit was preserved'})
                else:
                    curr_s['video_asset'] = asset_info['id']
                    curr_s['visual'] = 'stock'
                    curr_s['rights_reviewed'] = True
                    curr_s['trim_start'] = 0
                    modified = True

        elif kind == 'stock_all':
            selections = j['result'].get('selections', [])
            for sel in selections:
                sid = sel.get('scene_id')
                asset_info = sel.get('asset')
                if not sid or not asset_info:
                    continue
                if sid not in curr_scenes:
                    conflicts.append({'scene_id': sid, 'reason': 'Scene was deleted from project'})
                    continue
                curr_s = curr_scenes[sid]
                base_s = base_scenes.get(sid, {})
                base_asset = base_s.get('video_asset', '')
                current_asset = curr_s.get('video_asset', '')
                if current_asset and current_asset != base_asset and current_asset != asset_info['id'] and not force:
                    conflicts.append({'scene_id': sid,
                                      'reason': f'Scene {sid} has a newer manual video selection; edit was preserved'})
                else:
                    curr_s['video_asset'] = asset_info['id']
                    curr_s['visual'] = 'stock'
                    curr_s['rights_reviewed'] = True
                    curr_s['trim_start'] = 0
                    modified = True

        elif kind == 'research':
            current_project['research_report'] = j['result'].get('text', '')
            current_project['research_sources'] = j['result'].get('sources', [])
            current_project['research_grounding'] = j['result'].get('grounding', {})
            modified = True

        elif kind == 'draft':
            draft_scenes = j['result'].get('scenes', [])
            if not draft_scenes:
                raise ValueError('Draft result contains no scenes')
            # Check if user made custom scene edits since base snapshot
            custom_edits = False
            for sid, s in curr_scenes.items():
                if sid not in base_scenes or s.get('narration') != base_scenes[sid].get('narration'):
                    custom_edits = True
                    break
            if custom_edits and not force:
                conflicts.append({'scene_id': 'all',
                                  'reason': 'Current project has newer script edits; draft replacement skipped to prevent overwrite'})
            else:
                current_project['scenes'] = draft_scenes
                modified = True

        else:
            raise ValueError(f"Job kind '{kind}' does not produce an applicable storyboard result")

        new_rev = current_rev
        if modified:
            new_rev = current_rev + 1
            validated_project = Project.model_validate(current_project).model_dump()
            body_str = json.dumps(validated_project, ensure_ascii=False)
            db.execute('UPDATE projects SET revision=?, body=?, approved=NULL, updated=? WHERE id=?',
                       (new_rev, body_str, now(), pid))
            db.execute('INSERT INTO revisions VALUES(?,?,?,?)', (pid, new_rev, body_str, now()))
            db.execute('DELETE FROM drafts WHERE project_id=?', (pid,))

        db.execute('UPDATE jobs SET applied=1, applied_at=? WHERE id=?', (now(), jid))
    return {'project': get_project(pid), 'applied': True, 'conflicts': conflicts,
            'revision': new_rev}


def reserve_ai_call(limit):
    day = now()[:10]
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT calls FROM usage WHERE day=? AND provider=?', (day, 'gemini')).fetchone()
        count = row['calls'] if row else 0
        if count >= limit:
            raise ValueError('Daily Gemini request limit reached. Change Settings or try tomorrow (UTC).')
        db.execute('INSERT OR REPLACE INTO usage VALUES(?,?,?)', (day, 'gemini', count + 1))

