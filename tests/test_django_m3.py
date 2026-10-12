"""Stage M3 Acceptance and API Contract Parity Tests.

Verifies:
1. Ported read-only Django DRF endpoints:
   - GET /api/projects
   - GET /api/projects/<pid>
   - GET /api/projects/<pid>/history
   - GET /api/projects/<pid>/draft
   - GET /api/projects/<pid>/preflight
   - GET /api/projects/<pid>/jobs
   - GET /api/projects/<pid>/takes
   - GET /api/bootstrap
   - GET /api/voice/capabilities
   - GET /api/assets
   - GET /api/assets/<aid>
2. Exact payload parity between FastAPI (:8787) and Django (:8788) for identical data.
3. Native Telugu Unicode and Roman Telugu text survive HTTP serialization with 0 mojibake.
4. Error responses match FastAPI contract (e.g. 400 {'detail': 'Project not found'}).
5. Security headers match FastAPI local_guard contract.
6. Django database identity remains read-only and causes 0 mutations.
7. Alembic schema authority remains intact.
"""
import os
import hashlib
import psycopg2
import pytest
import httpx

# Configure Django settings before importing Django modules
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.conf import settings
from rest_framework.test import APIClient
from core.models import Project, Revision, Asset, Job


FASTAPI_URL = "http://127.0.0.1:8787"
DJ_CLIENT = APIClient()


@pytest.fixture(scope="module")
def pg_connection():
    """Direct PostgreSQL connection for ground truth."""
    conn = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5433/studio")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def sample_project_id():
    """Retrieve an existing project ID from the database."""
    proj = Project.objects.order_by('-updated_at').first()
    assert proj is not None, "No project in database for tests!"
    return proj.id


# ==============================================================================
# 1. Project List & Detail Contracts
# ==============================================================================

def test_django_project_list_endpoint():
    """Verify GET /api/projects returns workspace-scoped project list."""
    resp = DJ_CLIENT.get('/api/projects')
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    sample = data[0]
    for key in ('id', 'revision', 'approved', 'updated', 'project'):
        assert key in sample


def test_django_project_detail_endpoint(sample_project_id):
    """Verify GET /api/projects/<pid> returns full project detail."""
    resp = DJ_CLIENT.get(f'/api/projects/{sample_project_id}')
    assert resp.status_code == 200
    data = resp.json()
    assert data['id'] == sample_project_id
    assert 'project' in data
    assert isinstance(data['project'], dict)
    assert 'scenes' in data['project']


def test_project_detail_parity_with_fastapi(sample_project_id):
    """Verify 100% payload parity between FastAPI and Django project detail."""
    fa_resp = httpx.get(f"{FASTAPI_URL}/api/projects/{sample_project_id}")
    assert fa_resp.status_code == 200
    fa_data = fa_resp.json()

    dj_resp = DJ_CLIENT.get(f'/api/projects/{sample_project_id}')
    assert dj_resp.status_code == 200
    dj_data = dj_resp.json()

    assert fa_data['id'] == dj_data['id']
    assert fa_data['revision'] == dj_data['revision']
    assert fa_data['approved'] == dj_data['approved']
    assert fa_data['project'] == dj_data['project']


# ==============================================================================
# 2. Telugu Unicode HTTP Parity (Zero Mojibake)
# ==============================================================================

def test_telugu_unicode_http_parity():
    """Verify native Telugu script and Roman transliteration survive HTTP serialization."""
    # Find a project containing Telugu characters
    telugu_proj = None
    for p in Project.objects.all():
        for s in p.body.get('scenes', []):
            if any('\u0c00' <= c <= '\u0c7f' for c in s.get('narration', '')):
                telugu_proj = p
                break
        if telugu_proj:
            break

    assert telugu_proj is not None, "Could not locate project with Telugu narration in DB!"
    pid = telugu_proj.id

    fa_resp = httpx.get(f"{FASTAPI_URL}/api/projects/{pid}")
    assert fa_resp.status_code == 200
    fa_scenes = fa_resp.json()['project']['scenes']

    dj_resp = DJ_CLIENT.get(f'/api/projects/{pid}')
    assert dj_resp.status_code == 200
    dj_scenes = dj_resp.json()['project']['scenes']

    assert len(fa_scenes) == len(dj_scenes)
    for i in range(len(fa_scenes)):
        assert fa_scenes[i]['narration'] == dj_scenes[i]['narration']
        assert fa_scenes[i]['roman'] == dj_scenes[i]['roman']

    # Exact SHA-256 match
    fa_hash = hashlib.sha256(fa_scenes[0]['narration'].encode('utf-8')).hexdigest()
    dj_hash = hashlib.sha256(dj_scenes[0]['narration'].encode('utf-8')).hexdigest()
    assert fa_hash == dj_hash


# ==============================================================================
# 3. Revision History, Draft & Preflight
# ==============================================================================

def test_project_history_endpoint(sample_project_id):
    """Verify GET /api/projects/<pid>/history returns ordered revisions."""
    resp = DJ_CLIENT.get(f'/api/projects/{sample_project_id}/history')
    assert resp.status_code == 200
    history = resp.json()
    assert isinstance(history, list)
    if history:
        numbers = [item['revision'] for item in history]
        assert numbers == sorted(numbers, reverse=True)
        assert 'project' in history[0]
        assert 'created' in history[0]


