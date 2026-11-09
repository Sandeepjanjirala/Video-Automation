"""Current architecture regression suite; run with scripts/run_audit_tests.py."""
import copy
import json
import concurrent.futures
import uuid
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connections
from django.test import Client
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from core import services
from core.models import Job, VoiceTake
from core.views import DJANGO_TOKEN
from studio import store, media, providers, worker
from studio.config import DATA, executable, save_settings
from studio.models import Project, Scene
from studio.tasks import ExecutionLeaseContext, task_research, task_render_pipeline


@pytest.fixture(autouse=True)
def isolated():
    assert settings.DATABASES['default']['NAME'].startswith('studio_audit_'), 'Use scripts/run_audit_tests.py'
    with connections['default'].cursor() as cur:
        cur.execute('TRUNCATE application_receipts, outbox, job_stages, drafts, jobs, revisions, projects, assets, voice_takes, usage CASCADE')
    store.init()
    save_settings({'online_enabled': False, 'tts_provider': 'edge'})
    yield
    connections.close_all()


def project():
    return Project(title='Audit fixture', evidence_notes='Original diagnostic test.', scenes=[
        Scene(id='scene1', narration='ఇది ఒక పరీక్ష.', roman='Idi oka pariksha.',
              visual='graphic', graphic_title='Automation audit', rights_reviewed=True)
    ]).model_dump()


def saved_job(kind='research'):
    p = store.save_project(project())
    store.approve(p['id'], p['revision'])
    return store.enqueue(p['id'], p['revision'], kind)


def client():
    return Client(HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)


def test_fresh_database_health_and_schema():
    c = client()
    assert c.get('/').status_code == 200
    health = c.get('/api/health')
    assert health.status_code == 200, health.content
    assert health.json()['database']['tables_visible']
    schema = c.get('/api/migration/schema-status')
    assert schema.status_code == 200
    assert schema.json()['schema_authority'] == 'django'


def test_editor_controls_on_rendered_page():
    page = DATA.parent / 'editor.html'
    page.write_bytes(client().get('/').content)
    subprocess.run(['node', 'tests/audit_ui.cjs', str(page)], check=True, timeout=45)


def test_free_male_default_preserves_existing_choices():
    assert Project().tts_provider == 'edge'
    assert Project().voice == 'te-IN-MohanNeural'
    old = project()
    old.update(tts_provider='azure', voice='te-IN-ShrutiNeural')
    assert store.save_project(old)['project']['voice'] == 'te-IN-ShrutiNeural'


def test_scene_prompt_and_source_preferences_survive_revisions():
    body = project()
    body['scenes'][0].update(generation_prompt='Cinematic Andhra landscape', prompt_seconds=5,
                             stock_provider='pexels', short_clip_mode='freeze')
    p = store.save_project(body)
    saved = store.get_project(p['id'])['project']['scenes'][0]
    assert saved['generation_prompt'] == 'Cinematic Andhra landscape'
    assert saved['stock_provider'] == 'pexels'
    assert saved['short_clip_mode'] == 'freeze'


def test_sarvam_conversion_chunks_caches_and_keeps_project_unchanged(monkeypatch):
    from studio.transliteration import convert
    cfg = {'online_enabled': True, 'sarvam_key': 'fixture-key'}
    monkeypatch.setattr(providers, 'settings', lambda: cfg)
    monkeypatch.setattr(providers, 'acquire_rate_slot', lambda *a: True)
    calls = []
    class Response:
        def json(self):
            return {'transliterated_text': 'మన తెలుగు కథ.'}
    def request(method, url, **kw):
        assert url == 'https://api.sarvam.ai/transliterate'
        assert kw['json']['source_language_code'] == 'en-IN'
        assert kw['json']['target_language_code'] == 'te-IN'
        assert len(kw['json']['input']) <= 1000
        calls.append(kw['json']['input'])
        return Response()
    monkeypatch.setattr(providers, 'request', request)
    row = store.save_project(project())
    text = 'Mana telugu katha. ' * 100
    result = convert(text, 'sarvam')
    assert len(calls) == 2
    assert result['roman'] == text.strip()
    assert 'తెలుగు' in result['telugu']
    cfg['online_enabled'] = False
    assert convert(text, 'sarvam')['cached'] is True
    assert len(calls) == 2
    assert store.get_project(row['id'])['revision'] == 1
    with pytest.raises(ValueError, match='disabled'):
        convert('Kotha script ' + uuid.uuid4().hex, 'sarvam')
    assert client().post('/api/voice/transliterate', {'text': [], 'provider': 'sarvam'}, content_type='application/json').status_code == 400


def test_conversion_source_edit_requires_review():
    body = project()
    scene = body['scenes'][0]
    scene['narration_source_roman'] = scene['roman']
    assert store.preflight(body)['ready']
    scene['roman'] = 'Vere Telugu katha.'
    assert any('Roman source changed' in e['message'] for e in store.preflight(body)['errors'])


def test_pixabay_first_pexels_fallback_cache_and_download_hosts(monkeypatch):
    from studio.stock import safe_stock_url
    monkeypatch.setattr(providers, 'settings', lambda: {'online_enabled': True, 'pixabay_key': 'fixture-pixabay', 'pexels_key': 'fixture-pexels'})
    calls = []
    class Response:
        def __init__(self, body): self.body = body
        def json(self): return self.body
    def request(method, url, **kw):
        calls.append(url)
        if 'pixabay' in url:
            return Response({'hits': []})
        assert kw['headers']['Authorization'] == 'fixture-pexels'
        video = {'id': 12345, 'duration': 5, 'url': 'https://www.pexels.com/video/12345/', 'user': {'name': 'Fixture creator'},
                 'video_files': [{'file_type': 'video/mp4', 'width': 1080, 'height': 1920, 'link': 'https://videos.pexels.com/fixture.mp4'}]}
        return Response(video if 'videos/12345' in url else {'videos': [video]})
    monkeypatch.setattr(providers, 'request', request)
    query = 'fixture ' + uuid.uuid4().hex
    hits = providers.stock_search(query)
    assert hits[0]['provider'] == 'pexels'
    assert hits[0]['creator'] == 'Fixture creator'
    assert len(calls) == 2 and 'pixabay' in calls[0]
    assert providers.stock_search(query) == hits
    assert len(calls) == 2
    assert providers.stock_search(video_id=12345, provider='pexels')[0]['id'] == 12345
    assert calls[-1].endswith('/v1/videos/videos/12345')
    assert safe_stock_url('https://videos.pexels.com/fixture.mp4', 'pexels')
    for url in ('http://videos.pexels.com/a.mp4', 'https://videos.pexels.com.evil.test/a',
                'https://127.0.0.1/a', 'https://user@videos.pexels.com/a', 'https://videos.pexels.com:invalid/a'):
        assert not safe_stock_url(url, 'pexels')


def test_pixabay_hit_prevents_unnecessary_pexels_request(monkeypatch):
    monkeypatch.setattr(providers, 'settings', lambda: {'online_enabled': True, 'pixabay_key': 'fixture', 'pexels_key': 'fixture'})
    class Response:
        def json(self):
            return {'hits': [{'id': 678, 'duration': 4, 'user': 'Author', 'pageURL': 'https://pixabay.com/videos/678/',
                              'videos': {'medium': {'url': 'https://cdn.pixabay.com/fixture.mp4', 'size': 1024, 'width': 1080, 'height': 1920}}}]}
    def request(method, url, **kw):
        assert 'pixabay.com' in url
        return Response()
    monkeypatch.setattr(providers, 'request', request)
    assert providers.stock_search(uuid.uuid4().hex)[0]['provider'] == 'pixabay'


def test_stock_download_deduplication_is_provider_scoped(monkeypatch):
    calls = []
    def find(**kwargs):
        calls.append(kwargs)
        return {'id': 'a' * 32, 'provider': kwargs['provider']}
    monkeypatch.setattr(store, 'find_asset', find)
    monkeypatch.setattr(providers, 'stock_search', lambda *a, **k: pytest.fail('Already downloaded'))
    assert providers.download_stock(123, 'pexels')['provider'] == 'pexels'
    assert calls == [{'provider': 'pexels', 'external_id': '123'}]


