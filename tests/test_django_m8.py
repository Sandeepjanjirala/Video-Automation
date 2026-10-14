"""Test suite for Stage M8: Final Frontend Migration (React/TypeScript -> Django Templates + jQuery).

Verifies:
1. Root / and /index.html serve Django Templates + jQuery UI (HTTP 200).
2. Clean separation: Zero React runtime references or bundle imports in M8 UI.
3. Complete template hierarchy: base.html, index.html, and all partials render error-free.
4. Static file delivery: jQuery 3.7.1, modular JS scripts (api.js, state.js, media.js, etc.).
5. Path traversal protection on static asset endpoints.
6. Schema authority and Alembic invariance (0003_phase4_voice_lab, managed=False).
"""
import os
import sys
from pathlib import Path
import psycopg2
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.template.loader import render_to_string
from rest_framework.test import APIClient
from core.models import Project, Revision, Job, JobStage, OutboxEvent, Asset, VoiceTake
from core.views import DJANGO_TOKEN

CLIENT = APIClient()


@pytest.fixture(scope="module")
def pg_connection():
    """Superuser PostgreSQL connection for invariant checks."""
    conn = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5433/studio")
    yield conn
    conn.close()


# ==============================================================================
# 1. Root & Template Rendering Tests
# ==============================================================================

def test_m8_root_serves_django_templates():
    """Verify root / serves Django Templates + jQuery UI with HTTP 200."""
    resp = CLIENT.get('/')
    assert resp.status_code == 200
    assert 'text/html' in resp.headers['Content-Type']
    html = resp.content.decode('utf-8')

    # Must contain M8 layout elements
    assert 'id="app-main-layout"' in html
    assert 'id="tab-write"' in html
    assert 'id="tab-voice"' in html
    assert 'id="tab-export"' in html
    assert 'id="panel-production-jobs"' in html
    assert 'id="media-library-modal"' in html
    assert 'id="settings-modal"' in html
    assert 'id="history-modal"' in html

    # Must include jQuery 3.7.1 and modular scripts
    assert 'vendor/jquery.min.js' in html
    assert 'js/api.js' in html
    assert 'js/state.js' in html
    assert 'js/projects.js' in html
    assert 'js/scenes.js' in html
    assert 'js/media.js' in html
    assert 'js/voice.js' in html
    assert 'js/jobs.js' in html
    assert 'js/settings.js' in html
    assert 'js/app.js' in html

    # MUST NOT reference React bundle or React DOM root
    assert '<div id="root">' not in html
    assert 'react.development.js' not in html
    assert 'react-dom' not in html


def test_m8_index_html_route_parity():
    """Verify /index.html and /m8/ match root / response."""
    resp_index = CLIENT.get('/index.html')
    assert resp_index.status_code == 200
    assert 'id="app-main-layout"' in resp_index.content.decode('utf-8')

    resp_m8 = CLIENT.get('/m8/')
    assert resp_m8.status_code == 200
    assert 'id="app-main-layout"' in resp_m8.content.decode('utf-8')


def test_m8_template_engine_renders_all_partials():
    """Verify Django template engine renders studio/index.html and all partials cleanly."""
    rendered = render_to_string('studio/index.html', {'title': 'Test Studio'})
    assert '<!DOCTYPE html>' in rendered
    assert 'Telugu Reel Studio' in rendered
    assert 'btn-new-project' in rendered
    assert 'btn-save-project' in rendered
    assert 'btn-approve-project' in rendered
    assert 'btn-render-production' in rendered


# ==============================================================================
# 2. Static Asset Delivery & Path Traversal Protection
# ==============================================================================

def test_m8_static_jquery_delivery():
    """Verify pinned jQuery 3.7.1 static asset is delivered correctly."""
    resp = CLIENT.get('/static/vendor/jquery.min.js')
    assert resp.status_code == 200
    content = b"".join(resp.streaming_content).decode('utf-8')
    assert 'jQuery v3.7.1' in content


def test_m8_static_modular_scripts_delivery():
    """Verify all custom modular JS scripts are delivered via /static/js/."""
    modules = [
        'api.js', 'state.js', 'media.js', 'voice.js',
        'scenes.js', 'projects.js', 'jobs.js', 'settings.js', 'app.js'
    ]
    for mod in modules:
        resp = CLIENT.get(f'/static/js/{mod}')
        assert resp.status_code == 200, f"Failed to serve /static/js/{mod}"
        content = b"".join(resp.streaming_content)
        assert len(content) > 50


def test_m8_static_path_traversal_protection():
    """Verify path traversal attacks on static routes are blocked."""
    forbidden_paths = [
        '/static/../secrets.json',
        '/static/../../settings.py',
        '/static/..%2Fsecrets.json',
    ]
    for p in forbidden_paths:
        resp = CLIENT.get(p)
        assert resp.status_code in (400, 403, 404), f"Expected blocked status for {p}, got {resp.status_code}"


# ==============================================================================
# 3. Schema Authority and Alembic Invariance
# ==============================================================================

def test_m8_schema_authority_and_alembic_invariance(pg_connection):
    """Verify Alembic revision remains 0003_phase4_voice_lab and all models are unmanaged."""
    cur = pg_connection.cursor()
    cur.execute("SELECT version_num FROM alembic_version;")
    alembic_rev = cur.fetchone()[0]
    assert alembic_rev == '0003_phase4_voice_lab'
    cur.close()

    for model_cls in (Job, JobStage, OutboxEvent, Project, Revision, Asset, VoiceTake):
        assert model_cls._meta.managed is False, f"{model_cls.__name__} must be unmanaged"
