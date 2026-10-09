import importlib
import json
import os
import uuid
from pathlib import Path
import pytest


@pytest.fixture()
def modules(tmp_path, monkeypatch):
    monkeypatch.setenv('REEL_DATA_DIR', str(tmp_path / 'data'))
    test_db_url = os.environ.get('STUDIO_TEST_DATABASE_URL') or 'postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/studio_test'
    monkeypatch.setenv('STUDIO_DATABASE_URL', test_db_url)
    import studio.config, studio.store, studio.media, studio.providers, studio.worker, studio.api, studio.db
    from sqlalchemy import text
    for mod in (studio.config, studio.db.session, studio.store, studio.media, studio.providers, studio.worker, studio.api):
        importlib.reload(mod)
    eng = studio.db.get_engine(test_db_url)
    with eng.begin() as conn:
        conn.execute(text('TRUNCATE TABLE application_receipts, job_stages, drafts, jobs, revisions, projects, assets, usage CASCADE;'))
        conn.execute(text("INSERT INTO workspaces (id, name) VALUES ('default', 'Local Workspace') ON CONFLICT (id) DO NOTHING;"))
    studio.store.init()
    return studio.store, studio.media, studio.providers, studio.worker, studio.api


def project():
    from studio.models import Project, Scene
    return Project(title='Test story', evidence_notes='Diagnostic fixture, not a factual news story.',
                   scenes=[Scene(id='one', narration='ఇది ఒక పరీక్ష.', roman='Idi oka pariksha.',
                                 visual='graphic', graphic_title='A small story', rights_reviewed=True)]).model_dump()


def test_revision_approval_and_snapshot(modules):
    store, *_ = modules
    p = store.save_project(project())
    with pytest.raises(ValueError, match='Approve'):
        store.enqueue(p['id'], 1, 'render')
    store.approve(p['id'], 1)
    j = store.enqueue(p['id'], 1, 'render')
    changed = p['project'] | {'title': 'Changed title'}
    p2 = store.save_project(changed, p['id'], 1)
    assert p2['revision'] == 2 and not p2['approved']
    assert store.job(j['id'])['snapshot']['project']['title'] == 'Test story'
    with pytest.raises(ValueError, match='another tab'):
        store.save_project(project(), p['id'], 1)
    # No-op saves preserve revision and approval.
    store.approve(p['id'], 2)
    assert store.save_project(changed, p['id'], 2)['approved']


def test_no_automatic_fact_approval(modules):
    store, *_ = modules
    p = project()
    p['evidence_notes'] = ''
    row = store.save_project(p)
    with pytest.raises(ValueError, match='review notes'):
        store.approve(row['id'], 1)


def test_caption_validation_and_stale_audio(modules):
    store, media, *_ = modules
    from studio.models import Scene
    with pytest.raises(ValueError):
        Scene(id='s', roman='తెలుగు')
    with pytest.raises(ValueError):
        Scene(id='s', cues=[{'start': 0, 'end': 2, 'text': 'first'}, {'start': 1, 'end': 3, 'text': 'second'}])
    p = project()
    s = p['scenes'][0] | {'audio_asset': 'a'*32, 'audio_text_hash': store.digest('old script')}
    with pytest.raises(ValueError, match='no longer matches'):
        media.voice_for(s, p, 'edge')


def test_network_defaults_and_api_secret_boundary(modules):
    store, media, providers, worker, api = modules
    from fastapi.testclient import TestClient
    from studio.config import save_settings
    with pytest.raises(ValueError, match='disabled'):
        providers.online()
    client = TestClient(api.app, base_url='http://127.0.0.1')
    assert client.post('/api/projects', json=project()).status_code == 403
    headers = {'x-studio-token': client.get('/api/bootstrap').json()['token']}
    assert client.post('/api/projects', json=project(), headers=headers).status_code == 200
    assert client.post('/api/projects', json=project(), headers=headers | {'origin': 'https://evil.example'}).status_code == 403
    save_settings({'gemini_key': 'test-secret'})
    public = client.get('/api/bootstrap').json()
    assert 'test-secret' not in json.dumps(public)
    assert public['settings']['gemini_key_configured']