def test_sarvam_upgrade_detaches_generated_takes_and_preserves_uploads(monkeypatch):
    body = project()
    body['scenes'][0].update(audio_asset='a' * 32, take_id='take_free', audio_text_hash=store.digest(body['scenes'][0]['narration']),
                             cues=[{'start': 0, 'end': 1, 'text': 'Idi'}])
    body['scenes'].append({**body['scenes'][0], 'id': 'manual', 'take_id': '', 'audio_asset': 'b' * 32})
    monkeypatch.setattr(store, 'get_voice_take', lambda tid: {'provider': 'edge', 'asset_id': 'a' * 32})
    p = store.save_project(body)
    body.update(tts_provider='sarvam', voice='shubh')
    updated, _ = services.save_project_mutation(body, p['id'], 1)
    first, manual = updated.body['scenes']
    assert first['audio_asset'] == first['take_id'] == ''
    assert first['cues'] == []
    assert manual['audio_asset'] == 'b' * 32
    calls = []
    monkeypatch.setattr(providers, 'synthesize', lambda **kw: (calls.append(kw) or (Path('new.wav'), [])))
    media.voice_for(first, updated.body, 'sarvam')
    assert calls[0]['provider'] == 'sarvam' and calls[0]['voice'] == 'shubh'


def test_sarvam_tts_contract_uses_supported_speaker_and_controls(monkeypatch, tmp_path):
    import base64
    import io
    import wave
    from studio.voice import sarvam_adapter
    from studio.voice.registry import get_voice_capability
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(b'\x00\x00' * 2400)
    class Response:
        status_code = 200
        def json(self): return {'audios': [base64.b64encode(stream.getvalue()).decode()]}
    calls = []
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, **kw): calls.append(kw); return Response()
    monkeypatch.setattr(sarvam_adapter, 'settings', lambda: {'sarvam_key': 'fixture'})
    monkeypatch.setattr(sarvam_adapter.httpx, 'Client', Client)
    output = tmp_path / 'sample.wav'
    audio, boundaries, meta = sarvam_adapter.SarvamTTSAdapter().synthesize('ఇది తెలుగు.', 'shubh', target_audio=output, target_meta=tmp_path / 'timing.json')
    payload = calls[0]['json']
    assert payload['speaker'] == 'shubh' and payload['model'] == 'bulbul:v3'
    assert payload['language_code'] == 'te-IN'
    assert not {'pitch', 'loudness', 'enable_preprocessing'} & payload.keys()
    assert media.duration(audio) == pytest.approx(0.1)
    assert boundaries and meta['timing_type'] == 'duration_proportional'
    with pytest.raises(ValueError, match='Unsupported voice'):
        get_voice_capability('meera', 'sarvam')


def test_uploaded_image_and_freeze_clip_export_offline(tmp_path):
    picture, video, audio = (tmp_path / n for n in ('scene.png', 'clip.mp4', 'speech.wav'))
    media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=blue:s=320x240', '-frames:v', '1', '-threads', '1', picture])
    media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=30:duration=0.3', '-c:v', 'libx264', video])
    media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=duration=1.3', audio])
    def upload(path):
        res = client().post('/api/assets', {'file': SimpleUploadedFile(path.name, path.read_bytes())})
        assert res.status_code == 200, res.content
        return res.json()
    image, clip, voice = upload(picture), upload(video), upload(audio)
    assert image['kind'] == 'image'
    response = client().get(f"/api/assets/{image['id']}/thumbnail")
    assert response.status_code == 200
    response.close()
    bad = client().post('/api/assets', {'file': SimpleUploadedFile('invalid.png', b'not an image')})
    assert bad.status_code == 400
    body = project()
    scene = body['scenes'][0]
    scene.update(visual='image', video_asset=image['id'], audio_asset=voice['id'], audio_text_hash=store.digest(scene['narration']))
    body['scenes'].append({**scene, 'id': 'frozen', 'visual': 'upload', 'video_asset': clip['id'], 'short_clip_mode': 'freeze'})
    row = store.save_project(body)
    assert store.preflight(row['project'])['ready']
    store.approve(row['id'], row['revision'])
    job = store.enqueue(row['id'], row['revision'], 'render', {'quality': 'draft'})
    assert task_render_pipeline.run(job['id'])['status'] == 'succeeded'
    output = DATA / 'exports' / job['id'] / 'reel.mp4'
    assert media.duration(output) == pytest.approx(2.6, abs=0.2)
    assert (output.parent / 'source-report.json').is_file()
    assert (output.parent / 'captions.srt').is_file()


def test_backend_edit_invalidates_old_spoken_text():
    body = project()
    body['scenes'][0]['spoken_text'] = 'Old normalized text'
    p = store.save_project(body)
    body['scenes'][0]['narration'] = 'New narration'
    updated, _ = services.save_project_mutation(body, p['id'], 1)
    assert updated.body['scenes'][0]['spoken_text'] == ''


def test_preflight_reports_missing_media_and_unreviewed_music():
    body = project()
    body['music_asset'] = 'f' * 32
    report = store.preflight(body)
    assert not report['ready']
    assert {e['field'] for e in report['errors']} >= {'music_asset', 'music_rights_reviewed'}


def test_cancelled_worker_cannot_publish_files(tmp_path):
    j = saved_job()
    with ExecutionLeaseContext(j['id'], 'worker'):
        store.cancel(j['id'])
        with pytest.raises(store.FencingError):
            store.publish_export(j['id'], tmp_path, {})
    assert not (DATA / 'exports' / j['id']).exists()


def test_rate_limit_prevents_provider_request(monkeypatch):
    save_settings({'online_enabled': True, 'gemini_key': 'fixture', 'gemini_model': 'fixture-model'})
    monkeypatch.setattr(providers, 'acquire_rate_slot', lambda *a: False)
    monkeypatch.setattr(providers, 'request', lambda *a, **k: pytest.fail('Rate-limited request must not reach provider'))
    with pytest.raises(ValueError, match='rate limit'):
        providers.gemini('fixture')


def test_validation_errors_return_json_not_server_errors():
    body = project()
    body['scenes'].append(copy.deepcopy(body['scenes'][0]))
    response = client().post('/api/projects', body, content_type='application/json')
    assert response.status_code == 422
    assert 'unique' in str(response.json())
    response = client().post('/api/settings', {'voice': None}, content_type='application/json')
    assert response.status_code == 400


def test_failed_render_cleans_work_directory(monkeypatch, tmp_path):
    j = saved_job('render')
    source = tmp_path / 'fixture.wav'
    source.write_bytes(b'fixture')
    monkeypatch.setattr(media, 'voice_for', lambda *a: (source, []))
    monkeypatch.setattr(media, 'duration', lambda *a: 1.0)
    def fail(*a, **k):
        raise ValueError('Intentional render failure')
    monkeypatch.setattr(media, 'write_ass', fail)
    with pytest.raises(ValueError, match='Intentional'):
        task_render_pipeline.run(j['id'])
    assert store.job(j['id'])['state'] == 'failed'
    assert not list((DATA / 'tmp').glob(j['id'] + '_*'))


@pytest.mark.parametrize('duration_mode', ['natural', 'target'])
def test_upload_playback_thumbnail_and_multiscene_music_render(tmp_path, duration_mode):
    audio = tmp_path / 'narration.wav'
    video = tmp_path / 'footage.mp4'
    seconds = 4.8 if duration_mode == 'target' else 1.3
    media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', f'sine=frequency=550:duration={seconds}', audio])
    media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=30:duration=1',
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', video])
    c = client()
    def upload(path):
        res = c.post('/api/assets', {'file': SimpleUploadedFile(path.name, path.read_bytes())})
        assert res.status_code == 200, res.content
        data = res.json()
        assert (DATA / 'assets' / data['filename']).is_file()
        response = c.get(f"/api/assets/{data['id']}/media", HTTP_RANGE='bytes=0-31')
        assert response.status_code == 206
        assert len(b''.join(response.streaming_content)) == 32
        response.close()
        return data
    a, v = upload(audio), upload(video)
    thumb = c.get(f"/api/assets/{v['id']}/thumbnail")
    assert thumb.status_code == 200
    thumb.close()
    body = project()
    body['scenes'][0].update(audio_asset=a['id'], audio_text_hash=store.digest(body['scenes'][0]['narration']))
    body['scenes'].append({**body['scenes'][0], 'id': 'scene2', 'visual': 'upload', 'video_asset': v['id'], 'fit': 'contain'})
    body.update(music_asset=a['id'], music_rights_reviewed=True, duration_mode=duration_mode, target_seconds=10)
    for scene in body['scenes']:
        scene.update(hold_seconds=0.5, cues=[{'start': 0.1, 'end': 1.0, 'text': 'Manual caption'}])
    p = store.save_project(body)
    store.approve(p['id'], 1)
    j = store.enqueue(p['id'], 1, 'render', {'quality': 'draft'})
    assert task_render_pipeline.run(j['id'])['status'] == 'succeeded'
    directory = DATA / 'exports' / j['id']
    manifest = json.loads((directory / 'timing-manifest.json').read_text('utf-8'))
    first, second = manifest['scenes']
    assert second['start_offset'] == first['end_offset']
    assert first['raw_voice_duration'] == pytest.approx(seconds)
    assert first['cues'][0]['start'] == pytest.approx(0.1 / manifest['speed'])
    assert manifest['all_cues'][1]['start'] == pytest.approx(second['start_offset'] + second['cues'][0]['start'])
    from studio.timeline import to_srt
    assert (directory / 'captions.srt').read_text('utf-8') == to_srt(manifest['all_cues'])
    assert media.duration(directory / 'reel.mp4') == pytest.approx(manifest['total_duration'], abs=0.1)
    assert not list((DATA / 'tmp').glob(j['id'] + '_*'))


