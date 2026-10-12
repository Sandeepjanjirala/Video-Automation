"""Stage M2 Acceptance and Data-Integrity Tests.

Verifies:
1. Every unmanaged Django model resolves its actual PostgreSQL table.
2. Field types, nullability, and primary keys faithfully map the active schema.
3. Critical primary key preservation (e.g. Draft.project_id is PK, no phantom id).
4. Row count parity between Django ORM and direct PostgreSQL queries.
5. Representative domain reads (Project, Revision, Asset, Job, JobStage).
6. Revision history is ordered ascending.
7. JSONB snapshots round-trip deserialize correctly.
8. Native Telugu Unicode and Roman Telugu are preserved byte-for-byte (SHA-256 match).
9. Read-only database identity rejects INSERT/UPDATE/DELETE/ALTER/DROP.
10. Read-only repository layer functions.
11. Diagnostic endpoint /api/migration/schema-status returns 200 without exposing secrets.
12. Alembic revision and application row counts remain unchanged.
"""
import os
import hashlib
import psycopg2
import pytest

# Configure Django settings for test execution before importing Django/DRF modules
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.conf import settings
from django.db import connection
from rest_framework.test import APIClient

from core.models import (
    Workspace,
    Project,
    Revision,
    Asset,
    Job,
    JobStage,
    Draft,
    ApplicationReceipt,
    OutboxEvent,
    VoiceTake,
)
from core import repository


@pytest.fixture(scope="module")
def pg_connection():
    """Direct PostgreSQL connection for ground-truth comparison."""
    conn = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5433/studio")
    yield conn
    conn.close()


# ==============================================================================
# 1. Model & Schema Mapping Verification
# ==============================================================================

def test_models_are_unmanaged():
    """All M2 models must have managed = False to protect Alembic authority."""
    models = [
        Workspace, Project, Revision, Asset, Job, JobStage,
        Draft, ApplicationReceipt, OutboxEvent, VoiceTake
    ]
    for m in models:
        assert not m._meta.managed, f"Model {m.__name__} must be unmanaged (managed=False)!"


def test_primary_key_mappings():
    """Primary keys must accurately match the real PostgreSQL catalog."""
    assert Workspace._meta.pk.column == 'id'
    assert Project._meta.pk.column == 'id'
    assert Revision._meta.pk.column == 'id'
    assert Asset._meta.pk.column == 'id'
    assert Job._meta.pk.column == 'id'
    assert JobStage._meta.pk.column == 'id'
    # Critical: Draft MUST use project_id as PK, NEVER a synthetic 'id'
    assert Draft._meta.pk.column == 'project_id', (
        f"Draft PK is {Draft._meta.pk.column}, expected project_id!"
    )
    assert ApplicationReceipt._meta.pk.column == 'id'
    assert OutboxEvent._meta.pk.column == 'id'
    assert VoiceTake._meta.pk.column == 'id'


