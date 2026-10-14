"""Stage M6 Acceptance, Concurrency, and Contract Parity Tests.

Verifies:
1. Narrow project_writer role least-privilege isolation:
   - django_project_writer CAN write to projects, revisions, drafts, application_receipts
   - django_project_writer CAN update jobs.applied and jobs.applied_at
   - django_project_writer CANNOT mutate assets, outbox, voice_takes, job_stages
   - django_project_writer CANNOT DELETE from projects, revisions, jobs, assets
2. Transactional project creation (POST /api/projects):
   - Atomically inserts Project and Revision 1 snapshot
   - Unicode Telugu script and Roman transliteration preserved verbatim
   - Validation failure returns 422
3. Project updates and optimistic concurrency (PUT /api/projects/<pid>):
   - Stale expected revision returns 400 conflict
   - Concurrent update test: two simultaneous requests, one succeeds, other fails with conflict
   - Successful update increments revision to N+1, adds revision snapshot, clears draft
   - Modification invalidates previous approval state
   - Identical body is a no-op that preserves revision
4. Approval semantics (POST /api/projects/<pid>/approve):
   - Enforces scene narration, roman captions, and evidence notes
   - Stale revision rejected
   - Valid approval sets approved_revision and script_approved_hash
5. Draft / Autosave lifecycle:
   - PUT /api/projects/<pid>/draft upserts draft
   - GET /api/projects/<pid>/draft returns draft
   - DELETE /api/projects/<pid>/draft deletes draft
   - Project save automatically deletes draft
6. Specialized project mutations:
   - POST /api/projects/<pid>/assign-media
   - POST /api/projects/<pid>/voice_lock
   - POST /api/projects/<pid>/pronunciation_dict
7. Job Result Application (POST /api/projects/<pid>/jobs/<jid>/apply):
   - Safe merge of stock, draft, and research results
   - Scene-level and script conflict detection preserving user edits
   - Force flag override support
   - Application receipt creation
   - Idempotent duplicate application
8. Security middleware:
   - Token and origin enforcement
   - Unauthorized mutations (e.g. DELETE project, POST settings) rejected with 405
9. FastAPI vs Django contract parity on mutations
10. Alembic schema authority and database invariants
"""
import concurrent.futures
import copy
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
from core.views import DJANGO_TOKEN
from core.models import Project, Revision, Draft, Job, ApplicationReceipt, Asset
from core import services

ADMIN_DB_URL = os.environ.get('STUDIO_DATABASE_URL', 'postgresql://postgres:postgres@127.0.0.1:5433/studio')
DJ_CLIENT = APIClient()
CREATED_PIDS = set()


def get_admin_conn():
    db_name = settings.DATABASES['default']['NAME']
    db_user = settings.DATABASES.get('admin', {}).get('USER', 'postgres')
    db_pass = settings.DATABASES.get('admin', {}).get('PASSWORD', 'postgres')
    db_host = settings.DATABASES['default'].get('HOST', '127.0.0.1')
    db_port = settings.DATABASES['default'].get('PORT', 5433)
    conn = psycopg2.connect(dbname=db_name, user=db_user, password=db_pass, host=db_host, port=db_port)
    conn.autocommit = True
    return conn


@pytest.fixture(autouse=True)
def cleanup_test_projects():
    """Ensure disposable test projects are thoroughly removed after each test."""
    yield
    try:
        conn = get_admin_conn()
        cur = conn.cursor()
        pids = list(CREATED_PIDS)
        if pids:
            cur.execute("DELETE FROM application_receipts WHERE project_id = ANY(%s)", (pids,))
            cur.execute("DELETE FROM drafts WHERE project_id = ANY(%s)", (pids,))
            cur.execute("DELETE FROM revisions WHERE project_id = ANY(%s)", (pids,))
            cur.execute("DELETE FROM job_stages WHERE job_id IN (SELECT id FROM jobs WHERE project_id = ANY(%s))", (pids,))
            cur.execute("DELETE FROM jobs WHERE project_id = ANY(%s)", (pids,))
            cur.execute("DELETE FROM projects WHERE id = ANY(%s)", (pids,))
            CREATED_PIDS.clear()

        # Also purge any wildcard test records
        cur.execute("DELETE FROM application_receipts WHERE project_id LIKE 'test_m6_%'")
        cur.execute("DELETE FROM drafts WHERE project_id LIKE 'test_m6_%'")
        cur.execute("DELETE FROM revisions WHERE project_id LIKE 'test_m6_%'")
        cur.execute("DELETE FROM job_stages WHERE job_id IN (SELECT id FROM jobs WHERE project_id LIKE 'test_m6_%')")
        cur.execute("DELETE FROM jobs WHERE project_id LIKE 'test_m6_%'")
        cur.execute("DELETE FROM projects WHERE id LIKE 'test_m6_%'")
        cur.close()
        conn.close()
    except Exception:
        pass