def test_gemini_retries_count_each_attempt_once(monkeypatch):
    save_settings({'online_enabled': True, 'gemini_key': 'fixture', 'gemini_model': 'fixture', 'daily_ai_calls': 10})
    monkeypatch.setattr(providers, 'acquire_rate_slot', lambda *a: True)
    monkeypatch.setattr(providers.time, 'sleep', lambda *a: None)
    calls = []
    class Response:
        def json(self):
            return {'candidates': [{'content': {'parts': [{'text': 'ok'}]}, 'finishReason': 'STOP'}]}
    def request(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError('Provider returned HTTP 503')
        return Response()
    monkeypatch.setattr(providers, 'request', request)
    assert providers.gemini('fixture')[0] == 'ok'
    from studio.ratelimit import get_usage_summary
    assert get_usage_summary('gemini')[0]['calls'] == 2


def test_explicit_regeneration_replaces_cached_speech(tmp_path):
    from studio.voice.cache import store_cached_synthesis, get_cached_synthesis
    source = tmp_path / 'speech.mp3'
    key = uuid.uuid4().hex
    source.write_bytes(b'first fixture audio')
    store_cached_synthesis(key, source, [], {'duration': 1})
    source.write_bytes(b'second fixture audio')
    store_cached_synthesis(key, source, [{'start': 0, 'end': 2}], {'duration': 2}, overwrite=True)
    audio, timings, metadata = get_cached_synthesis(key)
    assert audio.read_bytes() == b'second fixture audio'
    assert timings[0]['end'] == 2
    assert metadata['duration'] == 2


def test_completed_delivery_does_not_repeat_provider(monkeypatch):
    j = saved_job()
    calls = []
    monkeypatch.setattr(providers, 'research', lambda p: calls.append(p) or {'text': 'fixture'})
    assert task_research.run(j['id'])['status'] == 'succeeded'
    assert task_research.run(j['id'])['status'] == 'fenced'
    assert len(calls) == 1


def test_cancelled_job_rejects_late_progress():
    j = saved_job()
    with ExecutionLeaseContext(j['id'], 'worker'):
        store.cancel(j['id'])
        with pytest.raises(store.FencingError):
            store.progress(j['id'], 'Complete', 'succeeded', {})
    assert store.job(j['id'])['state'] == 'cancelled'


def test_cancellation_stops_registered_subprocess():
    j = saved_job()
    def run_child():
        try:
            with ExecutionLeaseContext(j['id'], 'test-child'):
                media.run([sys.executable, '-c', 'import time; time.sleep(30)'], job_id=j['id'], timeout=10)
        finally:
            connections.close_all()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_child)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if Job.objects.get(id=j['id']).subprocess_pid:
                break
            time.sleep(0.05)
        else:
            pytest.fail('Child process did not register')
        store.cancel(j['id'])
        with pytest.raises(ValueError, match='Media processing failed'):
            future.result(timeout=5)
    assert store.job(j['id'])['state'] == 'cancelled'


def test_replaced_worker_cannot_commit_or_renew():
    j = saved_job()
    first = store.acquire_lease(j['id'], 'worker-a')
    Job.objects.using('job_writer').filter(id=j['id']).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    assert not store.renew_lease(j['id'], first)
    second = store.acquire_lease(j['id'], 'worker-b')
    assert second and second != first
    with pytest.raises(store.FencingError):
        store.progress(j['id'], 'late failure', 'failed', lease_token=first)
    assert store.job(j['id'])['state'] == 'running'


def test_provider_errors_are_redacted(monkeypatch):
    j = saved_job()
    save_settings({'sarvam_key': 'audit-secret'})
    def fail(p):
        raise ValueError('Provider said audit-secret')
    monkeypatch.setattr(providers, 'research', fail)
    with pytest.raises(ValueError):
        task_research.run(j['id'])
    result = store.job(j['id'])
    assert result['state'] == 'failed'
    assert 'audit-secret' not in result['progress']


def test_project_provider_is_snapshotted_and_bad_stock_id_is_422():
    data = project()
    data['tts_provider'] = 'sarvam'
    data['voice'] = 'priya'
    p = store.save_project(data)
    store.approve(p['id'], 1)
    c = client()
    response = c.post(f"/api/projects/{p['id']}/jobs", {'kind': 'voice', 'revision': 1}, content_type='application/json')
    assert response.status_code == 200, response.content
    assert response.json()['snapshot']['options']['tts_provider'] == 'sarvam'
    bad = c.post(f"/api/projects/{p['id']}/jobs", {'kind': 'stock', 'revision': 1, 'video_id': 'bad'}, content_type='application/json')
    assert bad.status_code == 422


def test_current_retry_requires_approval():
    j = saved_job('render')
    store.progress(j['id'], 'fixture failure', 'failed')
    p = store.get_project(j['project_id'])
    body = copy.deepcopy(p['project'])
    body['scenes'][0]['narration'] = 'Changed script'
    store.save_project(body, p['id'], p['revision'])
    response = client().post(f"/api/jobs/{j['id']}/retry", {'mode': 'current'}, content_type='application/json')
    assert response.status_code == 400
    with pytest.raises(ValueError, match='Approve'):
        store.retry(j['id'], 'current')


def test_voice_lookup_respects_provider():
    from studio.voice.registry import get_voice_capability
    assert get_voice_capability('te-IN-ShrutiNeural', 'azure').provider == 'azure'
    with pytest.raises(ValueError):
        get_voice_capability('does-not-exist', 'sarvam')


def test_voice_lab_obeys_offline_setting(monkeypatch):
    from studio.voice import lab
    monkeypatch.setattr(lab, 'get_cached_synthesis', lambda *a, **kw: None)
    with pytest.raises(ValueError, match='disabled'):
        lab.generate_voice_take('fixture', 'scene1', 'ఇది పరీక్ష.')


def test_edge_connection_rejection_is_actionable_and_cleans_partial_audio(monkeypatch, tmp_path):
    from aiohttp import WSServerHandshakeError
    from types import SimpleNamespace
    from studio.voice.edge_adapter import EdgeTTSAdapter
    adapter = EdgeTTSAdapter()

    async def rejected(text, voice, rate, pitch, temp_audio):
        temp_audio.write_bytes(b'partial')
        raise WSServerHandshakeError(SimpleNamespace(real_url='wss://example.test/?token=private'),
                                     (), status=403, message='Rejected')

    monkeypatch.setattr(adapter, '_stream_synthesis', rejected)
    # The transport exception can contain request URLs; do not expose them in the UI.
    monkeypatch.setattr('studio.voice.edge_adapter.logger.warning', lambda *a: None)
    with pytest.raises(ValueError, match='free Edge voice service rejected') as error:
        adapter.synthesize('ఇది పరీక్ష.', voice='te-IN-MohanNeural',
                           target_audio=tmp_path / 'speech.mp3', target_meta=tmp_path / 'speech.json')
    assert 'SETUP_WINDOWS.bat' in str(error.value)
    assert not list(tmp_path.iterdir())


def test_stale_voice_take_rejected_atomically():
    p = store.save_project(project())
    take = VoiceTake.objects.using('voice_writer').create(
        id='take_fixture', project_id=p['id'], scene_id='scene1', provider='edge',
        voice_id='te-IN-ShrutiNeural', spoken_text='Old script', meta={'source_text': 'Old script'})
    with pytest.raises(ValueError, match='Script changed'):
        services.accept_voice_take_mutation(p['id'], take.id)
    take.refresh_from_db(using='voice_writer')
    assert not take.accepted
    assert store.get_project(p['id'])['revision'] == 1


