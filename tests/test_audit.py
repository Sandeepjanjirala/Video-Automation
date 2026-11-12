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
    monkeypatch.setattr(media, 'audio_seconds', lambda *a: 1.0)
    monkeypatch.setattr(media, 'speech_for', lambda *a, **k: None)
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
    synthesized = len(calls)

    first = caption_timing(row['id'])['scene1']
    assert first['status'] == 'measured'
    assert first['duration'] == pytest.approx(media.duration(Path(voice['audio_path'])))
    assert first['duration'] == pytest.approx(2.0, abs=0.1)
    assert first['audio_url'] == f"/api/jobs/{job['id']}/audio/scene1"
    roman = first['automatic']['roman']
    assert roman['alignment'] == media.WORD_TIMING            # Edge's measured word times, mapped in word order
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
    assert len(calls) == synthesized  # measuring never synthesizes
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
    # Editing the text keeps the recording (it is what was said) and asks for a check instead.
    edited = copy.deepcopy(row['project'])
    edited['scenes'][0]['audio_text_hash'] = 'stale'
    after = caption_timing(row['id'], edited)['scene1']
    assert after['status'] == 'measured' and 'edited after this recording was attached' in after['warning']
    assert 'warning' not in timing


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
    assert second['alignment'] == media.WORD_TIMING
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
        for size in ((1080, 1920), (540, 960), (1920, 1080), (960, 540)):
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
        if style == 'translucent':
            assert d[5] == '&HFF000000'                                     # no halo around text on the panel
        seen.add(tuple(d[3:18]))
    assert len(seen) == 4


@pytest.mark.parametrize('style', STYLES)
def test_telugu_captions_use_complex_shaping_in_every_style(tmp_path, style):
    """Joined letters (conjuncts, vowel signs) need HarfBuzz shaping; the export's default must apply it."""
    ass = tmp_path / 'captions.ass'
    media.write_ass(ass, [{'start': 0, 'end': 5, 'text': 'క్షమించండి శ్రీ స్త్రీ ద్వారం'}],
                    {'caption_size': 64, 'caption_bottom': 300, 'caption_style': style})
    # media.CAPTION_FILTER is what exports and previews use (FFmpeg 7.1's subtitles filter skips shaping).
    frames = {}
    for name, vf in (('export', media.CAPTION_FILTER), ('complex', 'ass=captions.ass:shaping=complex'),
                     ('simple', 'ass=captions.ass:shaping=simple')):
        frames[name] = tmp_path / f'shape-{name}.png'
        media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0x406080:s=1080x1920',
                   '-vf', vf, '-frames:v', '1', frames[name]], cwd=tmp_path)
    assert psnr(frames['export'], frames['complex']) == float('inf')         # exports shape Telugu
    assert psnr(frames['export'], frames['simple']) < 40                     # and shaping changes the glyphs


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
    # Each style looks different in the export (on the bright clip; on the near-black title card a
    # black box is barely distinguishable from an outline), and a mismatched preview is detected.
    for one, two in zip(STYLES, STYLES[1:]):
        assert psnr(bands[(one, 'clip')][1], bands[(two, 'clip')][1]) < 30, (one, two)
    scores['wrong_style'] = psnr(bands[('classic', 'clip')][0], bands[('black_box', 'clip')][1])
    assert scores['wrong_style'] < 25
    (DATA.parent / f'caption-style-psnr-{quality}.json').write_text(json.dumps(scores), 'utf-8')
    graphic = c.post(f'/api/projects/{pid}/framing-preview', {'scene': body['scenes'][1]}, content_type='application/json')
    assert graphic.status_code == 422                                          # Stage 3 behaviour unchanged


def test_caption_style_picker_dom_behaviour():
    page = DATA.parent / 'caption-style.html'
    page.write_bytes(client().get('/').content)
    subprocess.run(['node', 'tests/audit_caption_style_ui.cjs', str(page)], check=True, timeout=60)


# One-click draft video -------------------------------------------------------------------

def fake_gemini(monkeypatch, rows=None, calls=None):
    """Answer like Gemini: Telugu narration, Roman captions, keywords and a heading per scene."""
    calls = [] if calls is None else calls

    def gemini(prompt, grounded=False, **options):
        calls.append(prompt)
        scenes = json.loads(prompt[prompt.index('Scenes: ') + len('Scenes: '):])
        out = rows if rows is not None else [
            {'index': s['index'], 'narration': f'దృశ్యం {s["index"] + 1}',
             'roman': s['roman'] or f'Drushyam {s["index"] + 1}', 'keywords': f'**octopus** underwater {s["index"]}',
             'graphic_title': f'Title: Heading {s["index"]}'} for s in scenes]
        return json.dumps({'scenes': out}, ensure_ascii=False), {}
    monkeypatch.setattr(providers, 'gemini', gemini)
    return calls


def test_caption_cleaning_matches_chat_formatting():
    clean = providers.clean_caption
    assert clean('Title: 3 Mind-Blowing Random Facts!') == '3 Mind-Blowing Random Facts!'
    assert clean('**Fact Number 1:** Octopus ki moodu hearts untayi!') == 'Fact Number 1: Octopus ki moodu hearts untayi!'
    assert clean('## Scene 2: Meeku teliyani') == 'Meeku teliyani'
    assert clean('Comment cheyyandi! #facts') == 'Comment cheyyandi! #facts'
    assert clean('Hook – Meeku telusa?') == 'Meeku telusa?'
    assert clean('Titles are fun') == 'Titles are fun'


def test_fill_scenes_completes_missing_parts_and_keeps_user_text(monkeypatch):
    calls = fake_gemini(monkeypatch)
    body = project()
    base = body['scenes'][0]
    body['scenes'] = [
        {**base, 'id': 'title', 'roman': 'Title: 3 Mind-Blowing Random Facts!', 'narration': '', 'graphic_title': ''},
        {**base, 'id': 'stale', 'roman': '**Fact 1:** Octopus', 'narration': 'పాత కథనం',
         'narration_source_roman': '**Fact 1:** Octopus', 'keywords': 'my own words',
         'cues': [{'start': 0, 'end': 1, 'text': 'Old'}]},
        {**base, 'id': 'telugu', 'roman': '', 'narration': 'తెలుగు మాత్రమే', 'graphic_title': 'Mine'},
        {**base, 'id': 'kept', 'roman': 'Idi oka pariksha.', 'narration': TELUGU_LINE,
         'narration_source_roman': 'Idi oka pariksha.', 'keywords': 'test', 'graphic_title': 'Kept'},
        {**base, 'id': 'empty', 'roman': '', 'narration': '', 'keywords': '', 'graphic_title': ''},
    ]
    scenes = {s['id']: s for s in providers.fill_scenes(Project.model_validate(body).model_dump())}
    assert len(calls) == 1
    assert '"Title:' not in calls[0] and '**' not in calls[0]           # the model sees cleaned text
    t = scenes['title']
    assert (t['roman'], t['narration'], t['narration_source_roman']) == ('3 Mind-Blowing Random Facts!', 'దృశ్యం 1', '3 Mind-Blowing Random Facts!')
    assert (t['keywords'], t['graphic_title']) == ('octopus underwater 0', 'Heading 0')
    s = scenes['stale']                                                  # cleaned Roman -> narration regenerated
    assert (s['roman'], s['narration'], s['cues'], s['keywords']) == ('Fact 1: Octopus', 'దృశ్యం 2', [], 'my own words')
    assert s['narration_source_roman'] == 'Fact 1: Octopus'
    assert (scenes['telugu']['roman'], scenes['telugu']['narration'], scenes['telugu']['graphic_title']) == ('Drushyam 3', 'తెలుగు మాత్రమే', 'Mine')
    k = scenes['kept']
    assert (k['roman'], k['narration'], k['keywords'], k['graphic_title']) == ('Idi oka pariksha.', TELUGU_LINE, 'test', 'Kept')
    assert scenes['empty']['roman'] == '' and scenes['empty']['keywords'] == ''
    # Nothing missing: no model call at all.
    again = Project.model_validate({**body, 'scenes': list(scenes.values())[:4]}).model_dump()
    assert providers.fill_scenes(again) and len(calls) == 1


@pytest.mark.parametrize('rows,message', [
    ([{'index': 0, 'narration': 'Not Telugu', 'roman': 'x', 'keywords': 'k', 'graphic_title': 'g'}], 'did not return Telugu'),
    ([], 'skipped scene 1'),
])
def test_fill_scenes_rejects_unusable_model_output(monkeypatch, rows, message):
    fake_gemini(monkeypatch, rows=rows)
    body = project()
    body['scenes'][0].update(roman='Octopus ki moodu hearts', narration='')
    with pytest.raises(ValueError, match=message):
        providers.fill_scenes(body)


def test_fill_scenes_retries_a_scene_without_telugu_with_the_scene_converter(monkeypatch):
    fake_gemini(monkeypatch, rows=[{'index': 0, 'narration': '3 Mind-Blowing Facts!', 'roman': 'x',
                                    'keywords': 'surprised face', 'graphic_title': 'Facts'}])
    from studio import transliteration
    monkeypatch.setattr(transliteration, 'convert', lambda text, provider: {'telugu': '3 మైండ్-బ్లోయింగ్ ఫాక్ట్స్!'})
    body = project()
    body['scenes'][0].update(roman='3 Mind-Blowing Facts!', narration='')
    scene = providers.fill_scenes(body)[0]
    assert (scene['narration'], scene['keywords']) == ('3 మైండ్-బ్లోయింగ్ ఫాక్ట్స్!', 'surprised face')


def test_fill_draft_job_applies_without_touching_visuals(monkeypatch, tmp_path):
    fake_gemini(monkeypatch)
    picture = tmp_path / 'scene.png'
    ffmpeg('-f', 'lavfi', '-i', 'color=c=teal:s=640x960', '-frames:v', '1', picture)
    image = upload_file(picture)
    body = project()
    body['scenes'][0].update(roman='**Octopus** ki moodu hearts', narration='', visual='image', video_asset=image['id'],
                             zoom=1.5, crop_y=0.2, rights_reviewed=True)
    c = client()
    pid = c.post('/api/projects', body, content_type='application/json').json()['id']
    queued = c.post(f'/api/projects/{pid}/jobs', {'revision': 1, 'kind': 'draft', 'fill': True}, content_type='application/json')
    assert queued.status_code == 200, queued.content
    assert queued.json()['snapshot']['options']['fill'] is True
    from studio.tasks import task_draft
    assert task_draft.run(queued.json()['id'])['status'] == 'succeeded'
    applied = c.post(f"/api/projects/{pid}/apply-job/{queued.json()['id']}", {}, content_type='application/json')
    assert applied.status_code == 200, applied.content
    scene = applied.json()['project']['project']['scenes'][0]
    assert (scene['id'], scene['visual'], scene['video_asset'], scene['zoom'], scene['crop_y']) == ('scene1', 'image', image['id'], 1.5, 0.2)
    assert (scene['roman'], scene['narration'], scene['keywords']) == ('Octopus ki moodu hearts', 'దృశ్యం 1', 'octopus underwater 0')
    assert store.preflight(applied.json()['project']['project'])['ready']


def test_one_click_draft_dom_behaviour():
    page = DATA.parent / 'autopilot.html'
    page.write_bytes(client().get('/').content)
    subprocess.run(['node', 'tests/audit_autopilot_ui.cjs', str(page)], check=True, timeout=60)


# Footage matched to the script ---------------------------------------------------------------

def pixabay_hit(id, tags, width=1920, height=1080, kind='film', downloads=1000, low=False, preview='https://cdn.pixabay.com/p.jpg'):
    return {'id': id, 'provider': 'pixabay', 'tags': tags, 'duration': 12, 'animation': kind == 'animation',
            'low_quality': low, 'downloads': downloads, 'preview': preview, 'creator': 'Author',
            'source_url': f'https://pixabay.com/videos/id-{id}/', 'license_url': 'https://pixabay.com/service/license-summary/',
            'video': {'url': f'https://cdn.pixabay.com/{id}.mp4', 'width': width, 'height': height, 'size': 1024}}


