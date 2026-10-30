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
    monkeypatch.setattr(lab, 'get_cached_synthesis', lambda *a: None)
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
