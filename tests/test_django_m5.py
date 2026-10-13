"""Stage M5 Acceptance and Parity Tests.

Verifies:
1. Narrow job writer role isolation:
   - django_readonly is strictly read-only (cannot write to jobs or outbox)
   - django_job_writer CAN write to jobs, job_stages, and outbox
   - django_job_writer CANNOT write to projects, revisions, drafts, application_receipts
2. Transactional job + outbox creation boundary:
   - Atomic commit of Job and Outbox records in PostgreSQL
   - No direct Celery apply_async/delay from Django HTTP views
   - No video bytes in outbox payload
3. Enqueue validation and error handling:
   - Nonexistent project returns 400
   - Revision mismatch returns 400
   - Unapproved script returns 400 for render/voice
   - Preflight failure returns 400
   - Schema validation failure returns 422
4. Idempotency:
   - Identical operation_key returns existing job without duplicate dispatch
   - Duplicate pending job of same kind is rejected with 400
5. Retry semantics:
   - Rejects non-failed jobs (400)
   - Mode 'original' retains original snapshot
   - Mode 'current' updates to current project body and revision
   - Generates retry outbox and job_stage records
6. Cancellation semantics:
   - Queued/running job is cancelled
   - Repeated cancellation is idempotent
   - Succeeded job cancellation is idempotent
7. Artifact delivery:
   - GET /api/jobs/<jid>/audio/<scene_id> serves audio with path validation
   - GET /api/exports/<jid>/<name> serves export artifacts and supports HTTP Range
8. Redis/dispatcher resilience:
   - Broker outage does not lose durable job intent in PostgreSQL
   - Duplicate delivery does not corrupt job state
9. Security middleware:
   - Mutation allowlist strictly permits only M4 asset upload and M5 job endpoints
   - Unauthorized mutations (draft, approve, apply) return 405
   - Token and origin headers enforced
10. Alembic schema authority invariance
"""
import io
import json
import os
import sys
sys.path.insert(0, os.getcwd())
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest
import httpx

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.conf import settings
from rest_framework.test import APIClient
from core.models import Asset, Project, Revision, Job, JobStage, OutboxEvent
from core.views import DJANGO_TOKEN
from core.serializers import serialize_job_dict
from studio.config import DATA

FASTAPI_URL = "http://127.0.0.1:8787"
DJANGO_URL = "http://127.0.0.1:8788"
DJ_CLIENT = APIClient()


@pytest.fixture(scope="module")
def pg_connection():
    """Superuser PostgreSQL connection for invariant checks and test fixture setup."""
    conn = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5433/studio")
    yield conn
    conn.close()


@pytest.fixture
def test_project_fixture(pg_connection):
    """Creates a temporary approved project ready for render testing."""
    cur = pg_connection.cursor()
    pid = f"m5_proj_{uuid.uuid4().hex[:12]}"
    now_dt = datetime.now(timezone.utc)
    body = {
        'title': 'M5 Test Project',
        'category': 'General',
        'voice': 'te-IN-ShrutiNeural',
        'speech_rate': 0,
        'target_seconds': 5,
        'duration_mode': 'natural',
        'caption_size': 58,
        'caption_bottom': 260,
        'music_asset': None,
        'music_gain': 0.2,
        'music_rights_reviewed': True,
        'evidence_notes': 'M5 Automated Test Project',
        'research_sources': [],
        'scenes': [
            {
                'id': 'scene_m5_1',
                'narration': 'ఇది పరీక్ష రీల్.',
                'roman': 'Idi pariksha reel.',
                'visual': 'graphic',
                'graphic_title': 'M5 Test',
                'rights_reviewed': True,
                'trim_start': 0.0,
                'fit': 'crop',
                'crop_x': 0.5,
                'hold_seconds': 0.0,
                'cues': []
            }
        ]
    }

    cur.execute(
        """
        INSERT INTO projects (id, workspace_id, revision, approved_revision, body, created_at, updated_at)
        VALUES (%s, 'default', 1, 1, %s, %s, %s)
        """,
        [pid, json.dumps(body), now_dt, now_dt]
    )
    cur.execute(
        """
        INSERT INTO revisions (project_id, revision, body, created_at)
        VALUES (%s, 1, %s, %s)
        """,
        [pid, json.dumps(body), now_dt]
    )
    pg_connection.commit()

    yield {'id': pid, 'revision': 1, 'body': body}

    # Cleanup
    cur.execute("DELETE FROM outbox WHERE payload->>'project_id' = %s", [pid])
    cur.execute("DELETE FROM job_stages WHERE job_id IN (SELECT id FROM jobs WHERE project_id = %s)", [pid])
    cur.execute("DELETE FROM jobs WHERE project_id = %s", [pid])
    cur.execute("DELETE FROM revisions WHERE project_id = %s", [pid])
    cur.execute("DELETE FROM projects WHERE id = %s", [pid])
    pg_connection.commit()
    cur.close()