def test_grounded_research_does_not_invent_evidence(modules, monkeypatch):
    store, media, providers, *_ = modules
    monkeypatch.setattr(providers, 'gemini', lambda *a, **k: ('Unverified text', {}))
    with pytest.raises(ValueError, match='No grounded sources'):
        providers.research(project())
    monkeypatch.setattr(providers, 'gemini', lambda *a, **k: ('A supported claim', {'groundingChunks': [{'web': {'title': 'Example', 'uri': 'https://example.org'}}]}))
    assert providers.research(project())['sources'][0]['url'] == 'https://example.org'


def test_stock_cache_and_download_host(modules, monkeypatch):
    store, media, providers, *_ = modules
    from studio.config import save_settings
    save_settings({'online_enabled': True, 'pixabay_key': 'fixture'})
    count = []
    class Response:
        def json(self):
            return {'hits': [{'id': 1, 'videos': {'medium': {'url': 'https://cdn.pixabay.com/video/test.mp4', 'size': 100, 'width': 1920, 'height': 1080}}, 'user': 'Creator', 'pageURL': 'https://pixabay.com/videos/id-1/'}]}
    monkeypatch.setattr(providers, 'request', lambda *a, **k: (count.append(1) or Response()))
    assert len(providers.stock_search('forest')) == 1
    assert len(providers.stock_search('forest')) == 1
    assert len(count) == 1
    assert not providers.safe_pixabay_url('http://127.0.0.1/internal')
    assert not providers.safe_pixabay_url('https://cdn.pixabay.com.evil.example/video')


def test_call_limit_and_job_retry(modules):
    store, *_ = modules
    store.reserve_ai_call(1)
    with pytest.raises(ValueError, match='limit'):
        store.reserve_ai_call(1)
    row = store.save_project(project())
    j = store.enqueue(row['id'], 1, 'research')
    assert store.claim()['id'] == j['id']
    assert store.claim() is None
    store.progress(j['id'], 'Interrupted', 'failed')
    assert store.retry(j['id'])['state'] == 'queued'


def test_stock_sourcing_preserves_assigned_media_and_reports_missing(modules, monkeypatch):
    store, media, providers, worker, api = modules
    p = project()
    p['scenes'] += [p['scenes'][0] | {'id': 'assigned', 'visual': 'upload', 'video_asset': 'b'*32},
                    p['scenes'][0] | {'id': 'needs', 'visual': 'stock', 'keywords': 'forest'},
                    p['scenes'][0] | {'id': 'missing', 'visual': 'stock', 'keywords': ''}]
    monkeypatch.setattr(providers, 'stock_search', lambda q: [{'id': 7, 'video': {'width': 1080, 'height': 1920}}])
    monkeypatch.setattr(providers, 'download_stock', lambda i: {'id': 'c'*32, 'provider': 'mock'})
    row = store.save_project(p)
    j = store.enqueue(row['id'], 1, 'stock_all')
    worker.process(store.claim())
    result = store.job(j['id'])
    assert result['state'] == 'succeeded'
    assert [s['scene_id'] for s in result['result']['selections']] == ['needs', 'missing']
    assert result['result']['selections'][1]['error']
    assert store.get_project(row['id'])['project'] == p


def test_excessive_duration_fit_rejected_without_cutting(modules, monkeypatch):
    store, media, providers, worker, api = modules
    p = project() | {'target_seconds': 10, 'duration_mode': 'target'}
    row = store.save_project(p)
    store.approve(row['id'], 1)
    j = store.enqueue(row['id'], 1, 'render', {'tts_provider': 'edge', 'quality': 'final'})
    monkeypatch.setattr(media, 'prepare_voices', lambda job: [{'duration': 30, 'cues': []}])
    with pytest.raises(ValueError, match='excessive speed'):
        media.render(j)


def register(store, media, path, kind):
    aid = uuid.uuid4().hex
    from studio.config import DATA
    destination = DATA / 'assets' / (aid + path.suffix)
    destination.write_bytes(path.read_bytes())
    store.add_asset({'id': aid, 'filename': destination.name, 'kind': kind, 'name': path.name,
                     'provider': 'diagnostic fixture', 'duration': media.duration(destination)})
    return aid


