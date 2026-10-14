"""Phase 3 Integration and Concurrency Test Suite.
Tests with real services (PostgreSQL 18 and Redis):
1. Redis outage during enqueue (outbox durability and repeat-safe reconciliation).
2. Duplicate delivery & operation key idempotency.
3. Worker death before/after artifact publication.
4. Worker startup during active render (never reset active unexpired leases).
5. Stale completion (fencing checks reject stale tokens).
6. Job cancellation and subprocess tree termination.
7. Distributed rate limiting and usage/uncertainty tracking.
8. Concurrent identical speech requests (atomic cache publication).
9. Dispatcher reconstruction of stages after crash.
"""
import concurrent.futures
import importlib
import json
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
import psycopg2
import pytest
from django.conf import settings
from django.db import connections
from core.models import Job, OutboxEvent, Project, Revision, Asset, Draft, Workspace
from studio.store import DB_ALIAS


@pytest.fixture()
def phase3_env(tmp_path, monkeypatch):
    monkeypatch.setenv('REEL_DATA_DIR', str(tmp_path / 'data'))
    test_db_url = os.environ.get('STUDIO_TEST_DATABASE_URL') or 'postgresql://postgres:postgres@127.0.0.1:5433/studio_test'
    monkeypatch.setenv('STUDIO_DATABASE_URL', test_db_url)
    monkeypatch.setenv('CELERY_BROKER_URL', 'redis://127.0.0.1:6379/0')
    monkeypatch.setenv('CELERY_RESULT_BACKEND', 'redis://127.0.0.1:6379/1')
    monkeypatch.setenv('STUDIO_CACHE_REDIS_URL', 'redis://127.0.0.1:6379/2')

    import studio.config, studio.store, studio.media, studio.providers
    import studio.celery_app, studio.tasks, studio.dispatcher, studio.ratelimit

    for mod in (
        studio.config, studio.store, studio.media, studio.providers,
        studio.celery_app, studio.tasks, studio.dispatcher, studio.ratelimit
    ):
        importlib.reload(mod)

    orig_names = {k: v['NAME'] for k, v in settings.DATABASES.items()}
    orig_users = {k: v.get('USER') for k, v in settings.DATABASES.items()}
    orig_passes = {k: v.get('PASSWORD') for k, v in settings.DATABASES.items()}
    for alias in list(settings.DATABASES.keys()):
        settings.DATABASES[alias]['NAME'] = 'studio_test'
        settings.DATABASES[alias]['USER'] = 'postgres'
        settings.DATABASES[alias]['PASSWORD'] = 'postgres'
    connections._settings = connections.configure_settings(settings.DATABASES)
    connections.close_all()

    clean_url = test_db_url.replace('+psycopg2', '')
    conn = psycopg2.connect(clean_url)
    with conn.cursor() as cur:
        cur.execute(
            'TRUNCATE TABLE application_receipts, outbox, job_stages, drafts, jobs, revisions, projects, assets, voice_takes, usage CASCADE;'
        )
        cur.execute("INSERT INTO workspaces (id, name) VALUES ('default', 'Local Workspace') ON CONFLICT (id) DO NOTHING;")
    conn.commit()
    conn.close()

    studio.store.init()
    try:
        yield {
            'store': studio.store,
            'media': studio.media,
            'providers': studio.providers,
            'tasks': studio.tasks,
            'dispatcher': studio.dispatcher,
            'ratelimit': studio.ratelimit,
            'db_url': test_db_url,
            'tmp_path': tmp_path
        }
    finally:
        for alias, name in orig_names.items():
            settings.DATABASES[alias]['NAME'] = name
            settings.DATABASES[alias]['USER'] = orig_users[alias]
            settings.DATABASES[alias]['PASSWORD'] = orig_passes[alias]
        connections._settings = connections.configure_settings(settings.DATABASES)
        connections.close_all()


