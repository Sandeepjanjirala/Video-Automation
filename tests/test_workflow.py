import importlib
import json
import os
import uuid
from pathlib import Path
import pytest


@pytest.fixture()
def modules(tmp_path, monkeypatch):
    monkeypatch.setenv('REEL_DATA_DIR', str(tmp_path / 'data'))
    import studio.config, studio.store, studio.media, studio.providers, studio.worker, studio.api
    for mod in (studio.config, studio.store, studio.media, studio.providers, studio.worker, studio.api):
        importlib.reload(mod)
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
    # Keep a test output path for optional human visual QA, outside the shipped project.
    keep = os.environ.get('REEL_TEST_OUTPUT')
    if keep:
        import shutil
        shutil.copyfile(out, keep)