def test_stock_search_reports_previews_quality_and_a_sharp_file(monkeypatch):
    from studio import stock
    monkeypatch.setattr(providers, 'settings', lambda: {'online_enabled': True, 'pixabay_key': 'fixture'})
    big = stock.SHARP_DOWNLOAD_LIMIT + 1

    class Response:
        def __init__(self, hits): self.hits = hits
        def json(self): return {'hits': self.hits}

    def file(size, w, h, name):
        return {'url': f'https://cdn.pixabay.com/{name}.mp4', 'width': w, 'height': h, 'size': size,
                'thumbnail': f'https://cdn.pixabay.com/{name}.jpg'}
    hits = [{'id': 1, 'type': 'film', 'tags': 'octopus, ocean', 'downloads': 50, 'isLowQuality': False, 'pageURL': 'https://pixabay.com/videos/id-1/',
             'videos': {'large': file(40_000_000, 3840, 2160, 'l'), 'medium': file(9_000_000, 1920, 1080, 'm'), 'tiny': file(1000, 640, 360, 't')}},
            {'id': 2, 'type': 'animation', 'tags': 'planet', 'isLowQuality': True, 'pageURL': 'https://pixabay.com/videos/id-2/',
             'videos': {'large': file(big, 3840, 2160, 'l2'), 'medium': file(9_000_000, 1920, 1080, 'm2'),
                        'tiny': {**file(1000, 640, 360, 't2'), 'thumbnail': 'https://evil.test/t.jpg'}}}]
    params = {}
    monkeypatch.setattr(providers, 'request', lambda method, url, **kw: params.update(kw['params']) or Response(hits))
    first, second = providers.stock_search('octopus ' + uuid.uuid4().hex)
    assert params['per_page'] == 20
    assert (first['video']['height'], first['preview'], first['animation'], first['low_quality']) == (2160, 'https://cdn.pixabay.com/t.jpg', False, False)
    # 4K is skipped when the download would be too big; previews only from the provider's image host.
    assert (second['video']['height'], second['preview'], second['animation'], second['low_quality']) == (1080, 'https://cdn.pixabay.com/m2.jpg', True, True)
    assert not stock.safe_preview_url('https://cdn.pixabay.com.evil.test/a.jpg', 'pixabay')
    assert stock.safe_preview_url('https://images.pexels.com/a.jpg', 'pexels') and not stock.safe_preview_url('https://images.pexels.com/a.jpg', 'pixabay')


def footage_fixture(monkeypatch, gemini):
    """Three scenes, a fake stock library, preview frames and downloads."""
    from studio import footage
    library = {
        'octopus tentacles': [pixabay_hit(1, 'heart, heartbeat'), pixabay_hit(2, 'octopus, tentacles, ocean')],
        'octopus': [pixabay_hit(2, 'octopus, tentacles, ocean'), pixabay_hit(3, 'octopus, aquarium', 1080, 1920)],
        'honey jar': [pixabay_hit(4, 'honey, jar, kitchen'), pixabay_hit(5, 'honey, green screen, subscribe')],
        'random facts': [pixabay_hit(6, 'particles, abstract', kind='animation', low=True), pixabay_hit(7, 'question mark, facts')],
        'woman surprised phone': [pixabay_hit(8, 'woman, phone, surprised')],
    }
    searched, downloaded = [], []

    def search(query, provider='auto'):
        searched.append(query)
        return copy.deepcopy(library.get(query, []))
    monkeypatch.setattr(providers, 'stock_search', search)
    monkeypatch.setattr(providers, 'download_stock', lambda id, provider, landscape=False:
                        downloaded.append(id if not landscape else ('landscape', id)) or {'id': f'{id:032x}', 'provider': provider})
    monkeypatch.setattr(footage, 'preview', lambda hit: ('image/jpeg', f'frame {hit["id"]}'.encode()))
    monkeypatch.setattr(providers, 'gemini', gemini)
    body = project()
    base = body['scenes'][0]
    body['scenes'] = [{**base, 'id': 'title', 'roman': '3 Random Facts', 'keywords': 'random facts'},
                      {**base, 'id': 'octo', 'roman': 'Octopus ki moodu hearts untayi!', 'keywords': ''},
                      {**base, 'id': 'honey', 'roman': 'Honey eppatiki paadavadu', 'keywords': 'honey jar'}]
    return footage, Project.model_validate(body).model_dump(), searched, downloaded


def test_footage_plans_from_the_script_and_lets_ai_pick_from_previews(monkeypatch):
    prompts = []

    def gemini(prompt, grounded=False, images=(), **options):
        prompts.append((prompt, list(images)))
        if 'found no good stock clip' in prompt:  # the second search, for the shots without a good clip
            shots = json.loads(prompt[prompt.index('SHOTS: ') + 7:])
            assert [(s['index'], s['shot'], s['tried'], s['seen']) for s in shots] == [(0, 1, ['random facts'], ['question mark, facts', 'particles, abstract'])]
            return json.dumps({'scenes': [{'index': 0, 'shot': 1, 'description': 'A surprised woman looking at her phone',
                                           'queries': ['random facts', 'woman surprised phone']}]}), {}
        if not images:  # planning
            assert 'Octopus ki moodu hearts untayi!' in prompt and 'NEED: [0, 1, 2, 3]' in prompt
            return json.dumps({'scenes': [{'index': 1, 'shot': 'An octopus moving its tentacles on the sea floor',
                                           'queries': ['**octopus tentacles**', 'octopus']},
                                          {'index': 2, 'shot': 'Golden honey dripping into a jar', 'queries': ['honey jar']}]}), {}
        if 'clip pixabay-8' in images[0][0]:
            assert 'intended shot: A surprised woman looking at her phone' in images[0][0]
            return json.dumps({'scenes': [{'index': 0, 'picks': [{'candidate': 1, 'score': 8}]}]}), {}
        return json.dumps({'scenes': [{'index': 0, 'picks': [{'candidate': 1, 'score': 3}]},   # nothing fits: searched again
                                      {'index': 1, 'picks': [{'candidate': 2, 'score': 9}]},
                                      {'index': 2, 'picks': [{'candidate': 1, 'score': 8}]},
                                      {'index': 3, 'picks': [{'candidate': 1, 'score': 9}, {'candidate': 2, 'score': 6}]}]}), {}
    footage, body, searched, downloaded = footage_fixture(monkeypatch, gemini)
    body['scenes'].append({**body['scenes'][2], 'id': 'honey2'})       # wants the same clip: gets its next pick
    steps = []
    selected = {s['scene_id']: s for s in footage.find(body, body['scenes'], steps.append)}
    assert len(prompts) == 4                                          # plan, check, plan again, check again
    assert set(searched) == {'random facts', 'octopus tentacles', 'octopus', 'honey jar', 'woman surprised phone'}
    # Gemini saw each scene's line and intended shot, then the phrases' best frames in turn: the octopus
    # (first phrase), the octopus aquarium clip (second phrase), then the heart.
    labels = [label for label, _, _ in prompts[1][1]]
    assert 'Scene 1: line "Octopus ki moodu hearts untayi!"; intended shot: An octopus moving its tentacles' in labels[2]
    assert labels[2].endswith('Scene 1 candidate 1 (clip pixabay-2):')
    assert [data for _, _, data in prompts[1][1][2:5]] == [b'frame 2', b'frame 3', b'frame 1']
    assert selected['octo']['asset']['id'] == f'{3:032x}' and 'AI-checked match (9/10): An octopus' in selected['octo']['match']
    assert selected['honey']['asset']['id'] == f'{4:032x}'           # green screen ranked last
    # The title found nothing fitting, so it was planned again from what was rejected and found a reaction shot.
    assert selected['title']['asset']['id'] == f'{8:032x}'
    assert selected['title']['match'] == 'AI-checked match (8/10) after a second search: A surprised woman looking at her phone'
    assert selected['honey2']['asset']['id'] == f'{5:032x}' and selected['honey2']['match'].startswith('AI-checked match (6/10)')
    assert sorted(downloaded) == [3, 4, 5, 8]                    # downloaded in parallel
    assert steps[0] == 'Reading the script and planning shots' and 'Checking candidate clips with AI' in steps
    assert 'Looking again for better clips (1 shot(s))' in steps


def test_footage_keeps_the_first_clip_when_the_second_search_finds_nothing_better(monkeypatch):
    def gemini(prompt, grounded=False, images=(), **options):
        if 'found no good stock clip' in prompt:
            return json.dumps({'scenes': [{'index': 2, 'shot': 1, 'description': 'Honey', 'queries': ['woman surprised phone']}]}), {}
        if not images:
            return json.dumps({'scenes': [{'index': 2, 'shot': 'Honey in a jar', 'queries': ['honey jar']}]}), {}
        if 'clip pixabay-8' in images[0][0]:
            return json.dumps({'scenes': [{'index': 2, 'picks': [{'candidate': 1, 'score': 5}]}]}), {}
        return json.dumps({'scenes': [{'index': 2, 'picks': [{'candidate': 1, 'score': 6}]}]}), {}
    footage, body, searched, downloaded = footage_fixture(monkeypatch, gemini)
    selected = footage.find(body, [body['scenes'][2]])
    assert selected[0]['asset']['id'] == f'{4:032x}' and selected[0]['match'] == 'AI-checked match (6/10): Honey in a jar'
    assert 'woman surprised phone' in searched and downloaded == [4]


def test_footage_plans_one_clip_per_part_of_a_long_line_in_spoken_order(monkeypatch):
    def gemini(prompt, grounded=False, images=(), **options):
        if 'found no good stock clip' in prompt:
            raise AssertionError('every shot found a good clip')
        if not images:
            assert 'SHOTS PER SCENE: {"2": 3}' in prompt
            return json.dumps({'scenes': [{'index': 2, 'shots': [
                {'words': 'Honey ni sarigga store chesthe vela samvatsaralu aina paadavakunda undagaladhu', 'shot': 'Honey in a jar',
                 'queries': ['honey jar']},
                {'words': 'ani scientists cheptunnaru,', 'shot': 'A scientist in a lab', 'queries': ['scientist lab']},
                {'words': 'Egypt pyramids lo kuda dorikindhi ani antunnaru', 'shot': 'The pyramids of Giza', 'queries': ['pyramids egypt']}]}]}), {}
        labels = [label for label, _, _ in images]
        assert any('Scene 2 shot 2 of 3: said while "ani scientists cheptunnaru,"' in label and 'intended shot: A scientist in a lab' in label
                   for label in labels)
        return json.dumps({'scenes': [{'index': 2, 'shot': 1, 'picks': [{'candidate': 1, 'score': 9}]},
                                      {'index': 2, 'shot': 2, 'picks': [{'candidate': 1, 'score': 8}]},
                                      {'index': 2, 'shot': 3, 'picks': [{'candidate': 1, 'score': 9}]}]}), {}
    footage, body, searched, downloaded = footage_fixture(monkeypatch, gemini)
    footage_library = {'scientist lab': [pixabay_hit(10, 'scientist, laboratory')], 'pyramids egypt': [pixabay_hit(11, 'pyramids, egypt, desert')]}
    search = providers.stock_search
    monkeypatch.setattr(providers, 'stock_search', lambda query, provider='auto': copy.deepcopy(footage_library.get(query)) or search(query, provider))
    body['scenes'][2]['roman'] = ('Honey ni sarigga store chesthe vela samvatsaralu aina paadavakunda undagaladhu ani '
                                  'scientists cheptunnaru, Egypt pyramids lo kuda dorikindhi ani antunnaru')
    honey = footage.find(body, [body['scenes'][2]])[0]
    # The clips follow the words: honey, then the scientist, then the pyramids, each for its part of the line.
    assert [honey['asset']['id']] + [a['id'] for a in honey['extra_assets']] == [f'{4:032x}', f'{10:032x}', f'{11:032x}']
    assert honey['shot_weights'] == [0.5132, 0.1776, 0.3092]   # 78, 27 and 47 characters
    assert honey['match'].startswith('AI-checked match (9/10) + 2 more shot(s)')