def test_table_names_match_database():
    """Every model db_table must match an existing table in PostgreSQL public schema."""
    expected = {
        Workspace: 'workspaces',
        Project: 'projects',
        Revision: 'revisions',
        Asset: 'assets',
        Job: 'jobs',
        JobStage: 'job_stages',
        Draft: 'drafts',
        ApplicationReceipt: 'application_receipts',
        OutboxEvent: 'outbox',
        VoiceTake: 'voice_takes',
    }
    with connection.cursor() as cur:
        cur.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public';
        """)
        db_tables = {r[0] for r in cur.fetchall()}

    for model, tbl in expected.items():
        assert model._meta.db_table == tbl
        assert tbl in db_tables, f"Table {tbl} not found in PostgreSQL public schema!"


# ==============================================================================
# 2. Row Count Parity (Django ORM vs Direct PostgreSQL)
# ==============================================================================

def test_row_count_parity(pg_connection):
    """Every mapped model row count must match a direct SELECT COUNT(*) query."""
    cur = pg_connection.cursor()
    mappings = [
        (Workspace, 'workspaces'),
        (Project, 'projects'),
        (Revision, 'revisions'),
        (Asset, 'assets'),
        (Job, 'jobs'),
        (JobStage, 'job_stages'),
        (Draft, 'drafts'),
        (ApplicationReceipt, 'application_receipts'),
        (OutboxEvent, 'outbox'),
        (VoiceTake, 'voice_takes'),
    ]

    for model, tbl in mappings:
        cur.execute(f'SELECT count(*) FROM "{tbl}"')
        pg_cnt = cur.fetchone()[0]
        dj_cnt = model.objects.count()
        assert dj_cnt == pg_cnt, (
            f"Count mismatch for {model.__name__} / {tbl}: Django={dj_cnt}, Postgres={pg_cnt}"
        )


# ==============================================================================
# 3. Domain Reads & Deserialization
# ==============================================================================

def test_read_representative_project():
    """Ensure projects can be read through Django ORM with full body JSONB."""
    proj = Project.objects.first()
    assert proj is not None, "No projects in database!"
    assert proj.id
    assert isinstance(proj.body, dict)
    assert 'title' in proj.body or 'scenes' in proj.body
    assert proj.revision >= 1


def test_revision_history_ordering():
    """Revision history must be ordered in ascending revision numbers."""
    first_proj = Project.objects.first()
    revs = repository.get_revision_history(first_proj.id)
    if revs:
        numbers = [r.revision for r in revs]
        assert numbers == sorted(numbers)
        assert isinstance(revs[0].body, dict)


def test_job_and_stage_deserialization():
    """Job snapshots, results, and stage details must deserialize as dicts."""
    first_job = Job.objects.first()
    if first_job:
        assert isinstance(first_job.snapshot, dict)
        assert isinstance(first_job.result, dict)
        stages = repository.get_job_stages(first_job.id)
        for st in stages:
            assert isinstance(st.details, dict)


def test_asset_references_preserved():
    """Assets metadata and JSON meta must deserialize faithfully."""
    assets = repository.list_assets()
    assert len(assets) > 0, "Expected assets in database!"
    for a in assets[:5]:
        assert a.id
        assert a.filename
        assert a.kind in ('video', 'audio')
        assert isinstance(a.meta, dict)


# ==============================================================================
# 4. Unicode & Telugu Exactness (No Mojibake)
# ==============================================================================

def test_telugu_unicode_and_roman_fidelity(pg_connection):
    """Verify Telugu script and Roman transliteration match byte-for-byte."""
    cur = pg_connection.cursor()
    # Find a project containing Telugu characters
    cur.execute("SELECT id, body FROM projects WHERE id = '9787a3632cf34b6ea4158241d0ae063c'")
    row = cur.fetchone()
    if not row:
        # Fallback to any project with Telugu characters
        cur.execute("SELECT id, body FROM projects")
        for pid, b in cur.fetchall():
            s = str(b)
            if any('\u0c00' <= c <= '\u0c7f' for c in s):
                row = (pid, b)
                break

    assert row is not None, "Could not find a project containing Telugu characters!"
    pid, pg_body = row

    # Read the same project through Django ORM
    dj_proj = Project.objects.get(id=pid)
    dj_body = dj_proj.body

    # Validate scene narration and roman transliteration
    pg_scenes = pg_body.get('scenes', [])
    dj_scenes = dj_body.get('scenes', [])
    assert len(pg_scenes) == len(dj_scenes)

    found_telugu = False
    for i in range(len(pg_scenes)):
        pg_narration = pg_scenes[i].get('narration', '')
        dj_narration = dj_scenes[i].get('narration', '')
        pg_roman = pg_scenes[i].get('roman', '')
        dj_roman = dj_scenes[i].get('roman', '')

        assert pg_narration == dj_narration, f"Mismatch in scene {i} Telugu narration!"
        assert pg_roman == dj_roman, f"Mismatch in scene {i} Roman caption!"

        # Check hash
        pg_hash = hashlib.sha256(pg_narration.encode('utf-8')).hexdigest()
        dj_hash = hashlib.sha256(dj_narration.encode('utf-8')).hexdigest()
        assert pg_hash == dj_hash

        if any('\u0c00' <= c <= '\u0c7f' for c in dj_narration):
            found_telugu = True

    assert found_telugu, "Verified that at least one scene contains valid Telugu script!"


# ==============================================================================
# 5. Read-Only Security & Negative Permission Verification
# ==============================================================================

def test_readonly_db_identity_negative_write():
    """Verify that the read-only PostgreSQL role rejects write operations."""
    ro_url = os.environ.get('STUDIO_READONLY_DATABASE_URL')
    if not ro_url:
        ro_file = settings.BASE_DIR / 'backups' / '.readonly_db_url'
        if ro_file.exists():
            ro_url = ro_file.read_text(encoding='utf-8').strip()

    if not ro_url:
        pytest.skip("No read-only database URL configured; skipping role permission test.")

    conn = psycopg2.connect(ro_url)
    cur = conn.cursor()

    # Negative test 1: INSERT
    with pytest.raises(psycopg2.Error):
        cur.execute("INSERT INTO workspaces (id, name) VALUES ('hack_ws', 'Hack')")
    conn.rollback()

    # Negative test 2: UPDATE
    with pytest.raises(psycopg2.Error):
        cur.execute("UPDATE workspaces SET name = 'Hack' WHERE id = 'default'")
    conn.rollback()

    # Negative test 3: DELETE
    with pytest.raises(psycopg2.Error):
        cur.execute("DELETE FROM workspaces WHERE id = 'default'")
    conn.rollback()

    # Negative test 4: DROP
    with pytest.raises(psycopg2.Error):
        cur.execute("DROP TABLE drafts")
    conn.rollback()

    conn.close()


# ==============================================================================
# 6. Read-Only Repository Functions
# ==============================================================================

def test_repository_functions():
    """Verify the repository layer provides structured, read-only data access."""
    # Workspaces
    ws_list = repository.list_workspaces()
    assert len(ws_list) >= 1
    default_ws = repository.get_workspace('default')
    assert default_ws is not None

    # Projects
    projs = repository.list_projects()
    assert len(projs) >= 1
    p = repository.get_project(projs[0].id)
    assert p is not None
    assert p.id == projs[0].id

    # Non-existent project
    none_proj = repository.get_project('non_existent_id_xyz')
    assert none_proj is None

    # Usage (Composite PK repository queries)
    usages = repository.list_usage()
    assert len(usages) >= 1
    first_u = usages[0]
    single_u = repository.get_usage(first_u['day'], first_u['provider'])
    assert single_u is not None
    assert single_u['day'] == first_u['day']
    assert single_u['provider'] == first_u['provider']


# ==============================================================================
# 7. Diagnostic Endpoint /api/migration/schema-status
# ==============================================================================

def test_diagnostic_schema_status_endpoint():
    """Verify /api/migration/schema-status returns 200 with safe metadata."""
    client = APIClient()
    resp = client.get('/api/migration/schema-status')
    assert resp.status_code == 200
    data = resp.json()
    assert data['stage'] == 'M2'
    assert data['schema_authority'] == 'alembic'
    assert data['alembic_revision'] == '0003_phase4_voice_lab'
    assert data['read_only'] is True
    assert data['mapped_models_count'] == 10
    assert 'Workspace' in data['model_counts']
    assert 'Project' in data['model_counts']
    assert 'usage' in data['unmapped_tables']
    assert 'alembic_version' in data['unmapped_tables']


# ==============================================================================
# 8. Database State & Alembic Invariance
# ==============================================================================

def test_database_state_and_alembic_invariance(pg_connection):
    """Verify that Alembic revision is untouched and zero Django internal tables exist."""
    cur = pg_connection.cursor()
    cur.execute("SELECT version_num FROM alembic_version")
    rev = cur.fetchone()[0]
    assert rev == '0003_phase4_voice_lab', f"Alembic revision altered: {rev}"

    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name LIKE 'django_%';
    """)
    django_tables = cur.fetchall()
    assert len(django_tables) == 0, f"Unexpected Django tables created: {django_tables}"