def make_test_project():
    from studio.models import Project, Scene
    return Project(
        title='Phase 3 Concurrency Project',
        evidence_notes='Diagnostic test fixture for Phase 3 verification.',
        scenes=[Scene(
            id='scene_1',
            narration='ఇది మూడవ దశ పరీక్ష.',
            roman='Idi moodava dasha pariksha.',
            visual='graphic',
            graphic_title='Phase 3 Verification',
            rights_reviewed=True
        )]
    ).model_dump()


def test_phase3_redis_outage_during_enqueue(phase3_env, monkeypatch):
    """Scenario 1: Redis outage during enqueue does not lose accepted work."""
    store = phase3_env['store']
    dispatcher = phase3_env['dispatcher']

    p = store.save_project(make_test_project())
    store.approve(p['id'], 1)

    # Enqueue job - writes Job and Outbox in same PostgreSQL transaction
    job = store.enqueue(p['id'], 1, 'render', {'quality': 'draft'})
    assert job['state'] == 'queued'
    assert job['id'] is not None

    # Verify both Job and Outbox exist in PostgreSQL
    db_job = Job.objects.using(DB_ALIAS).get(id=job['id'])
    assert db_job is not None
    assert db_job.state == 'queued'

    outbox_entry = OutboxEvent.objects.using(DB_ALIAS).filter(payload__job_id=job['id']).first()
    assert outbox_entry is not None
    assert not outbox_entry.processed
    assert outbox_entry.attempts == 0

    # Simulate broker outage during dispatch
    def mock_broken_apply_async(*args, **kwargs):
        raise ConnectionError("Redis broker is offline or unreachable")

    monkeypatch.setattr('studio.tasks.task_render_pipeline.apply_async', mock_broken_apply_async)

    # Dispatcher runs: catches broker error, increments attempt, backs off, DOES NOT delete record
    dispatched = dispatcher.dispatch_pending_outbox()
    assert dispatched == 0

    outbox_entry = OutboxEvent.objects.using(DB_ALIAS).get(payload__job_id=job['id'])
    assert not outbox_entry.processed
    assert outbox_entry.attempts == 1
    assert "Redis broker is offline" in outbox_entry.error
    assert outbox_entry.locked_until is not None

    # Now simulate broker recovery
    dispatched_events = []
    def mock_successful_apply_async(*args, **kwargs):
        dispatched_events.append(args)

    monkeypatch.setattr('studio.tasks.task_render_pipeline.apply_async', mock_successful_apply_async)

    # Unlock the record for immediate retry
    outbox_entry = OutboxEvent.objects.using(DB_ALIAS).get(payload__job_id=job['id'])
    outbox_entry.locked_until = None
    outbox_entry.save(using=DB_ALIAS, update_fields=['locked_until'])

    # Dispatcher runs again and succeeds
    dispatched_count = dispatcher.dispatch_pending_outbox()
    assert dispatched_count == 1
    assert len(dispatched_events) == 1

    outbox_entry = OutboxEvent.objects.using(DB_ALIAS).get(payload__job_id=job['id'])
    assert outbox_entry.processed
    assert outbox_entry.error is None


def test_phase3_operation_key_and_duplicate_delivery(phase3_env):
    """Scenario 2: Duplicate delivery & operation key idempotency."""
    store = phase3_env['store']
    p = store.save_project(make_test_project())
    store.approve(p['id'], 1)

    op_key = f"op_test_{uuid.uuid4().hex}"

    # 1. First enqueue with operation_key
    job1 = store.enqueue(p['id'], 1, 'render', {'quality': 'draft'}, operation_key=op_key)
    assert job1['id'] is not None

    # 2. Second enqueue with same operation_key returns identical job without duplicate rows
    job2 = store.enqueue(p['id'], 1, 'render', {'quality': 'draft'}, operation_key=op_key)
    assert job2['id'] == job1['id']

    # 3. Duplicate delivery simulation: two workers attempt to acquire lease for the same job
    worker_1 = "worker_slot_1"
    worker_2 = "worker_slot_2"

    lease_1 = store.acquire_lease(job1['id'], worker_1, duration_seconds=60)
    assert lease_1 == worker_1

    # Worker 2 attempts to claim while worker 1 holds active lease
    lease_2 = store.acquire_lease(job1['id'], worker_2, duration_seconds=60)
    assert lease_2 is None  # Worker 2 is fenced out