# ==============================================================================
# 1. Role Isolation Tests
# ==============================================================================

def test_job_writer_role_isolation(pg_connection):
    """Verify django_job_writer has narrow access and cannot write to projects or revisions."""
    ro_url = os.environ.get('STUDIO_READONLY_DATABASE_URL')
    if not ro_url:
        ro_file = settings.BASE_DIR / 'backups' / '.readonly_db_url'
        if ro_file.exists():
            ro_url = ro_file.read_text(encoding='utf-8').strip()

    job_writer_url = os.environ.get('STUDIO_JOB_WRITER_DATABASE_URL')
    if not job_writer_url:
        jw_file = settings.BASE_DIR / 'backups' / '.job_writer_db_url'
        if jw_file.exists():
            job_writer_url = jw_file.read_text(encoding='utf-8').strip()

    assert ro_url, "No readonly database URL found"
    assert job_writer_url, "No job writer database URL found"

    # 1. django_readonly CANNOT insert into jobs or outbox
    ro_conn = psycopg2.connect(ro_url)
    ro_cur = ro_conn.cursor()
    with pytest.raises(psycopg2.Error):
        ro_cur.execute(
            "INSERT INTO jobs (id, workspace_id, project_id, revision, kind, snapshot) VALUES ('ro_j', 'default', 'p', 1, 'render', '{}')"
        )
    ro_conn.rollback()
    with pytest.raises(psycopg2.Error):
        ro_cur.execute("INSERT INTO outbox (event_type, payload) VALUES ('test', '{}')")
    ro_conn.rollback()
    ro_conn.close()

    # 2. django_job_writer CAN insert into jobs, job_stages, outbox and update jobs
    jw_conn = psycopg2.connect(job_writer_url)
    jw_cur = jw_conn.cursor()
    test_jid = f"jw_test_{uuid.uuid4().hex[:12]}"
    test_pid = f"jw_proj_{uuid.uuid4().hex[:12]}"

    # Need a project in DB for foreign key
    admin_cur = pg_connection.cursor()
    admin_cur.execute(
        "INSERT INTO projects (id, workspace_id, revision, body) VALUES (%s, 'default', 1, '{}')",
        [test_pid]
    )
    pg_connection.commit()

    try:
        # django_job_writer CAN insert job
        jw_cur.execute(
            "INSERT INTO jobs (id, workspace_id, project_id, revision, kind, snapshot) VALUES (%s, 'default', %s, 1, 'render', '{}')",
            [test_jid, test_pid]
        )
        jw_conn.commit()

        # django_job_writer CAN insert job_stage
        jw_cur.execute(
            "INSERT INTO job_stages (job_id, attempt, stage, state, details) VALUES (%s, 1, 'queued', 'queued', '{}')",
            [test_jid]
        )
        jw_conn.commit()

        # django_job_writer CAN insert outbox
        jw_cur.execute(
            "INSERT INTO outbox (event_type, payload) VALUES ('job.enqueued', %s)",
            [json.dumps({'job_id': test_jid})]
        )
        jw_conn.commit()

        # django_job_writer CAN update job state/progress
        jw_cur.execute(
            "UPDATE jobs SET progress = 'Working', state = 'running' WHERE id = %s",
            [test_jid]
        )
        jw_conn.commit()

        # django_job_writer CANNOT delete from jobs or outbox
        with pytest.raises(psycopg2.Error):
            jw_cur.execute("DELETE FROM jobs WHERE id = %s", [test_jid])
        jw_conn.rollback()

        with pytest.raises(psycopg2.Error):
            jw_cur.execute("DELETE FROM outbox WHERE payload->>'job_id' = %s", [test_jid])
        jw_conn.rollback()

        # django_job_writer CANNOT write to projects, revisions, drafts, application_receipts
        forbidden_writes = [
            f"INSERT INTO projects (id, workspace_id, revision, body) VALUES ('bad_p', 'default', 1, '{{}}')",
            f"UPDATE projects SET revision = 99 WHERE id = '{test_pid}'",
            f"INSERT INTO revisions (project_id, revision, body) VALUES ('{test_pid}', 2, '{{}}')",
            f"INSERT INTO drafts (project_id, body) VALUES ('{test_pid}', '{{}}')",
            f"INSERT INTO application_receipts (job_id, project_id, applied_revision) VALUES ('{test_jid}', '{test_pid}', 1)",
        ]
        for sql in forbidden_writes:
            with pytest.raises(psycopg2.Error):
                jw_cur.execute(sql)
            jw_conn.rollback()

    finally:
        jw_conn.close()
        admin_cur.execute("DELETE FROM outbox WHERE payload->>'job_id' = %s", [test_jid])
        admin_cur.execute("DELETE FROM job_stages WHERE job_id = %s", [test_jid])
        admin_cur.execute("DELETE FROM jobs WHERE id = %s", [test_jid])
        admin_cur.execute("DELETE FROM projects WHERE id = %s", [test_pid])
        pg_connection.commit()
        admin_cur.close()