def test_accepted_take_preserves_word_timings(tmp_path):
    body = project()
    p = store.save_project(body)
    aid = uuid.uuid4().hex
    audio = DATA / 'assets' / f'{aid}.mp3'
    audio.write_bytes(b'fixture')
    store.add_asset({'id': aid, 'filename': audio.name, 'kind': 'audio', 'duration': 1})
    timing = [{'start': 0, 'end': 1, 'text': 'fixture'}]
    take = VoiceTake.objects.using('voice_writer').create(
        id='take_valid', project_id=p['id'], scene_id='scene1', provider='edge', asset_id=aid,
        voice_id='te-IN-ShrutiNeural', spoken_text=body['scenes'][0]['narration'], timing_data=timing,
        meta={'source_text': body['scenes'][0]['narration']})
    accepted = services.accept_voice_take_mutation(p['id'], take.id)['project']['project']
    assert media.voice_for(accepted['scenes'][0], accepted, 'edge')[1] == timing


def test_daily_quota_is_atomic_and_not_double_counted():
    from studio.ratelimit import record_usage, get_usage_summary
    def reserve(_):
        try:
            store.reserve_ai_call(3)
            record_usage('gemini', actual_units=1, count_call=False)
            return True
        except ValueError:
            return False
        finally:
            connections.close_all()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(reserve, range(8)))
    assert sum(outcomes) == 3
    assert get_usage_summary('gemini')[0]['calls'] == 3


def test_concurrent_enqueue_creates_one_job():
    p = store.save_project(project())
    def enqueue(_):
        try:
            return store.enqueue(p['id'], 1, 'research')['id']
        except ValueError:
            return None
        finally:
            connections.close_all()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(enqueue, range(4)))
    assert len([r for r in results if r]) == 1


@pytest.mark.parametrize('quality,dimensions', [('draft', (540, 960)), ('final', (1080, 1920))])
def test_real_offline_render_export_and_cache(quality, dimensions):
    aid = uuid.uuid4().hex
    audio = DATA / 'assets' / f'{aid}.wav'
    media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.2', audio])
    store.add_asset({'id': aid, 'filename': audio.name, 'kind': 'audio', 'name': 'Diagnostic tone', 'duration': 1.2})
    body = project()
    body['scenes'][0].update(audio_asset=aid, audio_text_hash=store.digest(body['scenes'][0]['narration']))
    p = store.save_project(body)
    store.approve(p['id'], 1)
    j = store.enqueue(p['id'], 1, 'render', {'quality': quality})
    worker.process(store.claim())
    finished = store.job(j['id'])
    assert finished['state'] == 'succeeded', finished['progress']
    output = DATA / 'exports' / j['id'] / 'reel.mp4'
    video = next(s for s in media.probe(output)['streams'] if s['codec_type'] == 'video')
    assert (video['width'], video['height']) == dimensions
    assert video['codec_name'] == 'h264'
    manifest_response = client().get(f"/api/exports/{j['id']}/timing-manifest.json")
    assert manifest_response.status_code == 200
    manifest = json.loads(b''.join(manifest_response.streaming_content))
    manifest_response.close()
    assert manifest['schema_version'] == 1
    assert manifest['job_id'] == j['id']
    assert manifest['project_id'] == p['id']
    assert manifest['dimensions'] == list(dimensions)
    assert manifest['scenes'][0]['raw_voice_duration'] == pytest.approx(media.duration(audio))
    assert manifest['total_duration'] == manifest['total_frames'] / 30
    assert abs(manifest['measured_export_duration'] - manifest['total_duration']) < 0.5
    from studio.timeline import to_srt
    assert (output.parent / 'captions.srt').read_text('utf-8') == to_srt(manifest['all_cues'])
    response = client().get(f"/api/exports/{j['id']}/reel.mp4")
    assert response.status_code == 200
    response.close()
    cached = {f.name: f.stat().st_mtime_ns for f in (DATA / 'cache').glob('*.scene.mp4')}
    j2 = store.enqueue(p['id'], 1, 'render', {'quality': quality})
    assert task_render_pipeline.run(j2['id'])['status'] == 'succeeded'
    assert cached == {f.name: f.stat().st_mtime_ns for f in (DATA / 'cache').glob('*.scene.mp4')}


@pytest.mark.parametrize('mode,target,speed', [('natural', 60, 1), ('target', 10, 1.25), ('target', 14.5, 0.8)])
def test_shared_timeline_offsets_speed_holds_and_input_preservation(mode, target, speed):
    from studio.timeline import compute_timeline
    p = {'duration_mode': mode, 'target_seconds': target,
         'scenes': [{'id': 'a', 'hold_seconds': 1}, {'id': 'b', 'hold_seconds': 1}]}
    voices = [{'scene_id': sid, 'duration': duration, 'alignment': 'manual',
               'cues': [{'start': 0.2, 'end': duration - 0.1, 'text': sid}]}
              for sid, duration in [('b', 6), ('a', 4)]]
    original = copy.deepcopy(voices)
    tl = compute_timeline(p, voices)
    assert tl.speed == pytest.approx(speed)
    assert tl.scenes[0].raw_voice_duration == 4  # match by ID, not input position
    assert tl.scenes[1].start_frame == tl.scenes[0].end_frame
    assert tl.scenes[1].start_offset == tl.scenes[0].frame_count / 30
    assert tl.scenes[0].cues[0]['start'] == pytest.approx(0.2 / speed)
    assert tl.all_cues[1]['start'] == pytest.approx(tl.scenes[1].start_offset + 0.2 / speed)
    assert tl.total_duration == sum(s.frame_count for s in tl.scenes) / 30
    assert voices == original


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'nan', 'zero', 'overlap', 'past_end', 'excessive_speed', 'holds'])
def test_shared_timeline_rejects_invalid_inputs(fault):
    from studio.timeline import compute_timeline
    p = {'duration_mode': 'natural', 'target_seconds': 10, 'scenes': [{'id': 'a', 'hold_seconds': 0}]}
    v = [{'scene_id': 'a', 'duration': 2, 'cues': [{'start': 0, 'end': 1, 'text': 'test'}]}]
    if fault == 'missing': v = []
    elif fault == 'duplicate': v *= 2
    elif fault == 'nan': v[0]['duration'] = float('nan')
    elif fault == 'zero': v[0]['duration'] = 0
    elif fault == 'overlap': v[0]['cues'].append({'start': 0.5, 'end': 1.5, 'text': 'overlap'})
    elif fault == 'past_end': v[0]['cues'][0]['end'] = 3
    elif fault == 'excessive_speed': p['duration_mode'] = 'target'
    elif fault == 'holds': p.update(duration_mode='target'); p['scenes'][0]['hold_seconds'] = 10
    with pytest.raises(ValueError): compute_timeline(p, v)


# Stage 2: caption editor ---------------------------------------------------------------

TELUGU_LINE = 'ఇది ఒక చిన్న పరీక్ష'


def edge_fixture(monkeypatch, seconds=2.0):
    """Offline stand-in for Edge: real MP3 in the synthesis cache plus word boundaries."""
    calls = []
    def synthesize(text, **kw):
        calls.append(text)
        path = DATA / 'cache' / f'fixture_{store.digest([text, kw, seconds])[:12]}.mp3'
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i',
                       f'sine=frequency=500:duration={seconds}', '-c:a', 'libmp3lame', path])
        step = seconds / 4
        return path, [{'start': i * step, 'end': (i + 1) * step - 0.05, 'text': f'w{i}'} for i in range(4)]
    monkeypatch.setattr(providers, 'synthesize', synthesize)
    return calls


def caption_project():
    body = project()
    body['scenes'][0].update(narration=TELUGU_LINE, roman='Idi oka chinna pariksha')
    body['scenes'].append({**body['scenes'][0], 'id': 'scene2', 'graphic_title': 'Second'})
    return body


def caption_timing(pid, body=None):
    res = client().post(f'/api/projects/{pid}/caption-timing', {'project': body} if body else {},
                        content_type='application/json')
    assert res.status_code == 200, res.content
    return {t['scene_id']: t for t in res.json()['scenes']}