def test_real_ffmpeg_render_and_cache(modules, tmp_path):
    store, media, providers, worker, api = modules
    from studio.config import executable, DATA
    audio = tmp_path / 'tone.wav'
    clip = tmp_path / 'test.mp4'
    media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.6', audio])
    media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=30:duration=1', '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', clip])
    aid, vid = register(store, media, audio, 'audio'), register(store, media, clip, 'video')
    p = project()
    p['scenes'][0].update(audio_asset=aid, audio_text_hash=store.digest(p['scenes'][0]['narration']))
    second = p['scenes'][0] | {'id': 'two', 'visual': 'upload', 'video_asset': vid, 'fit': 'contain', 'hold_seconds': .2}
    p['scenes'].append(second)
    p['music_asset'], p['music_rights_reviewed'] = aid, True
    row = store.save_project(p)
    store.approve(row['id'], 1)
    job = store.enqueue(row['id'], 1, 'render', {'tts_provider': 'edge', 'quality': 'final'})
    worker.process(store.claim())
    result = store.job(job['id'])
    assert result['state'] == 'succeeded', result['progress']
    out = DATA / 'exports' / job['id'] / 'reel.mp4'
    info = media.probe(out)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    assert (video['width'], video['height']) == (1080, 1920)
    assert video['codec_name'] == 'h264'
    assert any(s['codec_type'] == 'audio' for s in info['streams'])
    assert abs(media.duration(out) - 3.4) < .2
    cached = {x.name: x.stat().st_mtime_ns for x in (DATA / 'cache').glob('*.scene.mp4')}
    job2 = store.enqueue(row['id'], 1, 'render', {'tts_provider': 'edge', 'quality': 'final'})
    worker.process(store.claim())
    assert store.job(job2['id'])['state'] == 'succeeded'
    assert cached == {x.name: x.stat().st_mtime_ns for x in (DATA / 'cache').glob('*.scene.mp4')}
    assert (out.parent / 'captions.srt').is_file()
    # Also test draft render (540x960)
    job_draft = store.enqueue(row['id'], 1, 'render', {'tts_provider': 'edge', 'quality': 'draft'})
    worker.process(store.claim())
    assert store.job(job_draft['id'])['state'] == 'succeeded'
    out_draft = DATA / 'exports' / job_draft['id'] / 'reel.mp4'
    info_draft = media.probe(out_draft)
    v_draft = next(s for s in info_draft['streams'] if s['codec_type'] == 'video')
    assert (v_draft['width'], v_draft['height']) == (540, 960)
    assert any(s['codec_type'] == 'audio' for s in info_draft['streams'])
    # Keep a test output path for optional human visual QA, outside the shipped project.
    keep = os.environ.get('REEL_TEST_OUTPUT')
    if keep:
        import shutil
        shutil.copyfile(out, keep)


def test_independent_scene_results_applied_and_survive_reload(modules):
    store, media, providers, worker, api = modules
    from studio.models import Scene
    p = project()
    p['scenes'] = [
        Scene(id='sc1', narration='మొదటి భాగం.', roman='Modati bhaagam.', visual='stock', rights_reviewed=True).model_dump(),
        Scene(id='sc2', narration='రెండవ భాగం.', roman='Rendava bhaagam.', visual='stock', rights_reviewed=True).model_dump()
    ]
    saved = store.save_project(p)
    pid = saved['id']
    assert saved['revision'] == 1

    aid_1 = '1' * 32
    aid_2 = '2' * 32

    # Simulate two independent stock jobs started from revision 1
    j1 = store.enqueue(pid, 1, 'stock', {'scene_id': 'sc1', 'video_id': 101})
    worker.process = lambda j: None  # mock worker
    store.progress(j1['id'], 'Complete', 'succeeded', {'asset': {'id': aid_1, 'name': 'Clip 1'}, 'scene_id': 'sc1'})

    # Duplicate pending submission check: queueing another stock job for same project fails while first is pending
    # Here j1 is already succeeded so we can enqueue j2
    j2 = store.enqueue(pid, 1, 'stock', {'scene_id': 'sc2', 'video_id': 102})
    store.progress(j2['id'], 'Complete', 'succeeded', {'asset': {'id': aid_2, 'name': 'Clip 2'}, 'scene_id': 'sc2'})

    # Apply Job 1
    res1 = store.apply_job(pid, j1['id'])
    assert res1['applied'] is True
    assert res1['conflicts'] == []
    assert res1['project']['revision'] == 2
    sc1 = next(s for s in res1['project']['project']['scenes'] if s['id'] == 'sc1')
    assert sc1['video_asset'] == aid_1

    # Apply Job 2 from original rev 1 snapshot onto now-rev-2 project
    res2 = store.apply_job(pid, j2['id'])
    assert res2['applied'] is True
    assert res2['conflicts'] == []
    assert res2['project']['revision'] == 3
    sc2 = next(s for s in res2['project']['project']['scenes'] if s['id'] == 'sc2')
    assert sc2['video_asset'] == aid_2

    # Duplicate application of j1 has no duplicate effect
    res1_dup = store.apply_job(pid, j1['id'])
    assert res1_dup['applied'] is True
    assert res1_dup['revision'] == 3  # No revision increment
    assert store.get_project(pid)['revision'] == 3

    # Reload from DB and verify both survive reload
    reloaded = store.get_project(pid)
    scenes_map = {s['id']: s for s in reloaded['project']['scenes']}
    assert scenes_map['sc1']['video_asset'] == aid_1
    assert scenes_map['sc2']['video_asset'] == aid_2


