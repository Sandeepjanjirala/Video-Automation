"""Focused tests for Django Migration Stage M1.

Verifies:
1. Django initializes and checks pass without errors.
2. GET /api/health returns 200 with expected structure.
3. PostgreSQL connectivity is verified as read-only.
4. Health endpoint does not mutate any database state or table counts.
5. Existing FastAPI continues running and responds.
6. Database counts and Alembic version remain strictly unchanged before and after.
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import pytest
import httpx
from django.test import Client

# Configure Django settings for test execution
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.db import connection


@pytest.fixture(scope="module")
def db_baseline_counts():
    """Capture baseline table counts from active PostgreSQL database."""
    conn = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5433/studio")
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
        ORDER BY table_name;
    """)
    tables = [r['table_name'] for r in cur.fetchall()]
    counts = {}
    for t in tables:
        cur.execute(f'SELECT count(*) as c FROM "{t}";')
        counts[t] = cur.fetchone()['c']

    cur.execute("SELECT version_num FROM alembic_version LIMIT 1;")
    alembic_version = cur.fetchone()['version_num']

    cur.close()
    conn.close()
    return {"counts": counts, "alembic_version": alembic_version}


def test_django_system_check():
    """Verify Django configuration passes all system checks."""
    from django.core.management import call_command
    # Should complete with 0 errors
    call_command('check')


def test_django_health_endpoint_success():
    """Verify GET /api/health returns 200 with verified PostgreSQL & tool status."""
    client = Client()
    response = client.get('/api/health')
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "ok"
    assert data["service"] == "django"
    assert data["database"]["connected"] is True
    assert data["database"]["engine"] == "postgresql"
    assert data["database"]["tables_visible"] is True
    assert data["database"]["current_alembic_revision"] == "0003_phase4_voice_lab"
    assert data["database"]["read_query_verified"] is True
    assert data["redis"]["connected"] is True
    assert "ffmpeg" in data["media_tools"]
    assert "ffprobe" in data["media_tools"]


def test_health_check_does_not_mutate_data(db_baseline_counts):
    """Verify multiple health check calls cause zero database mutations."""
    client = Client()
    for _ in range(5):
        res = client.get('/api/health')
        assert res.status_code == 200

    conn = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5433/studio")
    cur = conn.cursor(cursor_factory=RealDictCursor)
    for t, expected_cnt in db_baseline_counts["counts"].items():
        cur.execute(f'SELECT count(*) as c FROM "{t}";')
        current_cnt = cur.fetchone()['c']
        assert current_cnt == expected_cnt, f"Table {t} mutated! Expected {expected_cnt}, got {current_cnt}"

    cur.execute("SELECT version_num FROM alembic_version LIMIT 1;")
    current_ver = cur.fetchone()['version_num']
    assert current_ver == db_baseline_counts["alembic_version"]

    cur.close()
    conn.close()


def test_fastapi_coexistence_and_accessibility():
    """Verify FastAPI on 127.0.0.1:8787 remains completely accessible."""
    try:
        res = httpx.get('http://127.0.0.1:8787/api/bootstrap', timeout=5.0)
        assert res.status_code == 200
        data = res.json()
        assert "token" in data
        assert "projects" in data
    except httpx.ConnectError:
        pytest.fail("FastAPI server on 127.0.0.1:8787 is not reachable!")


def test_alembic_schema_authority_intact(db_baseline_counts):
    """Verify Alembic remains sole authority and no Django migrations table exists."""
    conn = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5433/studio")
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public';
    """)
    current_tables = {r[0] for r in cur.fetchall()}
    # Crucial: Django's internal django_migrations table must NOT exist in studio database
    assert "django_migrations" not in current_tables, "django_migrations was created in studio database!"
    assert "alembic_version" in current_tables

    cur.close()
    conn.close()