def test_caption_text_accepts_telugu_and_roman_but_rejects_blank_or_overlap():
    scene = {'id': 's', 'cues': [{'start': 0, 'end': 1, 'text': TELUGU_LINE},
                                 {'start': 1, 'end': 2, 'text': 'Roman Telugu'}]}
    assert Scene.model_validate(scene).cues[0].text == TELUGU_LINE
    with pytest.raises(ValueError, match='empty'):
        Scene.model_validate({'id': 's', 'cues': [{'start': 0, 'end': 1, 'text': '   '}]})
    scene['cues'][1]['start'] = 0.9
    with pytest.raises(ValueError, match='overlap'):
        Scene.model_validate(scene)
    body = project()
    body['scenes'][0]['cues'] = scene['cues']
    assert client().post('/api/projects', body, content_type='application/json').status_code == 422


def test_caption_timing_uses_measured_prepared_narration_and_detects_stale_speech(monkeypatch):
    calls = edge_fixture(monkeypatch, seconds=2.0)
    row = store.save_project(caption_project())
    store.approve(row['id'], row['revision'])
    assert caption_timing(row['id'])['scene1']['status'] == 'unmeasured'
    job = store.enqueue(row['id'], row['revision'], 'voice')
    from studio.tasks import task_voice_pipeline
    assert task_voice_pipeline.run(job['id'])['status'] == 'succeeded'
    voice = store.job(job['id'])['result']['voices'][0]
    assert len(voice['boundaries']) == 4

    first = caption_timing(row['id'])['scene1']
    assert first['status'] == 'measured'
    assert first['duration'] == pytest.approx(media.duration(Path(voice['audio_path'])))
    assert first['duration'] == pytest.approx(2.0, abs=0.1)
    assert first['audio_url'] == f"/api/jobs/{job['id']}/audio/scene1"
    roman = first['automatic']['roman']
    assert roman['alignment'].startswith('word-index mapping')
    assert roman['cues'] == [{'start': 0, 'end': pytest.approx(1.95), 'text': 'Idi oka chinna pariksha'}]
    assert first['automatic']['telugu']['cues'][0]['text'] == TELUGU_LINE

    # Range requests let the editor seek within prepared narration.
    audio = client().get(first['audio_url'], HTTP_RANGE='bytes=0-15')
    assert audio.status_code == 206
    assert len(b''.join(audio.streaming_content)) == 16
    audio.close()

    # Unsaved text/voice edits are measured against what the renderer would actually use.
    edited = copy.deepcopy(row['project'])
    edited['scenes'][1]['narration'] = 'వేరే కథ'
    edited['scenes'][0]['cues'] = [{'start': 'unfinished draft', 'end': 1, 'text': ''}]
    timing = caption_timing(row['id'], edited)
    assert timing['scene1']['status'] == 'measured'
    assert timing['scene2']['status'] == 'unmeasured'
    edited['speech_rate'] = 10
    assert {t['status'] for t in caption_timing(row['id'], edited).values()} == {'unmeasured'}
    assert len(calls) == 2  # measuring never synthesizes
    bad = client().post(f"/api/projects/{row['id']}/caption-timing", {'project': {'scenes': [{'id': '../x'}]}},
                        content_type='application/json')
    assert bad.status_code == 422


def test_caption_timing_measures_attached_recording_and_flags_mismatch(tmp_path):
    audio = tmp_path / 'voice.wav'
    media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=duration=1.7', audio])
    asset = client().post('/api/assets', {'file': SimpleUploadedFile(audio.name, audio.read_bytes())}).json()
    body = caption_project()
    body['scenes'][0].update(audio_asset=asset['id'], audio_text_hash=store.digest(TELUGU_LINE))
    row = store.save_project(body)
    timing = caption_timing(row['id'])['scene1']
    assert timing['status'] == 'measured'
    assert timing['duration'] == pytest.approx(1.7, abs=0.05)
    assert timing['audio_url'] == f"/api/assets/{asset['id']}/media"
    assert timing['automatic']['roman']['alignment'].startswith('estimated')
    edited = copy.deepcopy(row['project'])
    edited['scenes'][0]['audio_text_hash'] = 'stale'
    assert caption_timing(row['id'], edited)['scene1']['status'] == 'stale'


def test_telugu_caption_style_and_roman_style_are_both_written(tmp_path):
    path = tmp_path / 'captions.ass'
    media.write_ass(path, [{'start': 0, 'end': 1, 'text': TELUGU_LINE},
                           {'start': 1, 'end': 2, 'text': 'Roman Telugu'}], project())
    text = path.read_text('utf-8')
    assert f'Style: Telugu,{media.TELUGU_FONT},56,' in text
    assert f',Telugu,,0,0,0,,{TELUGU_LINE}' in text
    assert ',Default,,0,0,0,,Roman Telugu' in text


@pytest.mark.parametrize('quality,dimensions', [('draft', (540, 960)), ('final', (1080, 1920))])
def test_caption_editor_workflow_exports_edited_captions(monkeypatch, quality, dimensions):
    """Prepare narration, save edited captions over HTTP as the editor does, approve, render, verify."""
    edge_fixture(monkeypatch, seconds=2.4)
    c = client()
    pid = c.post('/api/projects', caption_project(), content_type='application/json').json()['id']
    assert c.post(f'/api/projects/{pid}/approve', {'revision': 1}, content_type='application/json').status_code == 200
    queued = c.post(f'/api/projects/{pid}/jobs', {'revision': 1, 'kind': 'voice'}, content_type='application/json')
    assert queued.status_code == 200, queued.content
    from studio.tasks import task_voice_pipeline
    assert task_voice_pipeline.run(queued.json()['id'])['status'] == 'succeeded'
    timing = caption_timing(pid)
    measured = timing['scene1']['duration']

    body = c.get(f'/api/projects/{pid}').json()['project']
    # Scene 1: Telugu-script captions, split in two; scene 2 keeps automatic Roman timing.
    body['scenes'][0]['cues'] = [{'start': 0.1, 'end': 1.2, 'text': 'ఇది ఒక'},
                                 {'start': 1.2, 'end': round(measured, 3), 'text': 'చిన్న పరీక్ష'}]
    saved = c.put(f'/api/projects/{pid}', {'revision': 1, 'project': body}, content_type='application/json')
    assert saved.status_code == 200, saved.content
    revision = saved.json()['revision']
    assert saved.json()['project']['scenes'][0]['cues'][0]['text'] == 'ఇది ఒక'
    # Caption edits do not change narration, so the measurement still applies.
    assert caption_timing(pid)['scene1']['status'] == 'measured'
    overlap = copy.deepcopy(body)
    overlap['scenes'][0]['cues'][1]['start'] = 1.0
    assert c.put(f'/api/projects/{pid}', {'revision': revision, 'project': overlap},
                 content_type='application/json').status_code == 422

    assert c.post(f'/api/projects/{pid}/approve', {'revision': revision}, content_type='application/json').status_code == 200
    render = c.post(f'/api/projects/{pid}/jobs', {'revision': revision, 'kind': 'render', 'quality': quality},
                    content_type='application/json')
    assert render.status_code == 200, render.content
    jid = render.json()['id']
    assert task_render_pipeline.run(jid)['status'] == 'succeeded'
    directory = DATA / 'exports' / jid
    video = next(s for s in media.probe(directory / 'reel.mp4')['streams'] if s['codec_type'] == 'video')
    assert (video['width'], video['height']) == dimensions
    manifest = json.loads((directory / 'timing-manifest.json').read_text('utf-8'))
    first, second = manifest['scenes']
    assert first['alignment'] == 'manual'
    assert first['raw_voice_duration'] == pytest.approx(measured)
    assert [cue['text'] for cue in first['cues']] == ['ఇది ఒక', 'చిన్న పరీక్ష']
    assert first['cues'][1]['end'] == pytest.approx(round(measured, 3))
    assert second['alignment'].startswith('word-index mapping')
    assert [(cue['start'], cue['end'], cue['text']) for cue in second['cues']] == [
        (pytest.approx(cue['start']), pytest.approx(cue['end']), cue['text'])
        for cue in timing['scene2']['automatic']['roman']['cues']]
    from studio.timeline import to_srt
    srt = (directory / 'captions.srt').read_text('utf-8')
    assert srt == to_srt(manifest['all_cues'])
    assert 'చిన్న పరీక్ష' in srt and 'Idi oka chinna pariksha' in srt
    assert media.duration(directory / 'reel.mp4') == pytest.approx(manifest['total_duration'], abs=0.1)
    media.run([executable('ffmpeg'), '-v', 'error', '-xerror', '-i', directory / 'reel.mp4', '-f', 'null', '-'])