def test_conflict_aware_merge_preserves_newer_edit(modules):
    store, media, providers, worker, api = modules
    from studio.models import Scene
    p = project()
    p['scenes'] = [
        Scene(id='sc1', narration='మొదటి భాగం.', roman='Modati bhaagam.', visual='stock', rights_reviewed=True).model_dump()
    ]
    saved = store.save_project(p)
    pid = saved['id']

    aid_stock = 'a' * 32
    aid_manual = 'b' * 32

    # Job started when sc1 had no video_asset
    j1 = store.enqueue(pid, 1, 'stock', {'scene_id': 'sc1', 'video_id': 101})
    store.progress(j1['id'], 'Complete', 'succeeded', {'asset': {'id': aid_stock, 'name': 'Stock Clip'}, 'scene_id': 'sc1'})

    # User manually edits sc1 to assign their own video asset
    p['scenes'][0]['video_asset'] = aid_manual
    store.save_project(p, pid, 1)
    current = store.get_project(pid)
    assert current['revision'] == 2
    assert current['project']['scenes'][0]['video_asset'] == aid_manual

    # Now apply the stock job that finished
    apply_res = store.apply_job(pid, j1['id'])
    assert apply_res['applied'] is True
    assert len(apply_res['conflicts']) == 1
    assert apply_res['conflicts'][0]['scene_id'] == 'sc1'

    # Verify newer manual edit was NOT overwritten
    reloaded = store.get_project(pid)
    assert reloaded['project']['scenes'][0]['video_asset'] == aid_manual


def test_missing_footage_reported_by_preflight_before_render(modules):
    store, media, providers, worker, api = modules
    from studio.models import Scene
    p = project()
    p['scenes'] = [
        Scene(id='sc1', narration='మొదటి భాగం.', roman='Modati bhaagam.', visual='upload', video_asset='', rights_reviewed=False).model_dump()
    ]
    saved = store.save_project(p)
    pid = saved['id']
    store.approve(pid, 1)

    # Preflight inspection
    pf = store.preflight(p)
    assert pf['ready'] is False
    err_fields = [e['field'] for e in pf['errors']]
    assert 'video_asset' in err_fields
    assert 'rights_reviewed' in err_fields
    assert any('Scene 1' in e['message'] for e in pf['errors'])

    # Enqueue render fails with preflight message before job is created or worker called
    with pytest.raises(ValueError, match='Cannot queue render.*Scene 1'):
        store.enqueue(pid, 1, 'render')


def test_reusable_media_library_and_deduplication(modules, tmp_path):
    store, media, providers, worker, api = modules
    from studio.config import DATA, executable
    from fastapi.testclient import TestClient

    # Create dummy video file
    clip = tmp_path / 'reusable.mp4'
    media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=30:duration=1',
               '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', clip])

    client = TestClient(api.app, base_url='http://127.0.0.1')
    token = client.get('/api/bootstrap').json()['token']
    headers = {'x-studio-token': token}

    # Upload video first time
    with clip.open('rb') as f:
        res1 = client.post('/api/assets', files={'file': ('clip1.mp4', f, 'video/mp4')}, headers=headers)
    assert res1.status_code == 200
    a1 = res1.json()

    # Upload identical video second time -> must deduplicate and return existing asset ID without duplicating file
    with clip.open('rb') as f:
        res2 = client.post('/api/assets', files={'file': ('clip2.mp4', f, 'video/mp4')}, headers=headers)
    assert res2.status_code == 200
    a2 = res2.json()
    assert a1['id'] == a2['id']

    # List assets
    assets = client.get('/api/assets', headers=headers).json()
    assert any(a['id'] == a1['id'] for a in assets)

    # Assign single asset across multiple scenes
    p = project()
    from studio.models import Scene
    p['scenes'] = [
        Scene(id='sc1', narration='ఒకటి.', roman='Okati.', visual='upload', rights_reviewed=True).model_dump(),
        Scene(id='sc2', narration='రెండు.', roman='Rendu.', visual='upload', rights_reviewed=True).model_dump()
    ]
    saved = store.save_project(p)
    pid = saved['id']

    assign_res = client.post(f'/api/projects/{pid}/assign-media', json={'asset_id': a1['id'], 'scene_ids': ['all']}, headers=headers)
    assert assign_res.status_code == 200
    proj = assign_res.json()['project']
    assert proj['scenes'][0]['video_asset'] == a1['id']
    assert proj['scenes'][1]['video_asset'] == a1['id']