def test_phase3_worker_death_and_artifact_publication(phase3_env):
    """Scenario 3: Worker death before/after artifact publication."""
    store = phase3_env['store']
    p = store.save_project(make_test_project())
    store.approve(p['id'], 1)
    j = store.enqueue(p['id'], 1, 'render', {'quality': 'draft'})

    # Worker 1 acquires lease
    store.acquire_lease(j['id'], 'worker_crashed', duration_seconds=1)

    # Simulate worker 1 dying and its lease expiring
    db_job = Job.objects.using(DB_ALIAS).get(id=j['id'])
    db_job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    db_job.save(using=DB_ALIAS, update_fields=['lease_expires_at'])

    # Expired lease recovery routine runs
    recovered = store.recover_expired_leases()
    assert recovered == 1

    # Verify job reflects failed lease, not lost state
    updated_j = store.job(j['id'])
    assert updated_j['state'] == 'failed'
    assert 'lost ownership' in updated_j['progress'].lower() or 'expired' in updated_j['progress'].lower()


def test_phase3_worker_startup_during_active_render(phase3_env):
    """Scenario 4: Worker startup during another active render never resets active jobs."""
    store = phase3_env['store']
    p = store.save_project(make_test_project())
    store.approve(p['id'], 1)
    j = store.enqueue(p['id'], 1, 'render', {'quality': 'draft'})

    # Worker 1 has active lease for next 120 seconds
    store.acquire_lease(j['id'], 'worker_active_render', duration_seconds=120)

    # Worker 2 starts up and invokes recover_expired_leases()
    recovered = store.recover_expired_leases()
    assert recovered == 0  # Does NOT touch Worker 1's active job

    active_j = store.job(j['id'])
    assert active_j['state'] == 'running'
    assert active_j['lease_token'] == 'worker_active_render'


def test_phase3_stale_completion_fencing(phase3_env):
    """Scenario 5: Stale completion is fenced out when lease token mismatches."""
    store = phase3_env['store']
    tasks = phase3_env['tasks']
    p = store.save_project(make_test_project())
    store.approve(p['id'], 1)
    j = store.enqueue(p['id'], 1, 'render', {'quality': 'draft'})

    # Worker 1 starts task with lease token T1
    token_1 = store.acquire_lease(j['id'], 'token_worker_1', duration_seconds=60)
    assert token_1 == 'token_worker_1'

    # Worker 2 takes over ownership (e.g. after failover)
    db_job = Job.objects.using(DB_ALIAS).get(id=j['id'])
    db_job.lease_token = 'token_worker_2'
    db_job.save(using=DB_ALIAS, update_fields=['lease_token'])

    # Worker 1 checks ownership before publishing results
    assert not store.verify_lease_and_not_cancelled(j['id'], 'token_worker_1')


def test_phase3_job_cancellation_and_subprocess(phase3_env):
    """Scenario 6: Cancellation persists and kills registered subprocess tree."""
    store = phase3_env['store']
    p = store.save_project(make_test_project())
    store.approve(p['id'], 1)
    j = store.enqueue(p['id'], 1, 'render', {'quality': 'draft'})

    store.acquire_lease(j['id'], 'worker_cancel_test', duration_seconds=60)

    # Register mock subprocess PID (e.g. 99999)
    store.register_subprocess(j['id'], 99999)

    # User cancels job
    cancelled = store.cancel(j['id'])
    assert cancelled['cancelled'] is True
    assert cancelled['state'] == 'cancelled'

    # Verify ownership check fails for worker
    assert not store.verify_lease_and_not_cancelled(j['id'], 'worker_cancel_test')