def test_caption_editor_dom_behaviour():
    page = DATA.parent / 'captions.html'
    page.write_bytes(client().get('/').content)
    subprocess.run(['node', 'tests/audit_captions_ui.cjs', str(page)], check=True, timeout=45)


# Stage 3: framing previews -------------------------------------------------------------

from studio import framing


def ffmpeg(*args):
    media.run([executable('ffmpeg'), '-v', 'error', '-y', *args])


def upload_file(path):
    res = client().post('/api/assets', {'file': SimpleUploadedFile(path.name, path.read_bytes())})
    assert res.status_code == 200, res.content
    return res.json()


def framing_project(scenes, tmp_path, tone_seconds=2.0):
    """Project whose scenes share one attached narration tone; returns (pid, saved body)."""
    tone = tmp_path / f'tone{tone_seconds}.wav'
    ffmpeg('-f', 'lavfi', '-i', f'sine=frequency=420:duration={tone_seconds}', tone)
    voice = upload_file(tone)
    body = project()
    base = {**body['scenes'][0], 'audio_asset': voice['id'], 'audio_text_hash': store.digest(body['scenes'][0]['narration'])}
    body['scenes'] = [{**base, **scene} for scene in scenes]
    res = client().post('/api/projects', body, content_type='application/json')
    assert res.status_code == 200, res.content
    return res.json()['id'], res.json()['project']


def render_over_http(pid, quality):
    c = client()
    revision = c.get(f'/api/projects/{pid}').json()['revision']
    assert c.post(f'/api/projects/{pid}/approve', {'revision': revision}, content_type='application/json').status_code == 200
    queued = c.post(f'/api/projects/{pid}/jobs', {'revision': revision, 'kind': 'render', 'quality': quality},
                    content_type='application/json')
    assert queued.status_code == 200, queued.content
    jid = queued.json()['id']
    assert task_render_pipeline.run(jid)['status'] == 'succeeded', store.job(jid)['progress']
    directory = DATA / 'exports' / jid
    return directory, json.loads((directory / 'timing-manifest.json').read_text('utf-8'))


def psnr(a, b, b_at=None):
    """PSNR in dB between the first frame of a and the frame of b at b_at seconds.
    Compared at quarter size, so draft H.264 noise on test patterns does not mask framing shifts."""
    import re
    args = [executable('ffmpeg'), '-hide_banner', '-i', str(a)]
    args += (['-ss', f'{b_at:.4f}'] if b_at is not None else []) + ['-i', str(b)]
    scale = 'scale=iw/4:ih/4:flags=area,format=rgb24,setpts=0'
    args += ['-filter_complex', f'[0:v]{scale}[a];[1:v]{scale}[b];[a][b]psnr',
             '-frames:v', '1', '-f', 'null', '-']
    out = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace').stderr
    value = re.search(r'average:(\S+)', out).group(1)
    return float('inf') if value == 'inf' else float(value)