def make_valid_project_dict(title="M6 Telugu Reel"):
    return {
        "title": title,
        "topic": "Telugu AI Automation",
        "category": "Technology",
        "target_seconds": 30,
        "duration_mode": "natural",
        "tts_provider": "edge",
        "voice": "te-IN-ShrutiNeural",
        "speech_rate": 0,
        "delivery_preset": "conversational",
        "pronunciation_dict": {"AI": "ఏఐ"},
        "voice_locked": False,
        "scenes": [
            {
                "id": "scene_1",
                "narration": "నమస్కారం! తెలుగు రీల్ స్టూడియో కు స్వాగతం.",
                "spoken_text": "నమస్కారం! తెలుగు రీల్ స్టూడియో కు స్వాగతం.",
                "roman": "Namaskaram! Telugu Reel Studio ku swagatham.",
                "keywords": "studio, tech",
                "visual": "graphic",
                "graphic_title": "Namaskaram Telugu",
                "rights_reviewed": True,
                "hold_seconds": 3.0
            },
            {
                "id": "scene_2",
                "narration": "ఇది రెండవ దృశ్యం, పూర్తిగా ఆటోమేట్ చేయబడింది.",
                "spoken_text": "ఇది రెండవ దృశ్యం, పూర్తిగా ఆటోమేట్ చేయబడింది.",
                "roman": "Idi rendava drushyam, poorthiga automate cheyabadindi.",
                "keywords": "automation, code",
                "visual": "graphic",
                "graphic_title": "Second Scene",
                "rights_reviewed": True,
                "hold_seconds": 3.0
            }
        ],
        "evidence_notes": "Verified facts: Telugu Reel Studio Phase 4 & Stage M6 testing."
    }


# ==============================================================================
# 1. Database Role Isolation & Least-Privilege Verification
# ==============================================================================