def test_planned_shots_follow_the_words_of_the_line():
    from studio.framing import planned_parts, scene_shots, shot_lengths
    scene = {'visual': 'stock', 'video_asset': 'a' * 32, 'extra_shots': ['b' * 32, 'c' * 32], 'shot_weights': [0.55, 0.19, 0.26]}
    assert [s['video_asset'] for s in scene_shots(scene, 10.0)] == ['a' * 32, 'b' * 32, 'c' * 32]
    assert [round(x, 2) for x in shot_lengths(scene, 10.0, 3)] == [5.5, 1.9, 2.6]
    # A part too short to register stays on the clip before it.
    assert [s['video_asset'] for s in scene_shots(scene, 5.0)] == ['a' * 32, 'c' * 32]
    assert [round(x, 2) for x in shot_lengths(scene, 5.0, 2)] == [3.7, 1.3]
    # Unplanned or mismatched shares: equal parts, one clip about every 4 seconds as before.
    assert planned_parts({**scene, 'shot_weights': [0.5, 0.5]}, 10.0) is None
    assert shot_lengths({**scene, 'shot_weights': []}, 9.0, 3) == [3.0, 3.0, 3.0]


def test_footage_without_gemini_uses_keywords_and_ranking(monkeypatch):
    def gemini(prompt, grounded=False, images=(), **options):
        raise ValueError('Gemini rate limit reached. Try again shortly.')
    footage, body, searched, downloaded = footage_fixture(monkeypatch, gemini)
    body['scenes'].append({**body['scenes'][2], 'id': 'honey2'})       # the same words: never the same clip twice
    selected = {s['scene_id']: s for s in footage.find(body, body['scenes'])}
    assert selected['octo']['error'] == 'Add English search keywords'
    assert selected['title']['asset']['id'] == f'{7:032x}'           # real film over a low-quality animation
    assert (selected['honey']['asset']['id'], selected['honey2']['asset']['id']) == (f'{4:032x}', f'{5:032x}')
    assert selected['honey']['match'] == 'Best keyword match'


def test_stock_all_job_uses_the_footage_matcher(monkeypatch):
    from studio import footage
    seen = {}

    def find(project, scenes, progress):
        progress('Searching footage 1/1')
        seen['ids'] = [s['id'] for s in scenes]
        return [{'scene_id': 'scene1', 'asset': {'id': 'c' * 32}, 'match': 'AI-checked match (9/10)'}]
    monkeypatch.setattr(footage, 'find', find)
    body = project()
    body['scenes'][0].update(visual='upload', video_asset='')
    row = store.save_project(body)
    job = store.enqueue(row['id'], 1, 'stock_all')
    from studio.tasks import task_stock_all
    assert task_stock_all.run(job['id'])['status'] == 'succeeded'
    assert seen['ids'] == ['scene1']
    assert store.job(job['id'])['result']['selections'][0]['match'] == 'AI-checked match (9/10)'


def test_gemini_waits_out_a_per_minute_limit_but_not_a_daily_one(monkeypatch):
    monkeypatch.setattr(providers, 'online', lambda: {'gemini_key': 'fixture', 'gemini_model': 'gemini-test', 'daily_ai_calls': 100})
    slept, answers = [], []
    monkeypatch.setattr(providers.time, 'sleep', slept.append)

    class Response:
        def json(self):
            return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"ok": 1}'}]}}]}

    def request(method, url, **kw):
        answer = answers.pop(0)
        if isinstance(answer, str):
            raise ValueError(answer)
        assert kw['json']['contents'][0]['parts'][1:] == [{'text': 'Scene 0 candidate 1:'}, {'inline_data': {'mime_type': 'image/jpeg', 'data': 'ZnJhbWU='}}]
        return Response()
    monkeypatch.setattr(providers, 'request', request)
    answers[:] = ['Provider returned HTTP 429: Quota exceeded for requests per minute. Please retry in 12.5s.', Response()]
    assert providers.gemini('Pick', images=[('Scene 0 candidate 1:', 'image/jpeg', b'frame')])[0] == '{"ok": 1}'
    assert slept == [13.5]
    answers[:] = ['Provider returned HTTP 429: Generate content requests per day limit exceeded. Please retry in 48m23.5s.']
    with pytest.raises(ValueError, match='per day'):
        providers.gemini('Pick', images=[('Scene 0 candidate 1:', 'image/jpeg', b'frame')])
    assert slept == [13.5]


# Gemini backup models ------------------------------------------------------------------------

def fake_gemini_http(monkeypatch, answers, fallbacks='backup-a, backup-b'):
    """Each model answers with its entry in answers: an error message or a JSON text."""
    monkeypatch.setattr(providers, 'online', lambda: {'gemini_key': 'fixture', 'gemini_model': 'main-model',
                                                      'gemini_fallback_models': fallbacks, 'daily_ai_calls': 100})
    monkeypatch.setattr(providers.time, 'sleep', lambda s: None)
    called = []

    class Response:
        def __init__(self, text): self.text = text
        def json(self):
            return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': self.text}]}}]}

    def request(method, url, **kw):
        model = url.split('/models/')[1].split(':')[0]
        called.append(model)
        answer = answers[model]
        if answer.startswith('Provider returned'):
            raise ValueError(answer)
        return Response(answer)
    monkeypatch.setattr(providers, 'request', request)
    return called


def test_gemini_moves_to_backup_models_when_a_model_is_out_of_quota_busy_or_retired(monkeypatch):
    called = fake_gemini_http(monkeypatch, {
        'main-model': 'Provider returned HTTP 429: Quota exceeded for requests per day. Please retry in 48m2s.',
        'backup-a': 'Provider returned HTTP 404: This model is no longer available to new users.',
        'backup-b': '{"ok": "backup"}'})
    assert providers.gemini('Hello')[0] == '{"ok": "backup"}'
    assert called == ['main-model', 'backup-a', 'backup-b']
    assert providers.gemini_models({'gemini_model': 'main-model', 'gemini_fallback_models': ' a , main-model,,bad model!, b'}) == ['main-model', 'a', 'b']


def test_gemini_backup_models_are_not_used_for_request_errors_and_report_when_all_fail(monkeypatch):
    called = fake_gemini_http(monkeypatch, {'main-model': 'Provider returned HTTP 400: Invalid JSON payload.',
                                            'backup-a': '{"ok": 1}', 'backup-b': '{"ok": 2}'})
    with pytest.raises(ValueError, match='HTTP 400'):
        providers.gemini('Hello')
    assert called == ['main-model']                       # a bad request would fail on every model
    busy = 'Provider returned HTTP 503: This model is currently experiencing high demand.'
    called = fake_gemini_http(monkeypatch, {'main-model': busy, 'backup-a': busy, 'backup-b': busy})
    with pytest.raises(ValueError, match=r'Every Gemini model is busy or out of quota \(main-model, backup-a, backup-b\)'):
        providers.gemini('Hello')
    assert called == ['main-model', 'backup-a', 'backup-b', 'backup-b', 'backup-b']   # only the last one waits
    from studio.config import DEFAULTS
    assert DEFAULTS['gemini_fallback_models'].startswith('gemini-3-flash-preview, ')


# Deleting a project --------------------------------------------------------------------------

def test_delete_project_removes_its_data_and_keeps_library_media(tmp_path):
    picture = tmp_path / 'scene.png'
    ffmpeg('-f', 'lavfi', '-i', 'color=c=teal:s=640x960', '-frames:v', '1', picture)
    image = upload_file(picture)
    c = client()
    body = project()
    body['scenes'][0].update(visual='image', video_asset=image['id'])
    pid = c.post('/api/projects', body, content_type='application/json').json()['id']
    keep = c.post('/api/projects', project(), content_type='application/json').json()['id']
    saved = c.put(f'/api/projects/{pid}', {'revision': 1, 'project': {**body, 'title': 'Second revision'}}, content_type='application/json')
    assert saved.status_code == 200, saved.content
    assert c.put(f'/api/projects/{pid}/draft', {'title': 'autosave'}, content_type='application/json').status_code in (200, 201)
    job = store.enqueue(pid, 2, 'research')
    store.progress(job['id'], 'Complete', 'succeeded', {'duration': 1})
    export = DATA / 'exports' / job['id']
    export.mkdir(parents=True)
    (export / 'reel.mp4').write_bytes(b'video')
    other = store.enqueue(keep, 1, 'research')

    # A job in progress blocks deletion; nothing is removed.
    running = store.enqueue(pid, 2, 'research')
    refused = c.delete(f'/api/projects/{pid}')
    assert refused.status_code == 409 and 'job in progress' in refused.json()['detail']
    assert c.get(f'/api/projects/{pid}').status_code == 200
    store.progress(running['id'], 'Interrupted', 'failed')

    deleted = c.delete(f'/api/projects/{pid}')
    assert deleted.status_code == 200, deleted.content
    assert deleted.json() == {'deleted': pid, 'title': 'Second revision', 'jobs': 2}
    assert c.get(f'/api/projects/{pid}').status_code == 400
    assert c.get(f'/api/projects/{pid}/history').json() == []
    assert c.get(f'/api/projects/{pid}/jobs').json() == []
    assert not Job.objects.filter(project_id=pid).exists() and not export.exists()
    assert [p['id'] for p in c.get('/api/projects').json()] == [keep]
    assert c.get(f"/api/assets/{image['id']}").status_code == 200             # library media stays
    assert store.job(other['id'])['state'] == 'queued'                          # other projects untouched
    assert c.delete(f'/api/projects/{pid}').status_code == 404


def test_delete_project_buttons_dom_behaviour():
    page = DATA.parent / 'projects.html'
    page.write_bytes(client().get('/').content)
    subprocess.run(['node', 'tests/audit_projects_ui.cjs', str(page)], check=True, timeout=60)


# Shot pacing and captions on/off -------------------------------------------------------------