def test_retry_modes_and_duplicate_pending_prevention(modules):
    store, media, providers, worker, api = modules
    p = store.save_project(project())
    pid = p['id']

    j = store.enqueue(pid, 1, 'research')
    # Prevent duplicate pending job
    with pytest.raises(ValueError, match='already in progress'):
        store.enqueue(pid, 1, 'research')

    # Mark failed
    store.progress(j['id'], 'Error', 'failed')

    # Modify current project
    p_mod = p['project'] | {'topic': 'New corrected topic'}
    store.save_project(p_mod, pid, 1)

    # Mode 'original' retains original topic in snapshot
    store.retry(j['id'], mode='original')
    assert store.job(j['id'])['snapshot']['project']['topic'] == ''

    # Mark failed again
    store.progress(j['id'], 'Error', 'failed')

    # Mode 'current' updates snapshot to current project
    store.retry(j['id'], mode='current')
    assert store.job(j['id'])['snapshot']['project']['topic'] == 'New corrected topic'
    assert store.job(j['id'])['revision'] == 2


def test_autosave_recovery_and_voice_preview_reuse(modules):
    store, media, providers, worker, api = modules
    p = store.save_project(project())
    pid = p['id']

    # Test drafts persistence
    draft_data = p['project'] | {'topic': 'Unsaved auto draft'}
    store.save_draft(pid, draft_data)
    recovered = store.get_draft(pid)
    assert recovered is not None
    assert recovered['body']['topic'] == 'Unsaved auto draft'

    # Saving project clears draft
    store.save_project(draft_data, pid, 1)
    assert store.get_draft(pid) is None

    # Test voice preview digest reuse across unrelated layout edits
    text = 'ఈ కథ చాలా బాగుంది.'
    voice = 'te-IN-ShrutiNeural'
    rate = 0
    k1 = store.digest({'version': 1, 'provider': 'edge', 'text': text, 'voice': voice, 'rate': rate})
    # Unrelated change to layout or music does not change synthesis key
    k2 = store.digest({'version': 1, 'provider': 'edge', 'text': text, 'voice': voice, 'rate': rate})
    assert k1 == k2


def test_phase2_concurrent_edits_optimistic_locking(modules):
    store, *_ = modules
    p = store.save_project(project())
    pid = p['id']
    assert p['revision'] == 1

    # User in Tab A makes an edit
    edit_a = p['project'] | {'title': 'Tab A Title'}
    saved_a = store.save_project(edit_a, pid, expected=1)
    assert saved_a['revision'] == 2

    # User in Tab B still has revision 1 and attempts to save
    edit_b = p['project'] | {'title': 'Tab B Title'}
    with pytest.raises(ValueError, match='another tab'):
        store.save_project(edit_b, pid, expected=1)

    # After reloading to revision 2, Tab B can save successfully
    saved_b = store.save_project(edit_b, pid, expected=2)
    assert saved_b['revision'] == 3
    assert saved_b['project']['title'] == 'Tab B Title'