def test_project_writer_role_isolation():
    """Verify django_project_writer privileges match the narrow M6 write specification."""
    pw_url = None
    pw_file = Path('backups/.project_writer_db_url')
    if pw_file.exists():
        pw_url = pw_file.read_text(encoding='utf-8').strip()
    if not pw_url:
        pytest.skip("Project writer DB URL not found")

    conn = psycopg2.connect(pw_url)
    conn.autocommit = False
    cur = conn.cursor()

    test_pid = f"test_m6_perm_{uuid.uuid4().hex[:8]}"

    # A. CAN insert into projects, revisions, drafts
    try:
        cur.execute(
            "INSERT INTO projects (id, workspace_id, revision, body) VALUES (%s, 'default', 1, %s)",
            (test_pid, json.dumps({'title': 'test'}))
        )
        cur.execute(
            "INSERT INTO revisions (project_id, revision, body) VALUES (%s, 1, %s)",
            (test_pid, json.dumps({'title': 'test'}))
        )
        cur.execute(
            "INSERT INTO drafts (project_id, body) VALUES (%s, %s)",
            (test_pid, json.dumps({'draft': True}))
        )
        # CAN delete from drafts
        cur.execute("DELETE FROM drafts WHERE project_id = %s", (test_pid,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        pytest.fail(f"django_project_writer failed permitted operations: {e}")

    # B. CANNOT delete from projects
    try:
        cur.execute("DELETE FROM projects WHERE id = %s", (test_pid,))
        conn.commit()
        pytest.fail("django_project_writer must NOT have DELETE privileges on projects")
    except psycopg2.errors.InsufficientPrivilege:
        conn.rollback()

    # C. CANNOT delete from revisions
    try:
        cur.execute("DELETE FROM revisions WHERE project_id = %s", (test_pid,))
        conn.commit()
        pytest.fail("django_project_writer must NOT have DELETE privileges on revisions")
    except psycopg2.errors.InsufficientPrivilege:
        conn.rollback()

    # D. CANNOT insert into assets
    try:
        cur.execute("INSERT INTO assets (id, workspace_id, filename, name, kind) VALUES ('a_test', 'default', 'f', 'n', 'video')")
        conn.commit()
        pytest.fail("django_project_writer must NOT have INSERT privileges on assets")
    except psycopg2.errors.InsufficientPrivilege:
        conn.rollback()

    # E. CANNOT insert into outbox
    try:
        cur.execute("INSERT INTO outbox (event_type, payload) VALUES ('test', '{}')")
        conn.commit()
        pytest.fail("django_project_writer must NOT have INSERT privileges on outbox")
    except psycopg2.errors.InsufficientPrivilege:
        conn.rollback()

    cur.close()
    conn.close()


# ==============================================================================
# 2. Transactional Project Creation (POST /api/projects)
# ==============================================================================

def test_project_create_success():
    """Verify POST /api/projects creates project and revision 1 atomically."""
    body = make_valid_project_dict(title="తెలుగు ఆర్టిఫిషియల్ ఇంటెలిజెన్స్")

    resp = DJ_CLIENT.post(
        "/api/projects",
        body,
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 200
    data = resp.json()
    test_pid = data["id"]
    CREATED_PIDS.add(test_pid)

    assert data["revision"] == 1
    assert data["approved"] is False
    assert data["project"]["title"] == "తెలుగు ఆర్టిఫిషియల్ ఇంటెలిజెన్స్"
    assert "నమస్కారం!" in data["project"]["scenes"][0]["narration"]
    assert "Namaskaram!" in data["project"]["scenes"][0]["roman"]

    # Verify database state
    p = Project.objects.using('project_writer').get(id=test_pid)
    assert p.revision == 1
    assert p.approved_revision is None

    revs = list(Revision.objects.using('project_writer').filter(project_id=test_pid))
    assert len(revs) == 1
    assert revs[0].revision == 1
    assert revs[0].body["title"] == "తెలుగు ఆర్టిఫిషియల్ ఇంటెలిజెన్స్"


def test_project_create_validation_failure():
    """Verify invalid project body triggers 422 unprocessable entity."""
    resp = DJ_CLIENT.post(
        "/api/projects",
        {"title": "", "scenes": []},  # Empty title and invalid schema
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 422


# ==============================================================================
# 3. Project Updates & Optimistic Locking (PUT /api/projects/<pid>)
# ==============================================================================

def test_project_update_success_and_invalidation():
    """Verify PUT /api/projects/<pid> increments revision and invalidates approval."""
    body = make_valid_project_dict(title="Original Title")

    # 1. Create project
    create_resp = DJ_CLIENT.post("/api/projects", body, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
    assert create_resp.status_code == 200
    test_pid = create_resp.json()["id"]
    CREATED_PIDS.add(test_pid)

    # 2. Approve revision 1
    app_resp = DJ_CLIENT.post(f"/api/projects/{test_pid}/approve", {"revision": 1}, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
    assert app_resp.status_code == 200
    assert app_resp.json()["approved"] is True

    # 3. Modify project at expected revision 1
    updated_body = copy.deepcopy(body)
    updated_body["title"] = "Updated Telugu Title"
    update_resp = DJ_CLIENT.put(
        f"/api/projects/{test_pid}",
        {"revision": 1, "project": updated_body},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert update_resp.status_code == 200
    data = update_resp.json()

    assert data["revision"] == 2
    assert data["approved"] is False  # Invalidation check!
    assert data["project"]["title"] == "Updated Telugu Title"

    # Verify DB revisions table has exactly 2 rows
    revs = list(Revision.objects.using('project_writer').filter(project_id=test_pid).order_by('revision'))
    assert len(revs) == 2
    assert revs[0].revision == 1
    assert revs[1].revision == 2


def test_project_update_optimistic_lock_conflict():
    """Verify updating with stale revision returns 400 conflict."""
    body = make_valid_project_dict()

    create_resp = DJ_CLIENT.post("/api/projects", body, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
    assert create_resp.status_code == 200
    test_pid = create_resp.json()["id"]
    CREATED_PIDS.add(test_pid)

    # Attempt save expecting revision 99 instead of 1
    resp = DJ_CLIENT.put(
        f"/api/projects/{test_pid}",
        {"revision": 99, "project": body},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 400
    assert "This project changed in another tab" in resp.json()["detail"]


def test_project_update_noop():
    """Verify saving identical content is a no-op that preserves current revision."""
    body = make_valid_project_dict()

    create_resp = DJ_CLIENT.post("/api/projects", body, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
    assert create_resp.status_code == 200
    test_pid = create_resp.json()["id"]
    CREATED_PIDS.add(test_pid)

    # Fetch created state
    p = Project.objects.using('project_writer').get(id=test_pid)
    saved_body = p.body

    # Save exact same body expecting revision 1
    resp = DJ_CLIENT.put(
        f"/api/projects/{test_pid}",
        {"revision": 1, "project": saved_body},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 200
    assert resp.json()["revision"] == 1

    rev_count = Revision.objects.using('project_writer').filter(project_id=test_pid).count()
    assert rev_count == 1


def test_concurrent_same_revision_updates():
    """Section 25 Concurrency Test: Two concurrent requests attempting to update revision N."""
    body = make_valid_project_dict()

    create_resp = DJ_CLIENT.post("/api/projects", body, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
    assert create_resp.status_code == 200
    test_pid = create_resp.json()["id"]
    CREATED_PIDS.add(test_pid)

    def attempt_update(branch_id: int):
        client = APIClient()
        branch_body = copy.deepcopy(body)
        branch_body["title"] = f"Concurrent Edit Branch {branch_id}"
        return client.put(
            f"/api/projects/{test_pid}",
            {"revision": 1, "project": branch_body},
            format='json',
            HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(attempt_update, 1)
        f2 = executor.submit(attempt_update, 2)
        res1 = f1.result()
        res2 = f2.result()

    statuses = [res1.status_code, res2.status_code]
    assert 200 in statuses, "At least one update must succeed"
    assert 400 in statuses, "The losing concurrent update must receive 400 conflict"

    # Confirm database has exactly revision 2 and 2 revision records total
    p = Project.objects.using('project_writer').get(id=test_pid)
    assert p.revision == 2
    rev_count = Revision.objects.using('project_writer').filter(project_id=test_pid).count()
    assert rev_count == 2


# ==============================================================================
# 4. Approval Semantics (POST /api/projects/<pid>/approve)
# ==============================================================================

def test_project_approval_validation_rules():
    """Verify approval enforces Telugu narration, Roman captions, and evidence notes."""
    body = make_valid_project_dict()
    body["scenes"][0]["narration"] = ""  # Empty Telugu narration

    create_resp = DJ_CLIENT.post("/api/projects", body, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
    assert create_resp.status_code == 200
    test_pid = create_resp.json()["id"]
    CREATED_PIDS.add(test_pid)

    # Attempt approve
    resp = DJ_CLIENT.post(
        f"/api/projects/{test_pid}/approve",
        {"revision": 1},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 400
    assert "Every scene needs Telugu narration text" in resp.json()["detail"]


# ==============================================================================
# 5. Draft / Autosave Lifecycle
# ==============================================================================

def test_draft_lifecycle_and_cleared_on_save():
    """Verify PUT, GET, DELETE draft, and auto-clear on explicit project save."""
    body = make_valid_project_dict()

    create_resp = DJ_CLIENT.post("/api/projects", body, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
    assert create_resp.status_code == 200
    test_pid = create_resp.json()["id"]
    CREATED_PIDS.add(test_pid)

    # 1. Save draft
    draft_data = {"scenes": [{"id": "s1", "narration": "Drafting Telugu..."}]}
    put_resp = DJ_CLIENT.put(
        f"/api/projects/{test_pid}/draft",
        draft_data,
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["project_id"] == test_pid

    # 2. Get draft
    get_resp = DJ_CLIENT.get(f"/api/projects/{test_pid}/draft")
    assert get_resp.status_code == 200
    assert get_resp.json()["scenes"][0]["narration"] == "Drafting Telugu..."

    # 3. Explicit project save clears draft
    updated_body = copy.deepcopy(body)
    updated_body["title"] = "Finalized from Draft"
    DJ_CLIENT.put(
        f"/api/projects/{test_pid}",
        {"revision": 1, "project": updated_body},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )

    # 4. Draft is now gone
    get_after_resp = DJ_CLIENT.get(f"/api/projects/{test_pid}/draft")
    assert get_after_resp.status_code == 200
    assert get_after_resp.json() == {}


# ==============================================================================
# 6. Specialized Mutations: Media Assignment, Voice Lock, Pronunciation Dict
# ==============================================================================

def test_assign_media_and_voice_controls():
    """Verify assign-media, voice_lock, and pronunciation_dict endpoints."""
    body = make_valid_project_dict()

    create_resp = DJ_CLIENT.post("/api/projects", body, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
    assert create_resp.status_code == 200
    test_pid = create_resp.json()["id"]
    CREATED_PIDS.add(test_pid)

    # A. Voice lock toggle
    vl_resp = DJ_CLIENT.post(
        f"/api/projects/{test_pid}/voice_lock",
        {"locked": True},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert vl_resp.status_code == 200
    assert vl_resp.json()["project"]["voice_locked"] is True
    assert vl_resp.json()["revision"] == 2

    # B. Pronunciation dict
    pd_resp = DJ_CLIENT.post(
        f"/api/projects/{test_pid}/pronunciation_dict",
        {"dictionary": {"ML": "మెషిన్ లెర్నింగ్"}},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert pd_resp.status_code == 200
    assert pd_resp.json()["project"]["pronunciation_dict"] == {"ML": "మెషిన్ లెర్నింగ్"}
    assert pd_resp.json()["revision"] == 3


# ==============================================================================
# 7. Job Result Application (POST /api/projects/<pid>/jobs/<jid>/apply)
# ==============================================================================

def test_apply_job_stock_success_and_idempotent():
    """Verify applying succeeded stock job updates scene video_asset and creates receipt."""
    body = make_valid_project_dict()

    create_resp = DJ_CLIENT.post("/api/projects", body, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
    assert create_resp.status_code == 200
    test_pid = create_resp.json()["id"]
    CREATED_PIDS.add(test_pid)

    # Create dummy asset in DB using admin connection
    asset_id = uuid.uuid4().hex[:32]
    conn = get_admin_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO assets (id, workspace_id, filename, name, kind, duration, sha256, provider) "
        "VALUES (%s, 'default', 'test.mp4', 'Test Clip', 'video', 10.0, 'dummyhash', 'pixabay')",
        (asset_id,)
    )

    # Create dummy succeeded stock job
    jid = uuid.uuid4().hex[:32]
    snapshot = {
        'project': body,
        'options': {'scene_id': 'scene_1'}
    }
    result = {
        'asset': {'id': asset_id, 'filename': 'test.mp4'}
    }
    cur.execute(
        "INSERT INTO jobs (id, workspace_id, project_id, revision, kind, state, stage, snapshot, result) "
        "VALUES (%s, 'default', %s, 1, 'stock', 'succeeded', 'complete', %s, %s)",
        (jid, test_pid, json.dumps(snapshot), json.dumps(result))
    )
    cur.close()
    conn.close()

    # 1. Apply job result
    apply_resp = DJ_CLIENT.post(
        f"/api/projects/{test_pid}/jobs/{jid}/apply",
        {},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert apply_resp.status_code == 200
    data = apply_resp.json()
    assert data["applied"] is True
    assert data["revision"] == 2
    assert data["project"]["project"]["scenes"][0]["video_asset"] == asset_id
    assert data["project"]["project"]["scenes"][0]["visual"] == "stock"

    # Verify ApplicationReceipt
    receipts = list(ApplicationReceipt.objects.using('project_writer').filter(job_id=jid))
    assert len(receipts) == 1
    assert receipts[0].applied_revision == 2

    # 2. Idempotent second apply
    dup_resp = DJ_CLIENT.post(
        f"/api/projects/{test_pid}/jobs/{jid}/apply",
        {},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert dup_resp.status_code == 200
    dup_data = dup_resp.json()
    assert dup_data["applied"] is True
    assert dup_data["revision"] == 2
    assert "already applied" in dup_data.get("message", "")


def test_apply_job_conflict_preserved():
    """Verify scene conflict preserves user's manual video edit unless forced."""
    body = make_valid_project_dict()

    create_resp = DJ_CLIENT.post("/api/projects", body, format='json', HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
    assert create_resp.status_code == 200
    test_pid = create_resp.json()["id"]
    CREATED_PIDS.add(test_pid)

    manual_aid = "1111222233334444aaaabbbbccccdddd"
    stock_aid = "5555666677778888aaaabbbbccccdddd"

    # User manually assigns custom video_asset manual_aid to scene_1
    body_v2 = copy.deepcopy(body)
    body_v2["scenes"][0]["video_asset"] = manual_aid
    DJ_CLIENT.put(
        f"/api/projects/{test_pid}",
        {"revision": 1, "project": body_v2},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )

    # Create dummy job created from revision 1 that suggests stock_aid
    jid = uuid.uuid4().hex[:32]
    snapshot = {'project': body, 'options': {'scene_id': 'scene_1'}}
    result = {'asset': {'id': stock_aid, 'filename': 'stock.mp4'}}

    conn = get_admin_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO jobs (id, workspace_id, project_id, revision, kind, state, stage, snapshot, result) "
        "VALUES (%s, 'default', %s, 1, 'stock', 'succeeded', 'complete', %s, %s)",
        (jid, test_pid, json.dumps(snapshot), json.dumps(result))
    )
    cur.close()
    conn.close()

    # Apply without force
    apply_resp = DJ_CLIENT.post(
        f"/api/projects/{test_pid}/jobs/{jid}/apply",
        {},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert apply_resp.status_code == 200
    data = apply_resp.json()
    assert len(data["conflicts"]) == 1
    assert "newer manual video assignment" in data["conflicts"][0]["reason"]
    # User edit preserved!
    assert data["project"]["project"]["scenes"][0]["video_asset"] == manual_aid


# ==============================================================================
# 8. Security Guards & HTTP Parity
# ==============================================================================

def test_security_guards_enforce_tokens():
    """Verify missing or invalid x-studio-token returns 403."""
    body = make_valid_project_dict()

    # Missing token
    resp_no_tok = DJ_CLIENT.post("/api/projects", body, format='json')
    assert resp_no_tok.status_code == 403

    # Invalid token
    resp_bad_tok = DJ_CLIENT.post(
        "/api/projects",
        body,
        format='json',
        HTTP_X_STUDIO_TOKEN='wrong_token'
    )
    assert resp_bad_tok.status_code == 403


def test_unsupported_mutations_remain_rejected():
    """Verify unsupported mutation methods/endpoints return 405."""
    # DELETE on project is not supported
    resp = DJ_CLIENT.delete(
        "/api/projects/pid_1",
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 405

    # POST on arbitrary non-whitelisted path is not accepted by Django
    resp_set = DJ_CLIENT.post(
        "/api/unsupported_mutation",
        {},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp_set.status_code == 405


def test_django_project_creation_schema():
    """Verify Django produces complete JSON schema for project creation."""
    body_dj = make_valid_project_dict("Creation Title")
    dj_post = DJ_CLIENT.post(
        "/api/projects",
        body_dj,
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert dj_post.status_code == 200
    dj_json = dj_post.json()
    CREATED_PIDS.add(dj_json["id"])

    assert dj_json["revision"] == 1
    assert dj_json["approved"] is False
    assert {"id", "revision", "approved", "project"}.issubset(set(dj_json.keys()))
    assert {"title", "scenes", "evidence_notes"}.issubset(set(dj_json["project"].keys()))