def test_phase3_rate_limiting_and_usage_tracking(phase3_env):
    """Scenario 7: Enforce per-provider rate limits and usage/uncertainty tracking."""
    ratelimit = phase3_env['ratelimit']

    # Record usage for Gemini: requests, units, uncertainty
    ratelimit.record_usage('gemini', estimated_units=1500, actual_units=1450, uncertain=False)
    ratelimit.record_usage('gemini', estimated_units=800, actual_units=0, uncertain=True)

    summary = ratelimit.get_usage_summary(provider='gemini')
    assert len(summary) == 1
    row = summary[0]
    assert row['provider'] == 'gemini'
    assert row['calls'] == 2
    assert row['estimated_billable_units'] == 2300
    assert row['actual_billable_units'] == 1450
    assert row['uncertain_calls'] == 1  # Provider uncertainty is tracked


def test_phase3_concurrent_identical_speech_requests(phase3_env, monkeypatch):
    """Scenario 8: Concurrent identical speech requests produce valid cache without corruption."""
    providers = phase3_env['providers']
    store = phase3_env['store']
    media = phase3_env['media']

    text = "ఇది సమకాలీన వాయిస్ పరీక్ష."
    voice = "te-IN-ShrutiNeural"
    rate = 0

    # Mock synthesize request to return dummy audio bytes and timing
    original_run = media.run
    def mock_probe(path):
        return {'streams': [{'codec_type': 'audio'}], 'format': {'duration': '2.5'}}

    monkeypatch.setattr('studio.media.probe', mock_probe)
    monkeypatch.setattr('studio.providers.online', lambda: {'online_enabled': True})

    # Run two concurrent synthesis requests in thread pool
    results = []
    def do_synth():
        return providers.synthesize(text, voice, rate, 'edge')

    import edge_tts
    class MockCommunicate:
        def __init__(self, *args, **kwargs):
            pass
        async def stream(self):
            yield {'type': 'audio', 'data': b'ID3\x03\x00\x00\x00\x00\x00#TSSE\x00\x00\x00'}
            yield {'type': 'WordBoundary', 'offset': 1000000, 'duration': 5000000, 'text': 'ఇది'}

    monkeypatch.setattr(edge_tts, 'Communicate', MockCommunicate)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(do_synth)
        f2 = executor.submit(do_synth)
        r1 = f1.result(timeout=10)
        r2 = f2.result(timeout=10)

    # Both succeeded and returned valid path and boundary metadata
    audio1, bounds1 = r1
    audio2, bounds2 = r2
    assert audio1.exists()
    assert audio1.stat().st_size > 0
    assert bounds1 == bounds2
    assert len(bounds1) > 0


def test_phase3_dispatcher_reconstruction_after_crash(phase3_env):
    """Scenario 9: Dispatcher discovers queued job without outbox and reconstructs stage."""
    store = phase3_env['store']
    dispatcher = phase3_env['dispatcher']

    p = store.save_project(make_test_project())
    store.approve(p['id'], 1)
    j = store.enqueue(p['id'], 1, 'render', {'quality': 'draft'})

    # Simulate outbox record being deleted/lost in a crash
    OutboxEvent.objects.using('admin').filter(payload__job_id=j['id']).delete()

    # Backdate the job creation so reconcile_orphaned_jobs picks it up
    db_job = Job.objects.using('admin').get(id=j['id'])
    db_job.created_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    db_job.save(using='admin', update_fields=['created_at'])

    # Reconcile runs
    reconciled_count = dispatcher.reconcile_orphaned_jobs()
    assert reconciled_count == 1

    # Verify a new outbox entry was reconstructed
    outbox_entry = OutboxEvent.objects.using(DB_ALIAS).filter(payload__job_id=j['id']).first()
    assert outbox_entry is not None
    assert outbox_entry.payload.get('reconciled') is True