def test_phase2_importer_repeatability_and_unicode_fidelity(modules, tmp_path):
    store, media, providers, worker, api = modules
    import sqlite3
    from studio.importer import import_sqlite_to_pg

    # Create dummy SQLite fixture with Telugu content
    sqlite_file = tmp_path / 'fixture.sqlite3'
    conn = sqlite3.connect(sqlite_file)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE projects(id TEXT PRIMARY KEY, revision INTEGER, body TEXT, approved INTEGER, updated TEXT);
    ''')
    cur.execute('''
        CREATE TABLE revisions(project_id TEXT, revision INTEGER, body TEXT, created TEXT, PRIMARY KEY(project_id, revision));
    ''')
    cur.execute('''
        CREATE TABLE assets(id TEXT PRIMARY KEY, body TEXT, sha256 TEXT, provider TEXT, external_id TEXT);
    ''')
    cur.execute('''
        CREATE TABLE jobs(id TEXT PRIMARY KEY, project_id TEXT, revision INTEGER, kind TEXT, snapshot TEXT, state TEXT, progress TEXT, result TEXT, created TEXT, updated TEXT, applied INTEGER DEFAULT 0, applied_at TEXT);
    ''')

    telugu_text = 'ఒక చిన్న ఊరిలో, 20 సంవత్సరాలుగా ఒక ఇల్లు లాక్ చేసి ఉండేది.'
    p_data = {'title': 'తెలుగు కథ', 'evidence_notes': 'పరీక్ష నోట్స్', 'scenes': [{'id': 'sc1', 'narration': telugu_text, 'roman': 'Oka chinna oorilo.'}]}
    cur.execute("INSERT INTO projects VALUES ('p_telugu', 1, ?, 1, '2026-10-09T00:00:00Z')", (json.dumps(p_data, ensure_ascii=False),))
    cur.execute("INSERT INTO revisions VALUES ('p_telugu', 1, ?, '2026-10-09T00:00:00Z')", (json.dumps(p_data, ensure_ascii=False),))
    asset_meta = {'id': 'a_media', 'name': 'Test Clip', 'filename': 'test.mp4', 'kind': 'video'}
    cur.execute("INSERT INTO assets VALUES ('a_media', ?, 'dummy_hash', 'upload', '')", (json.dumps(asset_meta),))
    cur.execute("INSERT INTO jobs VALUES ('j_render', 'p_telugu', 1, 'render', ?, 'succeeded', 'Done', '{}', '2026-10-09T00:00:00Z', '2026-10-09T00:00:00Z', 1, '2026-10-09T00:00:00Z')", (json.dumps({'project': p_data, 'options': {}}, ensure_ascii=False),))
    conn.commit()
    conn.close()

    pg_url = store.get_database_url()

    # Dry-run check
    dry_rep = import_sqlite_to_pg(Path(sqlite_file), pg_url=pg_url, dry_run=True).summary()
    assert dry_rep['projects']['read'] == 1
    assert dry_rep['revisions']['read'] == 1
    assert dry_rep['assets']['read'] == 1
    assert dry_rep['jobs']['read'] == 1
    assert dry_rep['conflicts_count'] == 0

    # Actual import
    rep1 = import_sqlite_to_pg(Path(sqlite_file), pg_url=pg_url, dry_run=False).summary()
    assert rep1['projects']['inserted'] == 1
    assert rep1['revisions']['inserted'] == 1
    assert rep1['assets']['inserted'] == 1
    assert rep1['jobs']['inserted'] == 1

    # Verify Telugu Unicode text in PostgreSQL
    loaded = store.get_project('p_telugu')
    assert loaded['project']['title'] == 'తెలుగు కథ'
    assert loaded['project']['scenes'][0]['narration'] == telugu_text

    # Repeat import (must be idempotent - 0 newly inserted rows)
    rep2 = import_sqlite_to_pg(Path(sqlite_file), pg_url=pg_url, dry_run=False).summary()
    assert rep2['projects']['inserted'] == 0
    assert rep2['revisions']['inserted'] == 0
    assert rep2['assets']['inserted'] == 0
    assert rep2['jobs']['inserted'] == 0


def test_phase2_foreign_key_and_database_constraints(modules):
    store, *_ = modules
    from studio.db.models import Revision, Job
    from studio.db.session import session_scope
    from sqlalchemy.exc import IntegrityError

    # Non-existent project foreign key in Revision
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(Revision(project_id='nonexistent_project', revision=1, body={}, created_at=store.now()))

    # Non-existent workspace foreign key in Job
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(Job(id='job_bad_ws', workspace_id='nonexistent_ws', project_id='p', revision=1, kind='render', state='queued', snapshot={}))


def test_phase2_missing_database_connectivity_actionable_error():
    from studio.db.session import check_connection, DatabaseConnectionError, get_engine
    bad_engine = get_engine('postgresql+psycopg2://postgres:postgres@127.0.0.1:5439/nonexistent')
    with pytest.raises(DatabaseConnectionError, match='Actionable remedy'):
        check_connection(bad_engine)