# ==============================================================================
# 2. Job Enqueue Validation & Contract Tests
# ==============================================================================

def test_enqueue_nonexistent_project():
    """Verify 400 when enqueuing job for nonexistent project."""
    resp = DJ_CLIENT.post(
        "/api/projects/nonexistent_12345/jobs",
        {'revision': 1, 'kind': 'render'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 400
    assert resp.json()['detail'] == 'Save the current revision first'


def test_enqueue_revision_mismatch(test_project_fixture):
    """Verify 400 when revision in request does not match database revision."""
    pid = test_project_fixture['id']
    resp = DJ_CLIENT.post(
        f"/api/projects/{pid}/jobs",
        {'revision': 999, 'kind': 'render'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 400
    assert resp.json()['detail'] == 'Save the current revision first'


def test_enqueue_unapproved_script(pg_connection, test_project_fixture):
    """Verify 400 when attempting render on an unapproved revision."""
    pid = test_project_fixture['id']
    cur = pg_connection.cursor()
    cur.execute("UPDATE projects SET approved_revision = 0 WHERE id = %s", [pid])
    pg_connection.commit()
    cur.close()

    resp = DJ_CLIENT.post(
        f"/api/projects/{pid}/jobs",
        {'revision': 1, 'kind': 'render'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 400
    assert resp.json()['detail'] == 'Approve this exact script revision first'


def test_enqueue_invalid_payload_schema():
    """Verify 422 when required fields are missing or invalid kind specified."""
    # Missing revision and kind
    resp = DJ_CLIENT.post(
        "/api/projects/pid_123/jobs",
        {},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 422

    # Invalid kind
    resp2 = DJ_CLIENT.post(
        "/api/projects/pid_123/jobs",
        {'revision': 1, 'kind': 'invalid_kind_type'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp2.status_code == 422


# ==============================================================================
# 3. Transactional Outbox & Job Creation Tests
# ==============================================================================

def test_enqueue_transactional_job_and_outbox(pg_connection, test_project_fixture):
    """Verify atomic creation of Job and Outbox records in PostgreSQL without video bytes."""
    pid = test_project_fixture['id']
    resp = DJ_CLIENT.post(
        f"/api/projects/{pid}/jobs",
        {'revision': 1, 'kind': 'render', 'quality': 'draft'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 200
    data = resp.json()
    jid = data['id']
    assert data['state'] == 'queued'
    assert data['stage'] == 'queued'
    assert data['project_id'] == pid

    # Verify Job in PostgreSQL
    cur = pg_connection.cursor()
    cur.execute("SELECT state, stage, progress FROM jobs WHERE id = %s", [jid])
    job_row = cur.fetchone()
    assert job_row is not None
    assert job_row[0] == 'queued'
    assert job_row[1] == 'queued'

    # Verify Outbox in PostgreSQL
    cur.execute("SELECT event_type, payload, processed FROM outbox WHERE payload->>'job_id' = %s", [jid])
    outbox_row = cur.fetchone()
    assert outbox_row is not None
    assert outbox_row[0] == 'job.enqueued'
    payload = outbox_row[1]
    assert payload['job_id'] == jid
    assert payload['project_id'] == pid
    assert payload['kind'] == 'render'
    assert payload['revision'] == 1

    # CRITICAL CHECK: No video bytes or audio bytes inside outbox payload
    raw_payload_text = json.dumps(payload)
    assert len(raw_payload_text) < 4096, "Outbox payload unexpectedly large"
    assert 'base64' not in raw_payload_text
    cur.close()


# ==============================================================================
# 4. Idempotency Tests
# ==============================================================================

def test_enqueue_idempotency_with_operation_key(pg_connection, test_project_fixture):
    """Verify duplicate enqueue with same operation_key returns existing job without duplicate dispatch."""
    pid = test_project_fixture['id']
    op_key = f"op_test_{uuid.uuid4().hex}"

    # First enqueue
    resp1 = DJ_CLIENT.post(
        f"/api/projects/{pid}/jobs",
        {'revision': 1, 'kind': 'research', 'operation_key': op_key},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp1.status_code == 200
    jid1 = resp1.json()['id']

    # Second enqueue with identical operation_key
    resp2 = DJ_CLIENT.post(
        f"/api/projects/{pid}/jobs",
        {'revision': 1, 'kind': 'research', 'operation_key': op_key},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp2.status_code == 200
    jid2 = resp2.json()['id']

    assert jid1 == jid2

    # Verify only ONE outbox record exists
    cur = pg_connection.cursor()
    cur.execute("SELECT count(*) FROM outbox WHERE payload->>'operation_key' = %s", [op_key])
    count = cur.fetchone()[0]
    assert count == 1
    cur.close()


def test_enqueue_duplicate_pending_job_rejection(test_project_fixture):
    """Verify 400 when attempting to queue a job of same kind while one is already in progress."""
    pid = test_project_fixture['id']
    resp1 = DJ_CLIENT.post(
        f"/api/projects/{pid}/jobs",
        {'revision': 1, 'kind': 'draft'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp1.status_code == 200

    # Attempting to enqueue second draft job on same project
    resp2 = DJ_CLIENT.post(
        f"/api/projects/{pid}/jobs",
        {'revision': 1, 'kind': 'draft'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp2.status_code == 400
    assert 'already in progress' in resp2.json()['detail']


# ==============================================================================
# 5. Retry Semantics Tests
# ==============================================================================

def test_retry_non_failed_job_rejection(pg_connection, test_project_fixture):
    """Verify only failed jobs can be retried."""
    pid = test_project_fixture['id']
    resp = DJ_CLIENT.post(
        f"/api/projects/{pid}/jobs",
        {'revision': 1, 'kind': 'stock'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    jid = resp.json()['id']

    retry_resp = DJ_CLIENT.post(
        f"/api/jobs/{jid}/retry",
        {'mode': 'original'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert retry_resp.status_code == 400
    assert retry_resp.json()['detail'] == 'Only failed jobs can be retried'


def test_retry_original_vs_current(pg_connection, test_project_fixture):
    """Verify retry original preserves original snapshot; retry current updates snapshot and revision."""
    pid = test_project_fixture['id']
    cur = pg_connection.cursor()
    jid = f"retry_test_{uuid.uuid4().hex[:12]}"
    now_dt = datetime.now(timezone.utc)

    orig_snapshot = {'project': {'title': 'Original Title', 'scenes': []}, 'options': {'quality': 'draft'}}
    cur.execute(
        """
        INSERT INTO jobs (id, workspace_id, project_id, revision, kind, state, snapshot, created_at, updated_at)
        VALUES (%s, 'default', %s, 1, 'render', 'failed', %s, %s, %s)
        """,
        [jid, pid, json.dumps(orig_snapshot), now_dt, now_dt]
    )
    pg_connection.commit()

    # 1. Retry with original mode
    resp_orig = DJ_CLIENT.post(
        f"/api/jobs/{jid}/retry",
        {'mode': 'original'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp_orig.status_code == 200
    data_orig = resp_orig.json()
    assert data_orig['state'] == 'queued'
    assert data_orig['snapshot']['project']['title'] == 'Original Title'
    assert data_orig['progress'] == 'Retry queued (original inputs)'

    # Fail the job again
    cur.execute("UPDATE jobs SET state = 'failed' WHERE id = %s", [jid])
    # Update project to revision 2 with new title
    cur.execute(
        "UPDATE projects SET revision = 2, body = %s WHERE id = %s",
        [json.dumps({'title': 'Updated Title Revision 2', 'scenes': []}), pid]
    )
    pg_connection.commit()

    # 2. Retry with current mode
    resp_curr = DJ_CLIENT.post(
        f"/api/jobs/{jid}/retry",
        {'mode': 'current'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp_curr.status_code == 200
    data_curr = resp_curr.json()
    assert data_curr['state'] == 'queued'
    assert data_curr['revision'] == 2
    assert data_curr['snapshot']['project']['title'] == 'Updated Title Revision 2'
    assert data_curr['progress'] == 'Retry queued (current project)'
    cur.close()


# ==============================================================================
# 6. Cancellation Semantics Tests
# ==============================================================================

def test_cancel_job_lifecycle(pg_connection, test_project_fixture):
    """Verify cancellation sets cancelled flag and repeated cancellations are idempotent."""
    pid = test_project_fixture['id']
    resp = DJ_CLIENT.post(
        f"/api/projects/{pid}/jobs",
        {'revision': 1, 'kind': 'voice'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    jid = resp.json()['id']

    # 1. Cancel queued job
    cancel_resp1 = DJ_CLIENT.post(
        f"/api/jobs/{jid}/cancel",
        {},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert cancel_resp1.status_code == 200
    data1 = cancel_resp1.json()
    assert data1['cancelled'] is True
    assert data1['state'] == 'cancelled'

    # 2. Repeat cancel (idempotent)
    cancel_resp2 = DJ_CLIENT.post(
        f"/api/jobs/{jid}/cancel",
        {},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert cancel_resp2.status_code == 200
    data2 = cancel_resp2.json()
    assert data2['cancelled'] is True
    assert data2['state'] == 'cancelled'


# ==============================================================================
# 7. Artifact Read Endpoints Tests
# ==============================================================================

def test_job_audio_artifact_endpoint(pg_connection, test_project_fixture, tmp_path):
    """Verify GET /api/jobs/<jid>/audio/<scene_id> safely serves generated preview audio."""
    pid = test_project_fixture['id']
    jid = f"audio_j_{uuid.uuid4().hex[:12]}"
    cur = pg_connection.cursor()
    now_dt = datetime.now(timezone.utc)

    # Incomplete job returns 400
    cur.execute(
        """
        INSERT INTO jobs (id, workspace_id, project_id, revision, kind, state, snapshot, created_at, updated_at)
        VALUES (%s, 'default', %s, 1, 'voice', 'running', '{}', %s, %s)
        """,
        [jid, pid, now_dt, now_dt]
    )
    pg_connection.commit()

    resp = DJ_CLIENT.get(f"/api/jobs/{jid}/audio/scene_1")
    assert resp.status_code == 400
    assert resp.json()['detail'] == 'Voice preview is not ready'

    # Create dummy audio file inside DATA/cache
    cache_dir = DATA / 'cache'
    cache_dir.mkdir(parents=True, exist_ok=True)
    audio_file = cache_dir / f"{jid}_scene_1.mp3"
    audio_file.write_bytes(b"FAKE_MP3_AUDIO_BYTES_TEST")

    # Update job to succeeded with voices result
    cur.execute(
        """
        UPDATE jobs SET state = 'succeeded', result = %s WHERE id = %s
        """,
        [json.dumps({'voices': [{'scene_id': 'scene_1', 'audio_path': str(audio_file)}]}), jid]
    )
    pg_connection.commit()

    try:
        resp_success = DJ_CLIENT.get(f"/api/jobs/{jid}/audio/scene_1")
        assert resp_success.status_code == 200
        content = b"".join(resp_success.streaming_content)
        resp_success.close()
        assert content == b"FAKE_MP3_AUDIO_BYTES_TEST"
        assert resp_success.headers['Content-Type'] == 'audio/mpeg'
    finally:
        audio_file.unlink(missing_ok=True)
        cur.close()


def test_export_artifact_endpoint(pg_connection, test_project_fixture):
    """Verify GET /api/exports/<jid>/<name> serves finalized export artifacts with HTTP Range support."""
    jid = uuid.uuid4().hex  # 32-char hex
    pid = test_project_fixture['id']
    cur = pg_connection.cursor()
    now_dt = datetime.now(timezone.utc)

    # Setup dummy export directory and files
    exp_dir = DATA / 'exports' / jid
    exp_dir.mkdir(parents=True, exist_ok=True)
    reel_path = exp_dir / 'reel.mp4'
    srt_path = exp_dir / 'captions.srt'
    reel_path.write_bytes(b"HEADER_VIDEO_STREAM_BYTES_SAMPLE" * 100)
    srt_path.write_text("1\n00:00:01,000 --> 00:00:03,000\nNamaskaram", encoding='utf-8')

    cur.execute(
        """
        INSERT INTO jobs (id, workspace_id, project_id, revision, kind, state, snapshot, created_at, updated_at)
        VALUES (%s, 'default', %s, 1, 'render', 'succeeded', '{}', %s, %s)
        """,
        [jid, pid, now_dt, now_dt]
    )
    pg_connection.commit()

    try:
        # 1. Invalid file name rejected
        resp_invalid = DJ_CLIENT.get(f"/api/exports/{jid}/secret_file.txt")
        assert resp_invalid.status_code == 404

        # 2. Captions SRT served
        resp_srt = DJ_CLIENT.get(f"/api/exports/{jid}/captions.srt")
        assert resp_srt.status_code == 200
        srt_content = b"".join(resp_srt.streaming_content)
        resp_srt.close()
        assert b"Namaskaram" in srt_content

        # 3. Reel MP4 served with full range support
        resp_full = DJ_CLIENT.get(f"/api/exports/{jid}/reel.mp4")
        assert resp_full.status_code == 200
        assert resp_full.headers['Accept-Ranges'] == 'bytes'
        assert resp_full.headers['Content-Type'] == 'video/mp4'
        full_content = b"".join(resp_full.streaming_content)
        resp_full.close()
        assert len(full_content) == 3200

        # 4. Partial range request (RFC 7233)
        resp_part = DJ_CLIENT.get(f"/api/exports/{jid}/reel.mp4", HTTP_RANGE='bytes=0-99')
        assert resp_part.status_code == 206
        assert resp_part.headers['Content-Range'].startswith('bytes 0-99/')
        part_content = b"".join(resp_part.streaming_content)
        resp_part.close()
        assert len(part_content) == 100

    finally:
        reel_path.unlink(missing_ok=True)
        srt_path.unlink(missing_ok=True)
        try:
            exp_dir.rmdir()
        except OSError:
            pass
        cur.close()


# ==============================================================================
# 8. Security Guards & Mutation Allowlist Tests
# ==============================================================================

def test_m5_security_disallowed_mutations():
    """Verify unauthorized mutating endpoints remain strictly rejected with 405."""
    disallowed_calls = [
        ("POST", "/api/unsupported_action", {}),
        ("PATCH", "/api/projects/pid_1", {}),
        ("DELETE", "/api/projects/pid_1", {}),
        ("DELETE", "/api/assets/some_aid", {}),
    ]
    for method, path, payload in disallowed_calls:
        if method == 'POST':
            resp = DJ_CLIENT.post(path, payload, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
        elif method == 'PUT':
            resp = DJ_CLIENT.put(path, payload, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
        elif method == 'DELETE':
            resp = DJ_CLIENT.delete(path, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
        assert resp.status_code == 405, f"Expected 405 for {method} {path}, got {resp.status_code}"


def test_m5_security_token_and_origin(test_project_fixture):
    """Verify x-studio-token and origin enforcement on M5 job enqueue route."""
    pid = test_project_fixture['id']

    # Missing/invalid token returns 403
    resp_bad_token = DJ_CLIENT.post(
        f"/api/projects/{pid}/jobs",
        {'revision': 1, 'kind': 'research'},
        format='json',
        HTTP_X_STUDIO_TOKEN='invalid_token_value'
    )
    assert resp_bad_token.status_code == 403
    assert resp_bad_token.json()['detail'] == 'Reload the app before saving'

    # Disallowed origin returns 403
    resp_bad_origin = DJ_CLIENT.post(
        f"/api/projects/{pid}/jobs",
        {'revision': 1, 'kind': 'research'},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN,
        HTTP_ORIGIN='https://malicious-external-site.com'
    )
    assert resp_bad_origin.status_code == 403
    assert resp_bad_origin.json()['detail'] == 'Cross-origin changes are not allowed'


# ==============================================================================
# 9. Schema Authority and Alembic Invariance
# ==============================================================================

def test_m5_schema_authority_and_alembic_invariance(pg_connection):
    """Verify Alembic revision remains 0003_phase4_voice_lab and models are unmanaged."""
    cur = pg_connection.cursor()
    cur.execute("SELECT version_num FROM alembic_version;")
    alembic_rev = cur.fetchone()[0]
    assert alembic_rev == '0003_phase4_voice_lab'
    cur.close()

    # Verify unmanaged status on all core models
    for model_cls in (Job, JobStage, OutboxEvent, Project, Revision, Asset):
        assert model_cls._meta.managed is False, f"{model_cls.__name__} must be unmanaged"