def colour_clip(tmp_path, colour, seconds=3):
    path = tmp_path / f'{colour}.mp4'
    ffmpeg('-f', 'lavfi', '-i', f'color=c={colour}:s=640x360:r=30:d={seconds}', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', path)
    return upload_file(path)['id']


def test_long_scene_cuts_between_its_extra_shots_about_every_four_seconds(tmp_path):
    red, green, blue, white = (colour_clip(tmp_path, c) for c in ('red', 'green', 'blue', 'white'))
    shots = {'visual': 'stock', 'video_asset': red, 'rights_reviewed': True, 'extra_shots': [green, blue, white]}
    # 9 s of narration: three clips of 3 s each (the fourth is not needed).
    pid, _ = framing_project([{'id': 'long', **shots}], tmp_path, tone_seconds=9.0)
    directory, _ = render_over_http(pid, 'draft')
    reel = directory / 'reel.mp4'
    assert [centre_colour(reel, at) for at in (1.5, 4.5, 7.5)] == ['r', 'g', 'b']
    report = json.loads((directory / 'source-report.json').read_text('utf-8'))
    assert [a['id'] for a in report['scenes'][0]['extra_shots']] == [green, blue]
    # Shots planned for the words: each clip shows for its part of the line (here 6 s, 1.5 s and 1.5 s).
    pid, _ = framing_project([{'id': 'planned', **shots, 'extra_shots': [green, blue], 'shot_weights': [0.6667, 0.1667, 0.1667]}],
                             tmp_path, tone_seconds=9.0)
    directory, _ = render_over_http(pid, 'draft')
    assert [centre_colour(directory / 'reel.mp4', at) for at in (1.0, 5.5, 6.7, 8.2)] == ['r', 'r', 'g', 'b']
    # 2 s of narration: the scene's own clip only.
    pid, _ = framing_project([{'id': 'short', **shots}], tmp_path, tone_seconds=2.0)
    directory, _ = render_over_http(pid, 'draft')
    assert [centre_colour(directory / 'reel.mp4', at) for at in (0.5, 1.5)] == ['r', 'r']


def test_scene_shots_use_only_video_scenes_and_default_framing():
    from studio.framing import scene_shots
    scene = {'visual': 'upload', 'video_asset': 'a' * 32, 'extra_shots': ['b' * 32, 'c' * 32], 'zoom': 2.0, 'trim_start': 1.5}
    assert [s['video_asset'] for s in scene_shots(scene, 4.0)] == ['a' * 32]
    shots = scene_shots(scene, 8.5)
    assert [s['video_asset'] for s in shots] == ['a' * 32, 'b' * 32, 'c' * 32]
    assert (shots[0]['zoom'], shots[0]['trim_start'], shots[1]['zoom'], shots[1]['trim_start']) == (2.0, 1.5, 1.0, 0)
    assert scene_shots({**scene, 'visual': 'image'}, 12) == [{**scene, 'visual': 'image'}]


def test_extra_shots_are_applied_from_footage_search_and_cleared_by_a_manual_choice(tmp_path):
    red, green, blue = (colour_clip(tmp_path, c) for c in ('red', 'green', 'blue'))
    body = project()
    body['scenes'][0].update(visual='upload', video_asset='', rights_reviewed=False)
    row = store.save_project(body)
    job = store.enqueue(row['id'], 1, 'stock_all')
    store.progress(job['id'], 'Complete', 'succeeded', {'selections': [
        {'scene_id': 'scene1', 'asset': store.asset(red), 'extra_assets': [store.asset(green), store.asset(blue)],
         'shot_weights': [0.5, 0.2, 0.3]}]})
    c = client()
    applied = c.post(f"/api/projects/{row['id']}/apply-job/{job['id']}", {}, content_type='application/json')
    assert applied.status_code == 200, applied.content
    scene = applied.json()['project']['project']['scenes'][0]
    assert (scene['video_asset'], scene['extra_shots'], scene['visual'], scene['shot_weights']) == (red, [green, blue], 'stock', [0.5, 0.2, 0.3])
    assert store.preflight({**applied.json()['project']['project'], 'scenes': [{**scene, 'rights_reviewed': True}]})['ready']
    assigned = c.post(f"/api/projects/{row['id']}/assign-media", {'asset_id': blue, 'scene_ids': ['scene1']}, content_type='application/json')
    assert assigned.status_code == 200, assigned.content
    scene = assigned.json()['project']['scenes'][0]
    assert (scene['video_asset'], scene['extra_shots'], scene['visual'], scene['shot_weights']) == (blue, [], 'upload', [])
    # A missing extra shot blocks rendering with a clear message.
    broken = {**assigned.json()['project'], 'scenes': [{**scene, 'extra_shots': ['f' * 32]}]}
    assert any('extra shot is unavailable' in e['message'] for e in store.preflight(broken)['errors'])


def test_captions_can_be_turned_off_per_project(tmp_path, monkeypatch):
    body = project()
    assert body['captions_enabled'] is True                                   # on by default
    body['scenes'][0].update(roman='', narration_source_roman='')
    assert any(e['field'] == 'roman' for e in store.preflight(body)['errors'])
    assert store.preflight({**body, 'captions_enabled': False})['ready']       # no Roman captions needed when off
    written = []
    original = media.write_ass
    monkeypatch.setattr(media, 'write_ass', lambda path, cues, project, title=None, total=0, emphasis=None:
                        written.append((list(cues), title)) or original(path, cues, project, title, total, emphasis))
    pid, _ = framing_project([{'id': 'title', 'visual': 'graphic', 'graphic_title': 'Heading'}], tmp_path)
    c = client()
    for enabled in (True, False):
        row = c.get(f'/api/projects/{pid}').json()
        saved = c.put(f'/api/projects/{pid}', {'revision': row['revision'], 'project': {**row['project'], 'captions_enabled': enabled}},
                      content_type='application/json')
        assert saved.status_code == 200, saved.content
        directory, _ = render_over_http(pid, 'draft')
        assert (directory / 'captions.srt').read_text('utf-8').strip()          # the SRT is exported either way
    assert written[0][0] and written[0][1] == 'Heading'
    assert written[-1] == ([], 'Heading')                                       # off: no caption cues, title kept


def test_footage_downloads_extra_shots_for_a_long_scene(monkeypatch):
    def gemini(prompt, grounded=False, images=(), **options):
        if not images:
            return json.dumps({'scenes': []}), {}
        labels = ' '.join(label for label, _, _ in images)
        assert 'Scene 2 (needs 3 clips): line' in labels
        return json.dumps({'scenes': [{'index': 2, 'picks': [{'candidate': 1, 'score': 9}, {'candidate': 2, 'score': 6}]}]}), {}
    footage, body, searched, downloaded = footage_fixture(monkeypatch, gemini)
    body['scenes'][2]['roman'] = ('Honey ni sarigga store chesthe vela samvatsaralu aina paadavakunda undagaladhu ani '
                                  'scientists cheptunnaru, Egypt pyramids lo kuda dorikindhi ani antunnaru')   # 18 words, ~9.6 s
    assert footage.shots_needed(body['scenes'][2]) == 3
    selected = {s['scene_id']: s for s in footage.find(body, [body['scenes'][2]])}
    honey = selected['honey']
    assert honey['asset']['id'] == f'{4:032x}' and [a['id'] for a in honey['extra_assets']] == [f'{5:032x}']
    assert honey['match'].startswith('AI-checked match (9/10) + 1 more shot(s)')
    assert sorted(downloaded) == [4, 5]


def test_captions_switch_and_extra_shots_dom_behaviour():
    page = DATA.parent / 'captions-switch.html'
    page.write_bytes(client().get('/').content)
    subprocess.run(['node', 'tests/audit_shots_ui.cjs', str(page)], check=True, timeout=60)


# YouTube video format (16:9, long-form) ------------------------------------------------------

def test_video_format_limits_scenes_and_target_per_format():
    body = project()
    assert body['format'] == 'reel'                                              # the default
    many = [{**body['scenes'][0], 'id': f's{i}'} for i in range(31)]
    with pytest.raises(ValueError, match='Instagram Reel supports up to 30 scenes; choose YouTube video'):
        Project.model_validate({**body, 'scenes': many})
    with pytest.raises(ValueError, match='Instagram Reel supports a target of up to 3 minutes'):
        Project.model_validate({**body, 'target_seconds': 1200})
    long = Project.model_validate({**body, 'format': 'youtube', 'target_seconds': 1200,
                                   'scenes': [{**body['scenes'][0], 'id': f's{i}'} for i in range(400)]})
    assert len(long.scenes) == 400
    with pytest.raises(ValueError, match='at most 400 items'):
        Project.model_validate({**body, 'format': 'youtube', 'scenes': [{**body['scenes'][0], 'id': f's{i}'} for i in range(401)]})


def test_youtube_timeline_allows_long_videos_and_reels_stay_three_minutes():
    from studio.timeline import compute_timeline
    body = project()
    body['scenes'] = [{**body['scenes'][0], 'id': f's{i}'} for i in range(300)]
    voices = [{'scene_id': f's{i}', 'duration': 4.0, 'cues': []} for i in range(300)]
    timeline = compute_timeline({**body, 'format': 'youtube'}, voices)
    assert round(timeline.total_duration) == 1200                                    # 20 minutes
    with pytest.raises(ValueError, match='Instagram Reel supports up to 3 minutes.*or choose YouTube video'):
        compute_timeline(body, voices)


def test_youtube_captions_are_laid_out_for_a_landscape_frame(tmp_path):
    from studio.formats import caption_margin, size
    body = project()
    reel, youtube = tmp_path / 'reel.ass', tmp_path / 'youtube.ass'
    long_text = 'Mana Bhoomi meedha unna samudralu chala lothuga unnayi mariyu vatilo enno rahasyalu unnayi'
    media.write_ass(reel, [{'start': 0, 'end': 2, 'text': long_text}], body)
    media.write_ass(youtube, [{'start': 0, 'end': 2, 'text': long_text}], {**body, 'format': 'youtube'})
    r, y = reel.read_text('utf-8'), youtube.read_text('utf-8')
    assert 'PlayResX: 1080\nPlayResY: 1920' in r and ',2,90,140,300,1' in r              # reels unchanged
    assert 'PlayResX: 1920\nPlayResY: 1080' in y and ',2,200,200,84,1' in y
    assert r.split('Dialogue:')[1].count(r'\N') > y.split('Dialogue:')[1].count(r'\N')  # wider lines
    assert (size({'format': 'youtube'}, 'draft'), size({}, 'draft'), caption_margin({'format': 'youtube', 'caption_bottom': 600})) == \
           ((960, 540), (540, 960), 168)


def test_youtube_video_renders_landscape_draft_and_final(tmp_path):
    clip = tmp_path / 'wide.mp4'
    ffmpeg('-f', 'lavfi', '-i', 'color=c=blue:s=1920x1080:r=30:d=3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', clip)
    video = upload_file(clip)['id']
    pid, body = framing_project([{'id': 'clip', 'visual': 'stock', 'video_asset': video, 'rights_reviewed': True},
                                 {'id': 'title', 'visual': 'graphic', 'graphic_title': 'Chapter one'}], tmp_path)
    c = client()
    row = c.get(f'/api/projects/{pid}').json()
    saved = c.put(f'/api/projects/{pid}', {'revision': row['revision'], 'project': {**row['project'], 'format': 'youtube', 'target_seconds': 600}},
                  content_type='application/json')
    assert saved.status_code == 200, saved.content
    for quality, dimensions in (('draft', [960, 540]), ('final', [1920, 1080])):
        directory, manifest = render_over_http(pid, quality)
        reel = directory / 'reel.mp4'
        stream = next(s for s in media.probe(reel)['streams'] if s['codec_type'] == 'video')
        assert [stream['width'], stream['height']] == dimensions
        assert centre_colour(reel, 1.0) == 'b'                                       # the landscape clip fills the frame
        assert json.loads((directory / 'source-report.json').read_text('utf-8'))['dimensions'] == dimensions
    # The exact-frame preview uses the project's (or the editor's unsaved) format.
    preview = c.post(f'/api/projects/{pid}/framing-preview', {'scene': body['scenes'][0] | {'video_asset': video, 'visual': 'stock'},
                                                            'quality': 'draft'}, content_type='application/json')
    assert preview.status_code == 200, preview.content
    assert json.loads(preview['X-Framing-Geometry'])['width'] == 960
    reel_preview = c.post(f'/api/projects/{pid}/framing-preview', {'scene': body['scenes'][0] | {'video_asset': video, 'visual': 'stock'},
                                                                 'quality': 'draft', 'format': 'reel'}, content_type='application/json')
    assert json.loads(reel_preview['X-Framing-Geometry'])['width'] == 540


def test_framing_windows_fit_a_landscape_frame():
    from studio.framing import scene_shots, window
    assert window(1080, 1920, '4:5') == (0, 286, 1080, 1350)                         # reels unchanged
    assert window(1920, 1080, '4:5') == (528, 0, 864, 1080)
    assert window(1920, 1080, '1:1') == (420, 0, 1080, 1080)
    assert window(1920, 1080, '16:9') == (0, 0, 1920, 1080)
    scene = {'visual': 'stock', 'video_asset': 'a' * 32, 'extra_shots': ['b' * 32, 'c' * 32]}
    assert len(scene_shots(scene, 11.0, 6.0)) == 2 and len(scene_shots(scene, 11.0)) == 3   # YouTube: every ~6 s


def test_youtube_footage_prefers_landscape_clips_and_works_in_batches(monkeypatch):
    from studio import footage
    from studio.stock import best_file
    tall, wide = pixabay_hit(1, 'ocean, wave', 1080, 1920), pixabay_hit(2, 'ocean, wave', 1920, 1080)
    weight = footage.weights(['ocean wave'])
    assert footage.rank(tall, weight, 0) > footage.rank(wide, weight, 0)              # reel: vertical first
    assert footage.rank(wide, weight, 0, landscape=True) > footage.rank(tall, weight, 0, landscape=True)
    files = [{'url': 'u4k', 'width': 3840, 'height': 2160, 'size': 30_000_000}, {'url': 'u1080', 'width': 1920, 'height': 1080, 'size': 9_000_000}]
    assert (best_file(files)['url'], best_file(files, landscape=True)['url']) == ('u4k', 'u1080')

    calls = []

    def gemini(prompt, grounded=False, images=(), **options):
        calls.append(len(images))
        if not images:
            need = json.loads(prompt[prompt.index('NEED: ') + 6:])
            assert 'landscape 16:9 long-form YouTube video' in prompt and len(need) <= footage.PLAN_BATCH
            return json.dumps({'scenes': [{'index': i, 'shot': 'Ocean waves', 'queries': ['ocean wave']} for i in need]}), {}
        assert 'a landscape clip that fills a 16:9 frame' in prompt
        rows = {}
        for label, _, _ in images:
            scene = int(label.split('candidate')[0].split('Scene')[-1])
            rows.setdefault(scene, []).append({'candidate': len(rows.get(scene, [])) + 1, 'score': 8})
        return json.dumps({'scenes': [{'index': i, 'picks': p} for i, p in rows.items()]}), {}
    searches = iter(range(1000))          # every scene's search finds its own 20 clips
    monkeypatch.setattr(providers, 'stock_search', lambda query, provider='auto':
                        [pixabay_hit(1000 + 20 * n + k, 'ocean, wave', 1920, 1080) for n in [next(searches)] for k in range(20)])
    downloaded = []
    monkeypatch.setattr(providers, 'download_stock', lambda id, provider, landscape=False: downloaded.append(landscape) or {'id': f'{id:032x}'})
    monkeypatch.setattr(footage, 'preview', lambda hit: ('image/jpeg', b'frame'))
    monkeypatch.setattr(providers, 'gemini', gemini)
    body = project()
    body['format'] = 'youtube'
    body['scenes'] = [{**body['scenes'][0], 'id': f's{i}', 'visual': 'stock', 'roman': 'Samudram lo alalu', 'keywords': ''} for i in range(45)]
    steps = []
    selected = footage.find(body, body['scenes'][:18], steps.append)
    assert calls[0] == 0 and sorted(calls[1:]) == [6 * 2, 6 * 16]   # one plan request; pick: 16 and 2 scenes, six frames each
    assert len(selected) == 18 and all('asset' in s for s in selected) and set(downloaded) == {True}
    assert 'Checking candidate clips with AI (2 batches)' in steps
    calls.clear()
    footage.plan(body, list(range(45)))
    assert calls == [0, 0]                                 # 45 scenes: planned in two requests


def test_fill_scenes_completes_a_long_script_in_batches_with_the_whole_script(monkeypatch):
    calls = fake_gemini(monkeypatch)
    body = project()
    body['format'] = 'youtube'
    body['scenes'] = [{**body['scenes'][0], 'id': f's{i}', 'roman': f'Vakyam {i}', 'narration': '', 'keywords': '', 'graphic_title': ''}
                      for i in range(70)]
    scenes = providers.fill_scenes(Project.model_validate(body).model_dump())
    assert len(calls) == 3 and all('The whole script, for context only' in c and '"Vakyam 69"' in c for c in calls)
    assert [len(json.loads(c[c.index('Scenes: ') + 8:])) for c in calls] == [30, 30, 10]
    assert all(s['narration'] and s['keywords'] for s in scenes)


def test_stock_requests_are_paced_and_wait_out_a_rate_limit(monkeypatch):
    from studio import stock
    slept, clock = [], [100.0]
    monkeypatch.setattr(stock.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(stock.time, 'sleep', lambda s: (slept.append(round(s, 2)), clock.__setitem__(0, clock[0] + s)))
    stock._last_request.clear()
    answers = iter([ValueError('Provider returned HTTP 429: Too many requests'), 'ok'])

    def call():
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer
    assert stock.paced('pixabay', lambda: 'first') == 'first'
    assert stock.paced('pixabay', call) == 'ok'
    assert slept == [0.7, 20]
    with pytest.raises(ValueError, match='HTTP 401'):
        stock.paced('pixabay', lambda: (_ for _ in ()).throw(ValueError('Provider returned HTTP 401')))


def test_youtube_ai_draft_writes_a_long_form_script(monkeypatch):
    seen = {}

    def gemini(prompt, grounded=False, images=(), max_tokens=12000):
        seen.update(prompt=prompt, max_tokens=max_tokens)
        rows = [{'narration': 'దృశ్యం', 'roman': f'Drushyam {i}', 'keywords': 'ocean', 'graphic_title': 'Ocean'}
                for i in range(120 if max_tokens > 12000 else 5)]
        return json.dumps({'scenes': rows}), {}
    monkeypatch.setattr(providers, 'gemini', gemini)
    body = Project.model_validate({**project(), 'format': 'youtube', 'target_seconds': 1200, 'topic': 'Ocean secrets'}).model_dump()
    assert len(providers.draft(body)['scenes']) == 120
    assert seen['max_tokens'] == 60000 and 'Maximum 240 scenes' in seen['prompt'] and 'long-form YouTube video script' in seen['prompt']
    providers.draft(project())
    assert seen['max_tokens'] == 12000 and 'Maximum 20 scenes' in seen['prompt']


def test_large_youtube_project_saves_over_http():
    body = project()
    body['format'] = 'youtube'
    body['scenes'] = [{**body['scenes'][0], 'id': f's{i}', 'roman': 'Idi oka pedda vakyam ' * 60, 'narration': 'ఇది ' * 200}
                      for i in range(300)]
    payload = json.dumps(body, ensure_ascii=False)
    assert len(payload.encode()) > 1024 * 1024
    res = client().post('/api/projects', payload, content_type='application/json')
    assert res.status_code == 200, res.content[:200]
    assert len(res.json()['project']['scenes']) == 300


def test_video_format_switch_dom_behaviour():
    page = DATA.parent / 'format.html'
    page.write_bytes(client().get('/').content)
    subprocess.run(['node', 'tests/audit_format_ui.cjs', str(page)], check=True, timeout=60)


# Phones and other devices: password access ---------------------------------------------------

@pytest.fixture
def no_access_password(monkeypatch):
    monkeypatch.delenv('STUDIO_PASSWORD', raising=False)
    save_settings({'access_password_hash': ''})
    from studio import access
    access._failures.clear()
    yield
    save_settings({'access_password_hash': ''})


def phone(address='192.168.1.20', **extra):
    return Client(REMOTE_ADDR=address, **extra)


def test_other_devices_are_refused_until_a_password_is_set(no_access_password):
    page = phone().get('/')
    assert page.status_code == 403 and b'Access from other devices is off' in page.content
    assert phone().get('/api/bootstrap').status_code == 403
    assert phone().get('/static/js/app.js').status_code == 403
    assert phone().get('/static/app.css').status_code == 200                    # the sign-in page's style
    assert phone().get('/login').status_code == 403
    assert client().get('/').status_code == 200                                  # the studio PC needs no password
    assert client().get('/api/bootstrap').json()['access'] == {'local': True}
    # A loopback connection through a proxy is not the studio PC.
    assert Client(REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR='203.0.113.9').get('/api/bootstrap').status_code == 403


def test_other_devices_sign_in_with_the_studio_password(no_access_password):
    c = client()
    assert c.post('/api/settings', {'access_password': 'short'}, content_type='application/json').status_code == 400
    # The sign-in form must send its real origin (no-referrer would make browsers send "Origin: null").
    assert c.get('/login')['Referrer-Policy'] == 'same-origin' and c.get('/')['Referrer-Policy'] == 'no-referrer'
    assert phone().post('/login', {'password': 'x'}, HTTP_ORIGIN='https://evil.example').status_code == 403
    assert c.post('/api/settings', {'access_password_hash': 'x'}, content_type='application/json').status_code == 400
    saved = c.post('/api/settings', {'access_password': 'telugu-reels-2026'}, content_type='application/json')
    assert saved.status_code == 200, saved.content
    assert saved.json()['access_password_set'] is True and b'pbkdf2' not in saved.content
    assert 'access_password_hash' not in c.get('/api/bootstrap').json()['settings']

    device = phone()
    first = device.get('/?tab=export')
    assert first.status_code == 302 and first['Location'] == '/login?next=/?tab=export'
    assert device.get('/api/bootstrap').status_code == 401
    assert b'Sign in' in device.get('/login').content
    wrong = device.post('/login', {'password': 'telugu-reels-2025', 'next': '/'})
    assert wrong.status_code == 401 and b'not right' in wrong.content
    ok = device.post('/login', {'password': 'telugu-reels-2026', 'next': '//evil.example/'})
    assert ok.status_code == 302 and ok['Location'] == '/'                     # never redirects off the studio
    cookie = ok.cookies['studio_access']
    assert cookie['httponly'] and cookie['samesite'] == 'Lax' and int(cookie['max-age']) == 30 * 86400
    boot = device.get('/api/bootstrap')
    assert boot.status_code == 200 and boot.json()['access'] == {'local': False}
    token = boot.json()['token']
    created = device.post('/api/projects', project(), content_type='application/json', HTTP_X_STUDIO_TOKEN=token)
    assert created.status_code == 200, created.content                          # a signed-in phone can work

    forged = phone()
    forged.cookies['studio_access'] = cookie.value[:-3] + 'abc'
    assert forged.get('/api/bootstrap').status_code == 401
    # Changing the password signs every device out.
    assert c.post('/api/settings', {'access_password': 'another-password-1'}, content_type='application/json').status_code == 200
    assert device.get('/api/bootstrap').status_code == 401
    assert device.post('/login', {'password': 'another-password-1'}).status_code == 302
    out = device.get('/logout')
    assert out.status_code == 302 and out['Location'] == '/login' and device.get('/api/bootstrap').status_code == 401

    # Guessing is paused after ten wrong passwords from one address.
    guesser = phone('192.168.1.99')
    for _ in range(10):
        assert guesser.post('/login', {'password': 'guess-guess'}).status_code == 401
    assert guesser.post('/login', {'password': 'another-password-1'}).status_code == 429
    assert phone('192.168.1.50').post('/login', {'password': 'another-password-1'}).status_code == 302

    # Removing the password turns access from other devices off again.
    cleared = c.post('/api/settings', {'access_password_clear': True}, content_type='application/json')
    assert cleared.json()['access_password_set'] is False
    assert device.get('/').status_code == 403


def test_server_password_from_the_environment(no_access_password, monkeypatch):
    monkeypatch.setenv('STUDIO_PASSWORD', 'server-secret-99')
    device = phone('10.0.0.5')
    assert device.post('/login', {'password': 'server-secret-9'}).status_code == 401
    assert device.post('/login', {'password': 'server-secret-99'}).status_code == 302
    assert device.get('/api/bootstrap').status_code == 200
    assert client().get('/api/bootstrap').json()['settings']['access_password_from_server'] is True


def test_secret_key_is_not_the_old_public_default():
    assert settings.SECRET_KEY != 'django-insecure-studio-dev-key-7f2d37c50ff9429f0970aacf8f4aa068' and len(settings.SECRET_KEY) >= 40


# Key words in the highlight colour and automatic Telugu after splitting ---------------------------

def test_caption_key_words_are_highlighted_only_when_turned_on(tmp_path):
    import re
    scene = {'emphasis': ['Octopus', 'hearts'], 'keywords': 'octopus underwater'}
    on = {**project(), 'caption_emphasis': 'keywords'}
    words = media.emphasis_words(scene, on)
    assert media.ass_text('Octopus ki moodu hearts untayi 3 sarlu!', 32, words) == \
        r'{\1c&H00D7FF&}Octopus{\r} ki moodu {\1c&H00D7FF&}hearts{\r} untayi {\1c&H00D7FF&}3{\r}\Nsarlu!'
    # Tags never count towards the line width: the same words wrap exactly as plain captions do.
    plain = media.ass_text('Octopus ki moodu hearts untayi 3 sarlu!', 32)
    assert re.sub(r'\{[^}]*\}', '', media.ass_text('Octopus ki moodu hearts untayi 3 sarlu!', 32, words)) == plain
    # Without key words: the scene's stock subject words and numbers.
    assert media.emphasis_words({'keywords': 'blue whale ocean'}, on) == {'blue', 'whale', 'ocean'}
    assert media.emphasis_words(scene, project()) is None                      # older projects: plain captions
    # The whole subtitle file is unchanged for a project that does not highlight.
    before, after = tmp_path / 'before.ass', tmp_path / 'after.ass'
    cues = [{'start': 0, 'end': 1, 'text': 'Octopus ki moodu hearts'}]
    media.write_ass(before, cues, project())
    media.write_ass(after, cues, project(), emphasis=media.emphasis_words(scene, project()))
    assert before.read_bytes() == after.read_bytes() and b'1c&' not in before.read_bytes()
    yellow_style = {**on, 'caption_style': 'yellow_accent'}
    media.write_ass(after, cues, yellow_style, emphasis=words)
    assert r'{\1c&HFFFFFF&}Octopus{\r}' in after.read_text('utf-8')            # white on the all-yellow style


def test_fill_scenes_picks_key_words_from_the_line_without_extra_requests(monkeypatch):
    rows = [{'index': 0, 'narration': 'ఆక్టోపస్', 'roman': 'x', 'keywords': 'octopus underwater', 'graphic_title': 'Octopus',
             'emphasis': ['Octopus', 'ki', 'oka', 'HEARTS!', 'not-in-line', '3']}]
    calls = fake_gemini(monkeypatch, rows=rows)
    body = project()
    body['scenes'][0].update(roman='Oka Octopus ki 3 hearts untayi!', narration='', keywords='', graphic_title='')
    scene = providers.fill_scenes(body)[0]
    assert scene['emphasis'] == ['Octopus', 'hearts', '3']                       # from the line, small words left out
    # A complete scene without key words is not sent again just for them.
    assert providers.fill_scenes(Project.model_validate({**body, 'scenes': [{**scene, 'emphasis': []}]}).model_dump())
    assert len(calls) == 1
    with pytest.raises(ValueError, match='Key words'):
        Scene.model_validate({**scene, 'emphasis': ['x' * 41]})


def bottom_yellow_pixels(path, at=0.5):
    """Yellow pixels in the bottom third of the frame at `at` seconds."""
    seek = ['-ss', f'{at:.2f}'] if at else []          # seeking a still image returns no frame
    raw = subprocess.run([executable('ffmpeg'), '-v', 'error', *seek, '-i', str(path), '-frames:v', '1',
                          '-vf', 'crop=iw:ih/3:0:ih*2/3,scale=270:-2', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                         capture_output=True, check=True).stdout
    return sum(1 for k in range(0, len(raw), 3) if raw[k] > 200 and raw[k + 1] > 170 and raw[k + 2] < 90)


def test_highlighted_key_words_appear_yellow_in_renders_and_previews(tmp_path):
    c = client()
    cues = [{'start': 0, 'end': 1.9, 'text': 'Octopus ki moodu hearts'}]
    pid, body = framing_project([{'id': 'title', 'visual': 'graphic', 'graphic_title': 'Ocean', 'cues': cues,
                                  'emphasis': ['Octopus', 'hearts']}], tmp_path)
    counts = {}
    for mode in ('none', 'keywords'):
        row = c.get(f'/api/projects/{pid}').json()
        assert c.put(f'/api/projects/{pid}', {'revision': row['revision'], 'project': {**row['project'], 'caption_emphasis': mode}},
                     content_type='application/json').status_code == 200
        directory, _ = render_over_http(pid, 'draft')
        counts[mode] = bottom_yellow_pixels(directory / 'reel.mp4')
        preview = c.post(f'/api/projects/{pid}/caption-preview', {'scene': body['scenes'][0], 'caption': 'Octopus ki moodu hearts',
                                                                'quality': 'draft', 'caption_emphasis': mode}, content_type='application/json')
        assert preview.status_code == 200, preview.content
        image = tmp_path / f'preview-{mode}.jpg'
        image.write_bytes(preview.content)
        counts['preview-' + mode] = bottom_yellow_pixels(image, 0)
    assert counts['none'] == 0 and counts['keywords'] > 50, counts
    assert counts['preview-none'] == 0 and counts['preview-keywords'] > 50, counts


def test_auto_telugu_and_caption_highlight_dom_behaviour():
    page = DATA.parent / 'emphasis.html'
    page.write_bytes(client().get('/').content)
    subprocess.run(['node', 'tests/audit_emphasis_ui.cjs', str(page)], check=True, timeout=60)


def test_fill_scenes_treats_telugu_letters_in_the_roman_box_as_narration(monkeypatch):
    calls = fake_gemini(monkeypatch)
    body = project()
    body['scenes'][0].update(roman='', narration='', keywords='', graphic_title='')
    scene = dict(body['scenes'][0], roman='ఒక lake దగ్గరకు వెళ్లిన animals.')      # as an older split saved it
    filled = providers.fill_scenes({**body, 'scenes': [scene]})[0]
    assert filled['narration'] == 'ఒక lake దగ్గరకు వెళ్లిన animals.'                    # the writer's Telugu, unchanged
    assert filled['roman'] == 'Drushyam 1' and filled['narration_source_roman'] == ''  # Roman captions written from it
    assert '"roman": ""' in calls[0] and 'ఒక lake' in calls[0]


def test_folds_and_how_to_use_guide_dom_behaviour():
    page = DATA.parent / 'folds.html'
    page.write_bytes(client().get('/').content)
    subprocess.run(['node', 'tests/audit_folds_ui.cjs', str(page)], check=True, timeout=60)


# Faster video creation ---------------------------------------------------------------------------

def test_fast_gemini_turns_thinking_off_only_where_supported(monkeypatch):
    assert providers.thinking_off('gemini-2.5-flash') == {'thinkingBudget': 0}
    assert providers.thinking_off('gemini-3-flash-preview') == {'thinkingLevel': 'low'}
    assert providers.thinking_off('gemini-2.5-pro') is None
    sent = []

    class Response:
        def json(self):
            return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"ok": 1}'}]}}]}

    def request(method, url, **kw):
        sent.append((url.split('/models/')[1].split(':')[0], kw['json']['generationConfig'].get('thinkingConfig')))
        if kw['json']['generationConfig'].get('thinkingConfig', {}).get('thinkingLevel'):
            raise ValueError('Provider returned HTTP 400: Unknown name "thinkingLevel"')
        return Response()
    monkeypatch.setattr(providers, 'request', request)
    monkeypatch.setattr(providers, 'online', lambda: {'gemini_key': 'k', 'gemini_model': 'gemini-2.5-flash', 'daily_ai_calls': 100})
    providers.gemini('Hello', fast=True)
    providers.gemini('Hello')
    assert sent == [('gemini-2.5-flash', {'thinkingBudget': 0}), ('gemini-2.5-flash', None)]
    # A model that rejects the setting is asked again without it (no failure).
    sent.clear()
    monkeypatch.setattr(providers, 'online', lambda: {'gemini_key': 'k', 'gemini_model': 'gemini-3-flash-preview', 'daily_ai_calls': 100})
    assert providers.gemini('Hello', fast=True)[0] == '{"ok": 1}'
    assert sent == [('gemini-3-flash-preview', {'thinkingLevel': 'low'}), ('gemini-3-flash-preview', None)]


def test_narration_is_synthesized_for_several_scenes_at_once(monkeypatch):
    import threading
    active, peak, done = [0], [0], []
    lock = threading.Lock()

    def voice_for(scene, project, provider):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
            done.append(scene['id'])
        if scene['id'] == 's2':
            raise ValueError('Edge is busy')                      # left for the scene loop to report
        return Path('x.mp3'), []
    monkeypatch.setattr(media, 'voice_for', voice_for)
    body = project()
    body['scenes'] = [{**body['scenes'][0], 'id': f's{i}'} for i in range(8)]
    media.prefetch_voices(body, 'edge')
    assert sorted(done) == [f's{i}' for i in range(8)] and peak[0] == media.VOICE_WORKERS


def test_parallel_scene_rendering_keeps_scene_order(tmp_path):
    clips = {c: colour_clip(tmp_path, c) for c in ('red', 'green', 'blue')}
    scenes = [{'id': c, 'visual': 'stock', 'video_asset': clips[c], 'rights_reviewed': True} for c in ('red', 'green', 'blue')]
    pid, _ = framing_project(scenes, tmp_path, tone_seconds=2.0)
    from studio import tasks
    assert tasks.RENDER_WORKERS >= 1
    directory, manifest = render_over_http(pid, 'draft')
    reel = directory / 'reel.mp4'
    assert [centre_colour(reel, 1.0 + 2 * k) for k in range(3)] == ['r', 'g', 'b']
    report = json.loads((directory / 'source-report.json').read_text('utf-8'))
    assert [s['scene_id'] for s in report['scenes']] == ['red', 'green', 'blue']


def test_footage_downloads_run_in_parallel_and_keep_one_clip_per_scene(monkeypatch):
    import threading
    active, peak = [0], [0]
    lock = threading.Lock()

    def slow_download(id, provider, landscape=False):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        if id == 5:
            raise ValueError('Stock download returned HTTP 500')
        return {'id': f'{id:032x}', 'provider': provider}

    def gemini(prompt, grounded=False, images=(), **kw):
        raise ValueError('offline')
    footage, body, searched, downloaded = footage_fixture(monkeypatch, gemini)
    monkeypatch.setattr(providers, 'download_stock', slow_download)
    body['scenes'] = [{**body['scenes'][2], 'id': f'h{i}'} for i in range(2)] + [{**body['scenes'][0], 'id': 'title'}]
    selected = footage.find(body, body['scenes'])
    assert [s['scene_id'] for s in selected] == ['h0', 'h1', 'title']             # scene order kept
    assert selected[0]['asset']['id'] == f'{4:032x}'                              # h1 never gets h0's clip:
    assert selected[1]['error'] == 'Stock download returned HTTP 500'              # its own clip (5) failed to download
    assert selected[2]['asset']['id'] == f'{7:032x}'
    assert peak[0] > 1


def test_free_voice_is_recorded_while_footage_is_found(monkeypatch):
    from studio import footage, tasks
    recorded = []
    monkeypatch.setattr(media, 'prefetch_voices', lambda project, provider, workers=4: recorded.append(provider))
    monkeypatch.setattr(footage, 'find', lambda project, scenes, progress: [])
    for provider, options, expected in (('edge', {}, ['edge']), ('sarvam', {}, []), ('sarvam', {'prefetch_voice': True}, ['sarvam'])):
        recorded.clear()
        body = project()
        body.update(tts_provider=provider)
        body['scenes'][0].update(visual='upload', video_asset='')
        row = store.save_project(body)
        job = store.enqueue(row['id'], 1, 'stock_all', options)
        assert tasks.task_stock_all.run(job['id'])['status'] == 'succeeded'
        assert recorded == expected                                                # paid voices are never recorded early


def test_keys_from_the_env_file_are_used_and_never_shown(tmp_path):
    import os
    from studio import config
    # The test runner never loads the developer's real .env.
    assert not any(os.environ.get(name) for name in config.ENV_KEYS.values())
    env = tmp_path / '.env'
    env.write_text('# keys\nSTUDIO_GEMINI_KEY="from-the-file"\nSTUDIO_SARVAM_KEY=\nNOT_A_KEY\n', 'utf-8')
    save_settings({'gemini_key': 'typed-in-settings'})
    config.load_env_file(str(env))
    try:
        assert config.settings()['gemini_key'] == 'from-the-file'            # .env wins over Settings
        public = config.public_settings()
        assert public['gemini_key_from_env'] is True and public['sarvam_key_from_env'] is False
        assert 'from-the-file' not in json.dumps(public) and 'typed-in-settings' not in json.dumps(public)
    finally:
        os.environ.pop('STUDIO_GEMINI_KEY', None)
    assert config.settings()['gemini_key'] == 'typed-in-settings'


# Uploaded narration: one recording for the whole video, or one per scene -------------------------

import array


def tone_track(tmp_path, parts, name='voice.m4a', codec=('-c:a', 'aac', '-b:a', '128k')):
    """A recording of (frequency, seconds) parts, 0 Hz being a pause: speech with pauses, in miniature."""
    expr, t = [], 0.0
    for freq, seconds in parts:
        if freq:
            expr.append(f'0.4*sin(2*PI*{freq}*t)*between(t,{t:.3f},{t + seconds:.3f})')
        t += seconds
    path = tmp_path / name
    ffmpeg('-f', 'lavfi', '-i', "aevalsrc='" + '+'.join(expr) + f"':s=44100:d={t:.3f}", *codec, path)
    return path


def upload_narration(path):
    return client().post('/api/assets', {'file': SimpleUploadedFile(path.name, path.read_bytes()), 'purpose': 'narration'})


def pcm(path, at=None, seconds=None, rate=16000):
    args = [executable('ffmpeg'), '-v', 'error'] + (['-ss', f'{at:.3f}'] if at is not None else []) + ['-i', str(path)]
    args += (['-t', f'{seconds:.3f}'] if seconds else []) + ['-map', '0:a:0', '-ac', '1', '-ar', str(rate), '-f', 's16le', '-']
    samples = array.array('h')
    samples.frombytes(subprocess.run(args, capture_output=True, check=True).stdout)
    return samples


def pitch(path, at, seconds=0.25):
    """The tone's frequency around `at` seconds, from zero crossings."""
    s = pcm(path, at, seconds)
    return sum(1 for a, b in zip(s, s[1:]) if (a < 0) != (b < 0)) / 2 / seconds


SPEECH = [(300, 1.0), (0, 0.5), (500, 2.0), (0, 0.8), (700, 1.5)]   # pauses at 1.0-1.5 and 3.5-4.3


def test_narration_upload_is_checked_and_analysed(tmp_path):
    res = upload_narration(tone_track(tmp_path, SPEECH))
    assert res.status_code == 200, res.content
    aid = res.json()['id']
    assert res.json()['kind'] == 'audio'
    found = client().get(f'/api/assets/{aid}/narration').json()
    assert found['duration'] == pytest.approx(5.8, abs=0.03)          # measured from the decoded sound
    assert [[round(a, 1), round(b, 1)] for a, b in found['pauses'][:2]] == [[1.0, 1.5], [3.5, 4.3]]
    assert len(found['peaks']) == pytest.approx(5.8 * found['peaks_per_second'], abs=3)
    assert (found['sample_rate'], found['warnings']) == (44100, [])
    # A video, a picture, a file without sound and a damaged recording are refused with plain reasons.
    video = tmp_path / 'clip.mp4'
    ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=30:duration=1', '-f', 'lavfi', '-i', 'sine=duration=1',
           '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', video)
    assert 'video file' in upload_narration(video).json()['detail']
    silent = tmp_path / 'silent.mp4'
    ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=30:duration=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', silent)
    assert 'no sound' in upload_narration(silent).json()['detail']
    whole = tone_track(tmp_path, SPEECH, 'take.m4a').read_bytes()
    broken = tmp_path / 'broken.m4a'
    broken.write_bytes(whole[:len(whole) // 3])                       # half copied from the phone
    detail = upload_narration(broken).json()['detail']
    assert 'could not be read' in detail and str(DATA) not in detail
    picture = tmp_path / 'p.png'
    ffmpeg('-f', 'lavfi', '-i', 'color=c=red:s=16x16', '-frames:v', '1', picture)
    assert 'picture' in upload_narration(picture).json()['detail']


def test_narration_scenes_are_placed_at_pauses_and_slices_join_back_exactly(tmp_path):
    from studio import narration
    aid = upload_narration(tone_track(tmp_path, SPEECH)).json()['id']
    c = client()
    placed = c.post(f'/api/assets/{aid}/narration', {'action': 'place', 'texts': ['aaaa', 'aaaaaaaa', 'aaaaaa']},
                    content_type='application/json').json()['starts']
    assert placed[0] == 0 and placed[1:] == [pytest.approx(1.25, abs=0.03), pytest.approx(3.9, abs=0.03)]
    split = c.post(f'/api/assets/{aid}/narration', {'action': 'split', 'target': 2}, content_type='application/json').json()
    found = narration.analyze(aid)
    mids = [(a + b) / 2 for a, b in found['pauses']]
    assert split['starts'][0] == 0 and all(any(abs(s - m) < 0.03 for m in mids) for s in split['starts'][1:])
    assert c.post(f'/api/assets/{aid}/narration', {'action': 'nope'}, content_type='application/json').status_code == 400
    # Slices cut at any times meet exactly: joined in order they are the recording, sample for sample.
    cuts = [0, 0.7333, 2.12345, 4.9, None]
    whole = pcm(narration.cut(aid, 0, None), rate=48000)
    parts = array.array('h')
    for start, end in zip(cuts, cuts[1:]):
        parts.extend(pcm(narration.cut(aid, start, end), rate=48000))
    assert parts == whole
    assert narration.cut(aid, 0.5, 1.5) == narration.cut(aid, 0.5, 1.5)   # cached, not cut again


def recorded_project(aid, starts, visuals, **changes):
    body = project()
    base = body['scenes'][0]
    body.update(narration_mode='track', narration_track={'asset': aid, 'end': 0, 'gain_db': 0}, **changes)
    body['scenes'] = [{**base, 'id': f's{k}', 'narration': '', 'roman': f'Scene {k} words here', 'visual': 'stock',
                       'video_asset': v, 'rights_reviewed': True, 'track_start': start}
                      for k, (start, v) in enumerate(zip(starts, visuals))]
    return body


def test_whole_recording_plays_continuously_with_each_cut_in_sync(tmp_path):
    track = tone_track(tmp_path, [(300, 2.0), (0, 0.4), (600, 2.0), (0, 0.4), (900, 2.0)])
    aid = upload_narration(track).json()['id']
    red, green, blue = (colour_clip(tmp_path, c) for c in ('red', 'green', 'blue'))
    res = client().post('/api/projects', recorded_project(aid, [0, 2.2, 4.6], [red, green, blue]), content_type='application/json')
    assert res.status_code == 200, res.content
    pid = res.json()['id']
    report = store.preflight(res.json()['project'])
    assert report['ready'], report['errors']
    assert any('estimated from the recording' in w['message'] for w in report['warnings'])   # no Telugu text needed
    directory, manifest = render_over_http(pid, 'draft')
    reel = directory / 'reel.mp4'
    assert manifest['total_frames'] == 204                              # ceil(6.8 s x 30): nothing cut off
    assert [s['audio_offset'] for s in manifest['scenes']] == [0, pytest.approx(2.2, abs=1e-4), pytest.approx(4.6, abs=1e-4)]
    assert [s['start_frame'] for s in manifest['scenes']] == [0, 66, 138]
    assert [s['raw_voice_duration'] for s in manifest['scenes']] == [pytest.approx(d, abs=1e-4) for d in (2.2, 2.4, 2.2)]
    # Picture and sound change together at each cut, and the sound is the recording itself, uninterrupted.
    assert [centre_colour(reel, t) for t in (1.0, 2.15, 2.3, 3.4, 4.55, 4.7, 6.5)] == ['r', 'r', 'g', 'g', 'g', 'b', 'b']
    assert [round(pitch(reel, t), -2) for t in (0.8, 1.7, 2.5, 4.0, 5.0, 6.3)] == [300, 300, 600, 600, 900, 900]
    info = media.probe(reel)
    video, audio = (next(s for s in info['streams'] if s['codec_type'] == k) for k in ('video', 'audio'))
    assert (video['codec_name'], video['pix_fmt'], video['r_frame_rate'], audio['codec_name'], audio['sample_rate']) == \
        ('h264', 'yuv420p', '30/1', 'aac', '48000')
    assert abs(float(audio['duration']) - float(video['duration'])) < 0.05
    from studio import soundtrack
    assert soundtrack.moov_first(reel)
    source = json.loads((directory / 'source-report.json').read_text('utf-8'))
    assert (source['narration_mode'], source['speech_provider'], source['quality']['notes']) == ('track', 'recording', [])
    assert manifest['scenes'][0]['alignment'].startswith('estimated')


def test_scene_recording_trim_volume_and_text_edits(tmp_path):
    aid = upload_narration(tone_track(tmp_path, SPEECH, 'scene.wav', ('-c:a', 'pcm_s16le'))).json()['id']
    body = caption_project()
    body['scenes'][0].update(audio_asset=aid, audio_text_hash=store.digest(TELUGU_LINE), audio_trim_start=1.5,
                             audio_trim_end=3.5, audio_gain_db=-3)
    row = store.save_project(body)
    timing = caption_timing(row['id'])['scene1']
    assert timing['duration'] == pytest.approx(2.0, abs=1e-6)          # exactly the part used
    assert timing['audio_url'].startswith('/api/narration/audio/') and timing['speech'][0][0] == pytest.approx(0, abs=0.03)
    audio = client().get(timing['audio_url'], HTTP_RANGE='bytes=0-43')
    assert audio.status_code == 206
    audio.close()
    assert client().get('/api/narration/audio/' + 'f' * 40).status_code == 404
    assert client().get('/api/narration/audio/..%2F..%2Fsecret').status_code == 404
    # Editing the text keeps the recording and asks for a check; it never blocks the render.
    edited = copy.deepcopy(row['project'])
    edited['scenes'][0]['narration'] = 'వేరే మాటలు'
    report = store.preflight(edited)
    assert not any(e['field'] == 'audio_asset' for e in report['errors'])
    assert any('edited after its recording was attached' in w['message'] for w in report['warnings'])
    with pytest.raises(ValueError, match='0.3 seconds'):
        Scene.model_validate({'id': 's', 'audio_trim_start': 2, 'audio_trim_end': 2.1})


def test_recording_preflight_names_the_scene_to_fix(tmp_path):
    aid = upload_narration(tone_track(tmp_path, SPEECH)).json()['id']
    clip = colour_clip(tmp_path, 'red')
    body = recorded_project(aid, [0, None, 3.9], [clip] * 3)
    assert 'Scene 2 has no place in the recording' in store.preflight(body)['errors'][0]['message']
    body = recorded_project(aid, [0, 3.9, 1.25], [clip] * 3)
    assert 'Scene 3 must start' in store.preflight(body)['errors'][0]['message']
    body = recorded_project('', [0, 1.25, 3.9], [clip] * 3)
    assert 'Upload your narration recording' in store.preflight(body)['errors'][0]['message']
    body = recorded_project(aid, [0, 1.25, 3.9], [clip] * 3)
    body['scenes'][1]['audio_asset'] = aid
    report = store.preflight(body)
    assert report['ready'] and any('own recording is not used' in w['message'] for w in report['warnings'])


def test_changing_the_narration_audio_resets_only_the_captions_it_affects():
    cues = [{'start': 0.1, 'end': 0.9, 'text': 'Manual'}]
    old = {'narration_mode': 'track', 'narration_track': {'asset': 'a' * 32, 'end': 0, 'gain_db': 0},
           'scenes': [{'id': f's{k}', 'track_start': start, 'cues': list(cues)} for k, start in enumerate((0, 2, 4))]}
    moved = copy.deepcopy(old)
    moved['scenes'][1]['track_start'] = 2.5           # scene 1 now ends later, scene 2 starts later
    store.invalidate_stale_scene_fields(old, moved)
    assert [bool(s['cues']) for s in moved['scenes']] == [False, False, True]
    louder = copy.deepcopy(old)
    louder['narration_track']['gain_db'] = 3          # volume does not move any words
    store.invalidate_stale_scene_fields(old, louder)
    assert all(s['cues'] for s in louder['scenes'])
    replaced = copy.deepcopy(old)
    replaced['narration_track']['asset'] = 'b' * 32
    store.invalidate_stale_scene_fields(old, replaced)
    assert not any(s['cues'] for s in replaced['scenes'])
    trimmed = {'scenes': [{'id': 's', 'audio_asset': 'c' * 32, 'audio_trim_start': 0, 'cues': list(cues)}]}
    retrimmed = copy.deepcopy(trimmed)
    retrimmed['scenes'][0]['audio_trim_start'] = 0.4
    store.invalidate_stale_scene_fields(trimmed, retrimmed)
    assert retrimmed['scenes'][0]['cues'] == []


def test_continuous_narration_timeline_never_drifts():
    from studio.timeline import compute_timeline
    scenes = [{'id': f's{k}', 'hold_seconds': 0} for k in range(50)]
    voices = [{'scene_id': f's{k}', 'duration': 1.01, 'cues': [{'start': 0.0, 'end': 1.0, 'text': 'x'}]} for k in range(50)]
    timeline = compute_timeline({'scenes': scenes, 'narration_mode': 'track', 'format': 'youtube'}, voices)
    assert timeline.total_frames == 1515                          # ceil(50.5 s x 30), not 50 x ceil(30.3)
    for k, scene in enumerate(timeline.scenes):
        assert scene.audio_offset == pytest.approx(k * 1.01)
        assert abs(scene.start_frame / 30 - scene.audio_offset) <= 1 / 60 + 1e-9
        cue = timeline.all_cues[k]
        # A caption follows its sound, except that it cannot show before its scene's first frame (half a frame at most).
        assert scene.audio_offset - 1e-9 <= cue['start'] <= scene.audio_offset + 1 / 60 + 1e-9
    padded = compute_timeline({'scenes': scenes, 'format': 'youtube'}, voices)
    assert padded.total_frames == 50 * 31                         # scenes with their own audio: whole frames each


def test_clip_sound_is_mixed_only_when_turned_up(tmp_path):
    clip = tmp_path / 'beep.mp4'
    ffmpeg('-f', 'lavfi', '-i', 'color=c=blue:s=320x240:r=30:d=3', '-f', 'lavfi', '-i', 'sine=frequency=1000:duration=3',
           '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', clip)
    vid = upload_file(clip)['id']
    hush = tmp_path / 'hush.wav'                                      # a near-silent narration, so the clip is all you hear
    ffmpeg('-f', 'lavfi', '-i', 'anoisesrc=d=2:a=0.0005', hush)
    voice = upload_file(hush)['id']
    for volume, expect in ((0, False), (1, True)):
        body = project()
        body['scenes'][0].update(visual='upload', video_asset=vid, rights_reviewed=True, clip_volume=volume,
                                 audio_asset=voice, audio_text_hash=store.digest(body['scenes'][0]['narration']))
        pid = client().post('/api/projects', body, content_type='application/json').json()['id']
        directory, _ = render_over_http(pid, 'draft')
        loudest = max(map(abs, pcm(directory / 'reel.mp4', 0.5, 1.0)))
        assert (loudest > 3000) is expect
        if expect:
            assert round(pitch(directory / 'reel.mp4', 1.0), -2) == 1000
        source = json.loads((directory / 'source-report.json').read_text('utf-8'))
        assert source['scenes'][0]['clip_volume'] == volume and source['scenes'][0]['source_audio_muted'] is (volume == 0)


def test_music_mix_options():
    from studio import soundtrack
    p = {'music_gain': 0.1, 'music_ducking': True, 'music_fade': True}
    graph = soundtrack.mix_graph(p, {'music': True}, 30.0)
    assert 'sidechaincompress' in graph and 'afade=t=in' in graph and 'afade=t=out:st=28.000' in graph
    assert 'alimiter=limit=0.84' in graph and 'level=disabled' in graph
    plain = soundtrack.mix_graph({**p, 'music_ducking': False, 'music_fade': False}, {'music': True}, 30.0)
    assert 'sidechaincompress' not in plain and 'afade' not in plain and 'amix=inputs=2' in plain
    assert soundtrack.mix_graph(p, {}, 30.0) == '[1:a]alimiter=limit=0.84:attack=5:release=50:level=disabled:latency=1[a]'


def test_one_recording_cannot_be_spread_over_scenes(tmp_path):
    aid = upload_narration(tone_track(tmp_path, SPEECH)).json()['id']
    body = project()
    body['scenes'].append({**body['scenes'][0], 'id': 'scene2'})
    pid = client().post('/api/projects', body, content_type='application/json').json()['id']
    res = client().post(f'/api/projects/{pid}/assign-media', {'asset_id': aid, 'scene_ids': ['all']}, content_type='application/json')
    assert res.status_code == 400 and 'My recording, one file' in res.json()['detail']


def test_only_word_times_measured_by_the_voice_engine_are_used():
    words = [{'start': 0, 'end': 0.5, 'text': 'a'}]
    assert providers.measured_timings('edge', words) == words
    assert providers.measured_timings('sarvam', words) == []
    assert providers.measured_timings('azure', [{**words[0], 'estimated': True}]) == []
    cues, label = media.auto_captions('one two three four five six', 4.0, [], [[0, 1.0], [2.0, 4.0]])
    assert label == media.PAUSE_TIMING
    assert cues[1]['start'] >= 2.0                                     # the second cue waits for the speech after the pause
    assert media.auto_captions('one two', 2.0, [])[1] == media.LENGTH_TIMING


def test_narration_panel_dom_behaviour():
    page = DATA.parent / 'narration.html'
    page.write_bytes(client().get('/').content)
    subprocess.run(['node', 'tests/audit_narration_ui.cjs', str(page)], check=True, timeout=60)


def test_cancelling_a_job_stops_every_ffmpeg_it_runs():
    """A render encodes several scenes at once and the job records one process; each run stops itself."""
    import threading
    j = saved_job('render')
    results = []

    def encode():
        started = time.monotonic()
        try:
            media.run([executable('ffmpeg'), '-v', 'error', '-re', '-f', 'lavfi', '-i', 'testsrc=duration=30', '-f', 'null', '-'],
                      job_id=j['id'])
            results.append(('finished', time.monotonic() - started))
        except ValueError as exc:
            results.append((str(exc), time.monotonic() - started))
        finally:
            connections.close_all()
    threads = [threading.Thread(target=encode) for _ in range(2)]
    for thread in threads:
        thread.start()
    time.sleep(1.5)
    Job.objects.filter(id=j['id']).update(cancelled=True, state='cancelled')
    for thread in threads:
        thread.join(timeout=15)
    assert [r[0] for r in results] == ['The job was cancelled'] * 2
    assert all(seconds < 6 for _, seconds in results)          # not the 30 s the encodes would take


def test_media_errors_name_files_not_server_paths():
    message = media.short_paths('moov atom not found\nC:\\Users\\A B\\Studio\\data\\assets\\abc.mp4: Invalid data found')
    assert message == 'moov atom not found\nabc.mp4: Invalid data found'
    with pytest.raises(ValueError) as caught:
        media.run([executable('ffprobe'), '-v', 'error', str(DATA / 'assets' / 'missing-file.mp4')])
    assert 'missing-file.mp4' in str(caught.value) and str(DATA) not in str(caught.value)


# Reliability: unsaved edits, job results, retries and settings ------------------------------------

def test_autosaved_edits_come_back_with_the_revision_they_were_made_on():
    c = client()
    row = c.post('/api/projects', project(), content_type='application/json').json()
    edited = copy.deepcopy(row['project'])
    edited['scenes'][0]['roman'] = 'Unsaved words'
    assert c.put(f"/api/projects/{row['id']}/draft", {'body': edited, 'base_revision': row['revision']},
                 content_type='application/json').status_code == 200
    draft = c.get(f"/api/projects/{row['id']}/draft").json()
    assert draft['body']['scenes'][0]['roman'] == 'Unsaved words' and draft['base_revision'] == row['revision']
    assert draft['updated']
    # Drafts saved by earlier versions (the bare project) are still offered.
    c.put(f"/api/projects/{row['id']}/draft", edited, content_type='application/json')
    legacy = c.get(f"/api/projects/{row['id']}/draft").json()
    assert legacy['body']['scenes'][0]['roman'] == 'Unsaved words' and legacy['base_revision'] is None


def test_ai_fill_result_keeps_edits_saved_while_it_ran():
    body = project()
    body['scenes'][0].update(narration='', roman='Mana katha', keywords='')
    body['scenes'].append({**body['scenes'][0], 'id': 'scene2', 'roman': 'Rendo katha'})
    row = store.save_project(body)
    job = store.enqueue(row['id'], row['revision'], 'draft', {'fill': True})
    filled = copy.deepcopy(row['project']['scenes'])
    for scene in filled:
        scene.update(narration='తెలుగు ' + scene['roman'], keywords='story')
    filled[1]['roman'] = 'Rendo katha (AI)'
    store.progress(job['id'], 'Complete', 'succeeded', {'scenes': filled})
    # Meanwhile the user saves: a caption fix on scene 2, a trim on scene 1, and the scenes swapped.
    current = copy.deepcopy(row['project'])
    current['scenes'][1]['roman'] = 'Rendava katha'
    current['scenes'][0]['hold_seconds'] = 1.5
    current['scenes'].reverse()
    saved, _ = services.save_project_mutation(current, row['id'], row['revision'])
    result = services.apply_job_result(row['id'], job['id'])
    scenes = {s['id']: s for s in result['project']['project']['scenes']}
    assert [s['id'] for s in result['project']['project']['scenes']] == ['scene2', 'scene1']   # the order you saved
    assert scenes['scene1']['narration'] == 'తెలుగు Mana katha' and scenes['scene1']['hold_seconds'] == 1.5
    assert scenes['scene2']['roman'] == 'Rendava katha' and scenes['scene2']['keywords'] == 'story'
    assert [c['field'] for c in result['conflicts']] == ['roman']
    assert store.job(job['id'])['applied']


def test_footage_result_keeps_a_scene_you_made_a_title_card():
    body = project()
    body['scenes'][0].update(visual='upload', graphic_title='')
    row = store.save_project(body)
    job = store.enqueue(row['id'], row['revision'], 'stock_all')
    store.progress(job['id'], 'Complete', 'succeeded', {'selections': [{'scene_id': 'scene1', 'asset': {'id': 'c' * 32}}]})
    current = copy.deepcopy(row['project'])
    current['scenes'][0].update(visual='graphic', graphic_title='Title', video_asset='')
    services.save_project_mutation(current, row['id'], row['revision'])
    result = services.apply_job_result(row['id'], job['id'])
    assert result['project']['project']['scenes'][0]['visual'] == 'graphic'
    assert 'title card' in result['conflicts'][0]['reason']
    assert result['applied'] is False and not store.job(job['id'])['applied']   # it can still be applied on purpose
    forced = services.apply_job_result(row['id'], job['id'], force=True)
    assert forced['applied'] and forced['project']['project']['scenes'][0]['visual'] == 'stock'


def test_retry_respects_one_job_of_a_kind_at_a_time():
    failed = saved_job('research')
    store.progress(failed['id'], 'Gemini failed', 'failed')
    store.enqueue(failed['project_id'], 1, 'research')
    res = client().post(f"/api/jobs/{failed['id']}/retry", {'mode': 'original'}, content_type='application/json')
    assert res.status_code == 400 and 'already in progress' in res.json()['detail']


def test_settings_keep_env_keys_out_of_the_settings_file_and_need_real_ffmpeg(tmp_path):
    import os
    from studio import config
    os.environ['STUDIO_GEMINI_KEY'] = 'from-the-env-file'
    try:
        save_settings({'voice': 'te-IN-MohanNeural'})
        stored = json.loads((DATA / 'settings.json').read_text('utf-8'))
        assert stored['gemini_key'] != 'from-the-env-file'
        assert config.settings()['gemini_key'] == 'from-the-env-file'
    finally:
        os.environ.pop('STUDIO_GEMINI_KEY', None)
    fake = tmp_path / 'evil.exe'
    fake.write_bytes(b'x')
    for path in (str(fake), '\\\\server\\share\\ffmpeg.exe', str(tmp_path / 'ffmpeg.exe')):
        with pytest.raises(ValueError, match='full path of ffmpeg'):
            save_settings({'ffmpeg': path})