def centre_colour(path, at):
    raw = subprocess.run([executable('ffmpeg'), '-v', 'error', '-ss', f'{at:.3f}', '-i', str(path), '-frames:v', '1',
                          '-vf', 'crop=iw/2:ih/4:iw/4:ih/4,scale=1:1:flags=area', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                         capture_output=True, check=True).stdout
    return 'rgb'[max(range(3), key=lambda i: raw[i])]


def test_framing_geometry_matches_browser_and_editor_dom(tmp_path):
    cases = []
    for src in ((1920, 1080), (1080, 1920), (1200, 1600), (181, 333), (4000, 3000), (640, 640)):
        for size in ((1080, 1920), (540, 960)):
            for fit in ('crop', 'contain'):
                for aspect in framing.ASPECTS:
                    for zoom, x, y in ((1, .5, .5), (1.37, 0, 1), (2.5, .123, .877), (3, 1, 0)):
                        args = [src[0], src[1], size[0], size[1], fit, aspect, zoom, x, y]
                        g = framing.geometry(*args)
                        assert g['crop_x'] + g['crop_w'] <= g['scale_w'] and g['crop_y'] + g['crop_h'] <= g['scale_h']
                        assert g['pad_x'] + g['crop_w'] <= size[0] and g['pad_y'] + g['crop_h'] <= size[1]
                        assert all(g[k] % 2 == 0 for k in ('scale_w', 'scale_h', 'crop_w', 'crop_h',
                                                           'crop_x', 'crop_y', 'pad_x', 'pad_y'))
                        cases.append({'args': args, 'expected': g})
    data = tmp_path / 'geometry.json'
    data.write_text(json.dumps(cases), 'utf-8')
    page = DATA.parent / 'framing.html'
    page.write_bytes(client().get('/').content)
    subprocess.run(['node', 'tests/audit_framing_ui.cjs', str(page), str(data)], check=True, timeout=60)


def test_framing_reads_rotation_exif_and_pixel_aspect(tmp_path):
    import struct
    base, rotated, wide, photo = (tmp_path / n for n in ('base.mp4', 'rotated.mp4', 'wide.mp4', 'photo.jpg'))
    ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=320x180:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', base)
    ffmpeg('-display_rotation', '90', '-i', base, '-c', 'copy', rotated)
    ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=320x240:d=1', '-vf', 'setsar=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', wide)
    plain = tmp_path / 'plain.jpg'
    ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=400x200', '-frames:v', '1', plain)
    # EXIF orientation 6 (rotate 90 degrees), as phone cameras write it.
    tiff = b'II*\x00' + struct.pack('<I', 8) + struct.pack('<H', 1) + struct.pack('<HHII', 0x0112, 3, 1, 6) + struct.pack('<I', 0)
    app1 = b'Exif\x00\x00' + tiff
    raw = plain.read_bytes()
    photo.write_bytes(raw[:2] + b'\xff\xe1' + struct.pack('>H', len(app1) + 2) + app1 + raw[2:])

    info = {name: framing.source_info(path) for name, path in (('rotated', rotated), ('wide', wide), ('photo', photo))}
    assert (info['rotated']['width'], info['rotated']['height']) == (180, 320)
    assert (info['wide']['width'], info['wide']['height'], info['wide']['normalize']) == (640, 240, True)
    assert (info['photo']['width'], info['photo']['height']) == (200, 400)
    # The renderer's filters see exactly those sizes: 'contain' fills the 1080-wide frame without distortion.
    for name, path in (('rotated', rotated), ('wide', wide), ('photo', photo)):
        g = framing.geometry(info[name]['width'], info[name]['height'], 1080, 1920, 'contain')
        out = tmp_path / f'{name}.png'
        ffmpeg('-i', path, '-frames:v', '1', '-vf', framing.filter_chain(g, info[name]), out)
        stream = media.probe(out, allow_still=True)['streams'][0]
        assert (stream['width'], stream['height']) == (1080, 1920)
    for name, path in (('rotated', rotated), ('photo', photo)):
        res = client().get(f"/api/assets/{upload_file(path)['id']}/framing")
        assert res.status_code == 200, res.content
        assert (res.json()['width'], res.json()['height']) == (info[name]['width'], info[name]['height'])
    tone = tmp_path / 'tone.wav'
    ffmpeg('-f', 'lavfi', '-i', 'sine=duration=0.5', tone)
    assert client().get(f"/api/assets/{upload_file(tone)['id']}/framing").status_code == 400


@pytest.mark.parametrize('quality,dimensions', [('draft', (540, 960)), ('final', (1080, 1920))])
def test_exact_frame_preview_matches_exported_mp4(tmp_path, quality, dimensions):
    footage, picture = tmp_path / 'footage.mp4', tmp_path / 'picture.jpg'
    ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=1920x1080:r=30:d=4', '-c:v', 'libx264', '-crf', '8', '-pix_fmt', 'yuv420p', footage)
    ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=1600x1200', '-frames:v', '1', '-q:v', '2', picture)
    video, image = upload_file(footage), upload_file(picture)
    cue = lambda text: [{'start': 0, 'end': 1.9, 'text': text}]
    pid, body = framing_project([
        {'id': 'clip', 'visual': 'upload', 'video_asset': video['id'], 'trim_start': 1.0, 'zoom': 1.6,
         'crop_x': 0.2, 'crop_y': 0.7, 'cues': cue('నమస్కారం, మన కథ')},
        {'id': 'photo', 'visual': 'image', 'video_asset': image['id'], 'fit': 'contain', 'frame_aspect': '4:5',
         'zoom': 1.3, 'crop_x': 0.8, 'cues': cue('Mana katha')},
        {'id': 'wide', 'visual': 'upload', 'video_asset': video['id'], 'frame_aspect': '16:9', 'zoom': 2.6,
         'crop_x': 0.9, 'crop_y': 0.1, 'trim_start': 0.5, 'trim_end': 3.5, 'cues': cue('Idi oka pariksha')},
    ], tmp_path)
    directory, manifest = render_over_http(pid, quality)
    reel = directory / 'reel.mp4'
    offsets = {s['scene_id']: s['start_offset'] for s in manifest['scenes']}
    # A full-range BT.601 JPEG scene must not switch the joined reel's colour range or matrix.
    tags = subprocess.run([executable('ffprobe'), '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                           'frame=pix_fmt,color_range,color_space,color_primaries,color_transfer', '-of', 'csv=p=0',
                           str(reel)], capture_output=True, text=True, check=True).stdout.split()
    assert {tag.rstrip(',') for tag in tags} == {'yuv420p,tv,bt709,bt709,bt709'}, set(tags)

    def preview(scene, at, **changes):
        res = client().post(f'/api/projects/{pid}/framing-preview',
                            {'scene': {**scene, **changes}, 'source_time': at, 'caption': scene['cues'][0]['text'],
                             'quality': quality}, content_type='application/json')
        assert res.status_code == 200, res.content
        assert res['Content-Type'] == 'image/jpeg'
        path = tmp_path / f"preview-{scene['id']}-{len(changes)}.jpg"
        path.write_bytes(res.content)
        return path, json.loads(res['X-Framing-Geometry'])

    # Sample half a frame past a frame boundary, so both sides pick the same source frame.
    t = 0.5 + 1 / 60
    scores = {}
    for scene in body['scenes']:
        source_time = scene['trim_start'] + t if scene['visual'] != 'image' else None
        frame, geometry = preview(scene, source_time)
        path = store.asset_path(scene['video_asset'], 'image' if scene['visual'] == 'image' else 'video')
        assert geometry == framing.scene_geometry(scene, framing.source_info(path), *dimensions)
        assert media.probe(frame, allow_still=True)['streams'][0]['width'] == dimensions[0]
        scores[scene['id']] = psnr(frame, reel, b_at=offsets[scene['id']] + t)
        assert scores[scene['id']] > 28, scores
    # Negative control: a different crop is clearly detected, so the threshold is meaningful.
    shifted, _ = preview(body['scenes'][0], body['scenes'][0]['trim_start'] + t, crop_x=0.8)
    scores['clip_with_wrong_crop'] = psnr(shifted, reel, b_at=offsets['clip'] + t)
    assert scores['clip_with_wrong_crop'] < scores['clip'] - 8, scores
    (DATA.parent / f'framing-psnr-{quality}.json').write_text(json.dumps(scores), 'utf-8')

    # Invalid input is reported, not rendered.
    bad = client().post(f'/api/projects/{pid}/framing-preview', {'scene': {**body['scenes'][0], 'trim_start': 9}},
                        content_type='application/json')
    assert bad.status_code == 422 and 'Trim start' in bad.json()['detail']
    graphic = client().post(f'/api/projects/{pid}/framing-preview', {'scene': {**body['scenes'][0], 'visual': 'graphic'}},
                            content_type='application/json')
    assert graphic.status_code == 422
    assert not list((DATA / 'tmp').glob('framing_*'))


def test_trimmed_clips_loop_and_hold_inside_the_trim(tmp_path):
    """Red 0-1 s, green 1-2 s, blue 2-3 s; every scene must show only its trimmed colours."""
    bands = tmp_path / 'bands.mp4'
    ffmpeg('-f', 'lavfi', '-i', 'color=red:s=320x240:r=30:d=1', '-f', 'lavfi', '-i', 'color=green:s=320x240:r=30:d=1',
           '-f', 'lavfi', '-i', 'color=blue:s=320x240:r=30:d=1', '-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0',
           '-c:v', 'libx264', '-pix_fmt', 'yuv420p', bands)
    clip = upload_file(bands)
    pid, _ = framing_project([
        {'id': 'loopgreen', 'visual': 'upload', 'video_asset': clip['id'], 'trim_start': 1, 'trim_end': 2},
        {'id': 'holdgreen', 'visual': 'upload', 'video_asset': clip['id'], 'trim_start': 1, 'trim_end': 1.5,
         'short_clip_mode': 'freeze'},
        {'id': 'loopblue', 'visual': 'upload', 'video_asset': clip['id'], 'trim_start': 2},
        {'id': 'whole', 'visual': 'upload', 'video_asset': clip['id']},
    ], tmp_path, tone_seconds=2.6)
    directory, manifest = render_over_http(pid, 'draft')
    reel = directory / 'reel.mp4'
    offsets = {s['scene_id']: s['start_offset'] for s in manifest['scenes']}
    seen = {sid: [centre_colour(reel, offsets[sid] + at) for at in (0.3, 1.3, 2.3)] for sid in offsets}
    # Before Stage 3, -stream_loop restarted at 0 s, so trimmed loops showed red after one pass.
    assert seen == {'loopgreen': ['g', 'g', 'g'], 'holdgreen': ['g', 'g', 'g'],
                    'loopblue': ['b', 'b', 'b'], 'whole': ['r', 'g', 'b']}, seen


def frame_number(path, frame=0):
    """Read the frame index a 'ramp' clip encodes in luma (Y = 16 + 3n), from frame `frame` of path."""
    raw = subprocess.run([executable('ffmpeg'), '-v', 'error', '-i', str(path), '-frames:v', '1', '-vf',
                          f'select=eq(n\\,{frame}),crop=iw/2:ih/4:iw/4:ih/4,scale=2:2:flags=area:out_range=tv',
                          '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-'], capture_output=True, check=True).stdout
    return round((sum(raw[:4]) / 4 - 16) / 3)


def test_trim_preview_and_export_show_the_same_source_frame(tmp_path):
    ramp = tmp_path / 'ramp.mp4'
    # Luma 16 + 3n identifies frame n of a 30 fps clip.
    ffmpeg('-f', 'lavfi', '-i', "color=black:s=640x360:r=30:d=2.2,format=yuv420p,geq=lum='16+3*N':cb=128:cr=128",
           '-c:v', 'libx264', '-crf', '0', '-pix_fmt', 'yuv420p', ramp)
    clip = upload_file(ramp)
    pid, body = framing_project([
        {'id': 'trimmed', 'visual': 'upload', 'video_asset': clip['id'], 'trim_start': 1.0, 'trim_end': 2.0, 'zoom': 1.4},
    ], tmp_path, tone_seconds=1.5)
    directory, _ = render_over_http(pid, 'draft')
    reel = directory / 'reel.mp4'
    # A browser playhead part-way through scene frame k shows source frame 30 + k.
    for k in (0, 15, 27):
        res = client().post(f'/api/projects/{pid}/framing-preview',
                            {'scene': body['scenes'][0], 'source_time': 1.0 + (k + 0.4) / 30, 'quality': 'draft'},
                            content_type='application/json')
        assert res.status_code == 200, res.content
        still = tmp_path / f'ramp-{k}.jpg'
        still.write_bytes(res.content)
        assert (frame_number(still), frame_number(reel, k)) == (30 + k, 30 + k), k
    # The loop restarts at the trim start: scene frame 36 is trim frame 6 again.
    assert frame_number(reel, 36) == 36


def test_framing_fields_validate_and_old_scenes_keep_defaults(tmp_path):
    old = {'id': 'legacy', 'fit': 'contain', 'crop_x': 0.3}
    scene = Scene.model_validate(old)
    assert (scene.zoom, scene.crop_y, scene.frame_aspect, scene.trim_end) == (1, 0.5, 'full', 0)
    for bad in ({'zoom': 0.5}, {'zoom': 3.5}, {'crop_y': 1.2}, {'frame_aspect': '21:9'},
                {'trim_start': 2, 'trim_end': 1.5}):
        with pytest.raises(ValueError):
            Scene.model_validate({**old, **bad})
    footage = tmp_path / 'short.mp4'
    ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=320x240:r=30:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', footage)
    clip = upload_file(footage)
    body = project()
    body['scenes'][0].update(visual='upload', video_asset=clip['id'], trim_start=0.5, trim_end=9)
    report = store.preflight(body)
    assert report['ready'], report
    assert any('trim end is past' in w['message'] for w in report['warnings'])
    body['scenes'][0].update(trim_start=1.99, trim_end=0)
    assert any('Trim start must be before the end' in e['message'] for e in store.preflight(body)['errors'])
    res = client().post('/api/projects', {**body, 'scenes': [{**body['scenes'][0], 'trim_start': 1, 'trim_end': 0.5}]},
                        content_type='application/json')
    assert res.status_code == 422


# Stage 4: caption style presets ---------------------------------------------------------

STYLES = ('classic', 'black_box', 'yellow_accent', 'translucent')
# The ASS styles every render used before presets existed; 'classic' must reproduce them.
ORIGINAL_STYLE_LINES = [
    'Style: Default,Arial,56,&H00FFFFFF,&H00FFFFFF,&H00120E0B,&H88000000,-1,0,0,0,100,100,0,0,1,3,1,2,90,140,300,1',
    f'Style: Telugu,{media.TELUGU_FONT},56,&H00FFFFFF,&H00FFFFFF,&H00120E0B,&H88000000,-1,0,0,0,100,100,0,0,1,3,1,2,90,140,300,1',
]


def style_lines(tmp_path, **settings):
    path = tmp_path / 'styles.ass'
    media.write_ass(path, [{'start': 0, 'end': 1, 'text': TELUGU_LINE}], {'caption_size': 56, 'caption_bottom': 300, **settings})
    return [line for line in path.read_text('utf-8').splitlines() if line.startswith(('Style: Default', 'Style: Telugu'))]


def test_existing_projects_keep_classic_captions_unchanged(tmp_path):
    legacy = project()
    legacy.pop('caption_style', None)
    assert Project.model_validate(legacy).caption_style == 'classic'
    assert style_lines(tmp_path) == ORIGINAL_STYLE_LINES                    # body without the field
    assert style_lines(tmp_path, caption_style='classic') == ORIGINAL_STYLE_LINES
    saved = store.save_project(legacy)
    assert saved['project']['caption_style'] == 'classic'
    c = client()
    assert c.post('/api/projects', {**project(), 'caption_style': 'neon'}, content_type='application/json').status_code == 422
    pid = c.post('/api/projects', {**project(), 'caption_style': 'black_box'}, content_type='application/json').json()['id']
    body = c.get(f'/api/projects/{pid}').json()['project']
    assert body['caption_style'] == 'black_box'
    # Editing something else never resets the chosen style.
    body['title'] = 'Renamed'
    res = c.put(f'/api/projects/{pid}', {'revision': 1, 'project': body}, content_type='application/json')
    assert res.status_code == 200 and res.json()['project']['caption_style'] == 'black_box'


def test_caption_style_presets_keep_size_position_and_fonts(tmp_path):
    seen = set()
    for style in STYLES:
        default, telugu = style_lines(tmp_path, caption_style=style, caption_size=64, caption_bottom=420)
        d, t = default.split(','), telugu.split(',')
        assert d[1] == 'Arial' and t[1] == media.TELUGU_FONT
        assert d[2:] == t[2:], style                                       # same look for both scripts
        assert (d[2], d[-2], d[-5]) == ('64', '420', '2')                   # size, bottom margin, bottom-centre
        preset = media.CAPTION_STYLES[style]
        assert (d[3], d[5], d[6], d[15]) == (preset['primary'], preset['outline_colour'], preset['back'], str(preset['border']))
        if preset['outline'] == 'pad':
            assert d[16] == '16'                                            # box padding: a quarter of the size
        seen.add(tuple(d[3:18]))
    assert len(seen) == 4


@pytest.mark.parametrize('style', STYLES)
def test_telugu_captions_use_complex_shaping_in_every_style(tmp_path, style):
    """Joined letters (conjuncts, vowel signs) need HarfBuzz shaping; the export's default must apply it."""
    ass = tmp_path / 'captions.ass'
    media.write_ass(ass, [{'start': 0, 'end': 5, 'text': 'క్షమించండి శ్రీ స్త్రీ ద్వారం'}],
                    {'caption_size': 64, 'caption_bottom': 300, 'caption_style': style})
    frames = {}
    for shaping in ('', ':shaping=complex', ':shaping=simple'):
        out = tmp_path / f'shape{shaping.replace(":", "_").replace("=", "")}.png'
        media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0x406080:s=1080x1920',
                   '-vf', f'subtitles=captions.ass{shaping}', '-frames:v', '1', out], cwd=tmp_path)
        frames[shaping] = out
    assert psnr(frames[''], frames[':shaping=complex']) == float('inf')     # exports shape Telugu
    assert psnr(frames[''], frames[':shaping=simple']) < 40                  # and shaping changes the glyphs