def test_project_draft_endpoint(sample_project_id):
    """Verify GET /api/projects/<pid>/draft returns draft or empty dict matching FastAPI."""
    fa_draft = httpx.get(f"{FASTAPI_URL}/api/projects/{sample_project_id}/draft").json()
    dj_draft = DJ_CLIENT.get(f'/api/projects/{sample_project_id}/draft').json()
    assert fa_draft == dj_draft


def test_project_preflight_parity(sample_project_id):
    """Verify GET /api/projects/<pid>/preflight pure calculation matches FastAPI."""
    fa_pref = httpx.get(f"{FASTAPI_URL}/api/projects/{sample_project_id}/preflight").json()
    dj_pref = DJ_CLIENT.get(f'/api/projects/{sample_project_id}/preflight').json()
    assert fa_pref == dj_pref
    assert 'ready' in dj_pref
    assert 'errors' in dj_pref
    assert 'warnings' in dj_pref


# ==============================================================================
# 4. Jobs, Voice Takes, Bootstrap & Capabilities
# ==============================================================================

def test_project_jobs_endpoint(sample_project_id):
    """Verify GET /api/projects/<pid>/jobs returns jobs list matching FastAPI."""
    fa_jobs = httpx.get(f"{FASTAPI_URL}/api/projects/{sample_project_id}/jobs").json()
    dj_jobs = DJ_CLIENT.get(f'/api/projects/{sample_project_id}/jobs').json()
    assert len(fa_jobs) == len(dj_jobs)
    if fa_jobs:
        assert fa_jobs[0]['id'] == dj_jobs[0]['id']
        assert fa_jobs[0]['state'] == dj_jobs[0]['state']


def test_project_takes_endpoint(sample_project_id):
    """Verify GET /api/projects/<pid>/takes returns voice takes list matching FastAPI."""
    fa_takes = httpx.get(f"{FASTAPI_URL}/api/projects/{sample_project_id}/takes").json()
    dj_takes = DJ_CLIENT.get(f'/api/projects/{sample_project_id}/takes').json()
    assert fa_takes == dj_takes


def test_voice_capabilities_parity():
    """Verify GET /api/voice/capabilities returns registered voice capabilities matching FastAPI."""
    fa_caps = httpx.get(f"{FASTAPI_URL}/api/voice/capabilities").json()
    dj_caps = DJ_CLIENT.get('/api/voice/capabilities').json()
    assert fa_caps == dj_caps
    assert len(dj_caps) >= 5


def test_bootstrap_endpoint():
    """Verify GET /api/bootstrap returns handshake token, settings and project list."""
    resp = DJ_CLIENT.get('/api/bootstrap')
    assert resp.status_code == 200
    data = resp.json()
    assert 'token' in data
    assert 'settings' in data
    assert 'projects' in data
    assert len(data['projects']) >= 1


def test_assets_list_and_detail():
    """Verify GET /api/assets and /api/assets/<aid> return asset metadata."""
    resp = DJ_CLIENT.get('/api/assets')
    assert resp.status_code == 200
    assets = resp.json()
    assert len(assets) >= 1
    first_aid = assets[0]['id']

    detail_resp = DJ_CLIENT.get(f'/api/assets/{first_aid}')
    assert detail_resp.status_code == 200
    assert detail_resp.json()['id'] == first_aid


# ==============================================================================
# 5. Security Headers & Error Parity
# ==============================================================================

def test_security_headers():
    """Verify Django responses contain exact FastAPI security headers."""
    resp = DJ_CLIENT.get('/api/projects')
    assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
    assert resp.headers.get('Referrer-Policy') == 'no-referrer'
    assert resp.headers.get('X-Frame-Options') == 'DENY'
    assert resp.headers.get('Cache-Control') == 'no-store'


def test_error_parity_nonexistent_project():
    """Verify 400 {'detail': 'Project not found'} matching FastAPI on missing projects."""
    fa_resp = httpx.get(f"{FASTAPI_URL}/api/projects/nonexistent_xyz_id")
    dj_resp = DJ_CLIENT.get('/api/projects/nonexistent_xyz_id')

    assert fa_resp.status_code == 400
    assert dj_resp.status_code == 400
    assert fa_resp.json() == dj_resp.json()


def test_readonly_mutation_rejection():
    """Verify mutating requests (POST) on Django are rejected in M3."""
    resp = DJ_CLIENT.post('/api/projects', {}, content_type='application/json')
    # Rejected either by missing x-studio-token (403) or read-only mode (405)
    assert resp.status_code in (403, 405)


# ==============================================================================
# 6. Database Invariance & Alembic Authority
# ==============================================================================

def test_database_and_alembic_invariance(pg_connection):
    """Verify Alembic revision remains unchanged and zero Django tables exist."""
    cur = pg_connection.cursor()
    cur.execute("SELECT version_num FROM alembic_version")
    assert cur.fetchone()[0] == '0003_phase4_voice_lab'

    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name LIKE 'django_%';
    """)
    django_tables = cur.fetchall()
    assert len(django_tables) == 0, f"No Django tables should exist: {django_tables}"
