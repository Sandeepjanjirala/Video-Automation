"""Current architecture regression suite; run with scripts/run_audit_tests.py."""
import copy
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


def test_upload_playback_thumbnail_and_multiscene_music_render(tmp_path):
    audio = tmp_path / 'narration.wav'
    video = tmp_path / 'footage.mp4'
    media.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=550:duration=1.3', audio])
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
    body.update(music_asset=a['id'], music_rights_reviewed=True)
    p = store.save_project(body)
    store.approve(p['id'], 1)
    j = store.enqueue(p['id'], 1, 'render', {'quality': 'draft'})
    assert task_render_pipeline.run(j['id'])['status'] == 'succeeded'
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
    response = client().get(f"/api/exports/{j['id']}/reel.mp4")
    assert response.status_code == 200
    response.close()
    cached = {f.name: f.stat().st_mtime_ns for f in (DATA / 'cache').glob('*.scene.mp4')}
    j2 = store.enqueue(p['id'], 1, 'render', {'quality': quality})
    assert task_render_pipeline.run(j2['id'])['status'] == 'succeeded'
    assert cached == {f.name: f.stat().st_mtime_ns for f in (DATA / 'cache').glob('*.scene.mp4')}