def caption_band(path, out):
    """The caption area (y 1250-1800 of 1080x1920), where every style draws."""
    ffmpeg('-i', path, '-vf', 'scale=1080:1920:flags=area,crop=1080:550:0:1250', '-frames:v', '1', out)
    return out


@pytest.mark.parametrize('quality,dimensions', [('draft', (540, 960)), ('final', (1080, 1920))])
def test_caption_style_preview_matches_draft_and_final_exports(tmp_path, quality, dimensions):
    footage = tmp_path / 'footage.mp4'
    ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=1280x720:r=30:d=3', '-c:v', 'libx264', '-crf', '10', '-pix_fmt', 'yuv420p', footage)
    clip = upload_file(footage)
    telugu = 'నమస్కారం, శ్రీ క్షేత్రం కథ'
    pid, body = framing_project([
        {'id': 'clip', 'visual': 'upload', 'video_asset': clip['id'], 'zoom': 1.2,
         'cues': [{'start': 0, 'end': 1.9, 'text': telugu}]},
        {'id': 'title', 'visual': 'graphic', 'graphic_title': 'Mana katha',
         'cues': [{'start': 0, 'end': 1.9, 'text': 'Idi oka chinna pariksha mana Telugu katha ikkada'}]},
    ], tmp_path)
    c = client()
    bands, scores = {}, {}
    for style in STYLES:
        current = c.get(f'/api/projects/{pid}').json()
        current['project']['caption_style'] = style
        saved = c.put(f'/api/projects/{pid}', {'revision': current['revision'], 'project': current['project']},
                      content_type='application/json')
        assert saved.status_code == 200, saved.content
        directory, manifest = render_over_http(pid, quality)
        assert [cue['text'] for cue in manifest['all_cues']] == [telugu, body['scenes'][1]['cues'][0]['text']]
        reel = directory / 'reel.mp4'
        for scene, at in (('clip', 0.5), ('title', 1.0)):
            data = next(s for s in body['scenes'] if s['id'] == scene)
            res = c.post(f'/api/projects/{pid}/caption-preview',
                         {'scene': data, 'source_time': 0.5 if scene == 'clip' else None, 'caption': data['cues'][0]['text'],
                          'quality': quality, 'caption_style': style}, content_type='application/json')
            assert res.status_code == 200, res.content
            preview = tmp_path / f'{style}-{scene}.jpg'
            preview.write_bytes(res.content)
            assert media.probe(preview, allow_still=True)['streams'][0]['width'] == dimensions[0]
            offset = next(s['start_frame'] for s in manifest['scenes'] if s['scene_id'] == scene)
            exported = tmp_path / f'{style}-{scene}-export.png'
            ffmpeg('-i', reel, '-vf', f'select=eq(n\\,{offset + round(at * 30)})', '-frames:v', '1', exported)
            a = caption_band(preview, tmp_path / f'{style}-{scene}-a.png')
            b = caption_band(exported, tmp_path / f'{style}-{scene}-b.png')
            bands[(style, scene)] = (a, b)
            score = scores[f'{style}/{scene}'] = psnr(a, b)
            assert score > 30, (style, scene, score)
    # Each style looks different in the export, and a mismatched preview is detected.
    for scene in ('clip', 'title'):
        for one, two in zip(STYLES, STYLES[1:]):
            assert psnr(bands[(one, scene)][1], bands[(two, scene)][1]) < 30, (one, two, scene)
    scores['wrong_style'] = psnr(bands[('classic', 'clip')][0], bands[('black_box', 'clip')][1])
    assert scores['wrong_style'] < 25
    (DATA.parent / f'caption-style-psnr-{quality}.json').write_text(json.dumps(scores), 'utf-8')
    graphic = c.post(f'/api/projects/{pid}/framing-preview', {'scene': body['scenes'][1]}, content_type='application/json')
    assert graphic.status_code == 422                                          # Stage 3 behaviour unchanged


def test_caption_style_picker_dom_behaviour():
    page = DATA.parent / 'caption-style.html'
    page.write_bytes(client().get('/').content)
    subprocess.run(['node', 'tests/audit_caption_style_ui.cjs', str(page)], check=True, timeout=60)
