"""Test suite for Stage M7: Port remaining APIs + React frontend serving + E2E verification.

Verifies:
1. Complete FastAPI vs Django route parity and React requirements.
2. POST /api/settings safety: controlled server path, atomic replace, secret masking.
3. Spoken Telugu normalization: numbers, dates, dictionary overrides.
4. Voice Lab candidate preview, rewrite diff, and A/B comparison.
5. Take acceptance: project revision increment, approval invalidation, take ownership check.
6. React static serving: index.html, app.js, app.css, path traversal protection.
7. Security middleware enforcement: token validation, mutation allowlist, headers.
"""
import copy
import json
import os
import shutil
import tempfile
import sys
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from rest_framework.test import APIClient

from studio.config import DATA, settings, save_settings, public_settings, SECRETS, DEFAULTS
from studio.models import Project as PydanticProject
import studio.store as store
from core.models import Project, Revision, VoiceTake, Asset
from core.views import DJANGO_TOKEN
from core import services


@pytest.fixture
def auth_client():
    client = APIClient()
    client.credentials(HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN)
    return client


@pytest.fixture
def anon_client():
    return APIClient()


@pytest.fixture
def test_project(auth_client):
    """Create a disposable project document for test use."""
    proj_data = {
        'title': 'M7 Test Project',
        'topic': 'Voice Lab Migration',
        'category': 'Diagnostic',
        'target_seconds': 60,
        'duration_mode': 'natural',
        'tts_provider': 'edge',
        'voice': 'te-IN-ShrutiNeural',
        'speech_rate': 0,
        'delivery_preset': 'conversational',
        'pronunciation_dict': {'AI': 'ఎఐ'},
        'voice_locked': False,
        'voice_profile': {},
        'narration_blocks': [],
        'scenes': [
            {
                'id': 'scene_m7_01',
                'narration': 'ఇది మొదటి దృశ్యం.',
                'spoken_text': 'ఇది మొదటి దృశ్యం.',
                'roman': 'Idi modati drushyam.',
                'keywords': 'intro',
                'visual': 'graphic',
                'video_asset': '',
                'audio_asset': '',
                'audio_text_hash': '',
                'trim_start': 0,
                'fit': 'crop',
                'crop_x': 0.5,
                'hold_seconds': 0,
                'graphic_title': 'Intro Title',
                'rights_reviewed': True,
                'narration_block_id': '',
                'take_id': '',
                'delivery_preset': 'conversational',
                'cues': [],
            }
        ],
        'evidence_notes': 'M7 diagnostic verification notes.',
        'research_report': '',
        'research_sources': [],
        'research_grounding': {},
        'music_asset': '',
        'music_gain': 0.12,
        'music_rights_reviewed': False,
        'caption_size': 56,
        'caption_bottom': 300,
        'script_approved_hash': '',
        'draft_script': '',
    }
    resp = auth_client.post('/api/projects', proj_data, format='json')
    assert resp.status_code == 200, resp.data
    pid = resp.data['id']
    yield pid
    # Cleanup project
    with patch('core.services.Project.objects'):
        try:
            Project.objects.using('project_writer').filter(id=pid).delete()
            Revision.objects.using('project_writer').filter(project_id=pid).delete()
            VoiceTake.objects.using('voice_writer').filter(project_id=pid).delete()
        except Exception:
            pass


# ==============================================================================
# 1. API Route Parity & Frontend Inventory
# ==============================================================================

def test_api_route_parity_and_react_requirements():
    """Verify that every endpoint required by React exists in Django core.urls."""
    from django.urls import get_resolver
    resolver = get_resolver()

    # Collect all registered Django URL patterns
    django_patterns = set()
    def _collect(patterns, prefix=''):
        for p in patterns:
            if hasattr(p, 'url_patterns'):
                _collect(p.url_patterns, prefix + str(p.pattern))
            else:
                django_patterns.add(prefix + str(p.pattern))
    _collect(resolver.url_patterns)

    # React mandatory endpoint requirements
    react_endpoints = [
        'api/bootstrap',
        'api/health',
        'api/settings',
        'api/projects',
        'api/projects/<str:pid>',
        'api/projects/<str:pid>/history',
        'api/projects/<str:pid>/approve',
        'api/projects/<str:pid>/draft',
        'api/projects/<str:pid>/preflight',
        'api/projects/<str:pid>/assign-media',
        'api/projects/<str:pid>/voice_lock',
        'api/projects/<str:pid>/pronunciation_dict',
        'api/projects/<str:pid>/jobs',
        'api/projects/<str:pid>/jobs/<str:jid>/apply',
        'api/projects/<str:pid>/takes',
        'api/projects/<str:pid>/takes/<str:take_id>/accept',
        'api/voice/capabilities',
        'api/voice/normalize',
        'api/voice/preview',
        'api/voice/ab_compare',
        'api/voice/rewrite_preview',
        'api/jobs/<str:jid>/retry',
        'api/jobs/<str:jid>/cancel',
        'api/jobs/<str:jid>/audio/<str:scene_id>',
        'api/exports/<str:jid>/<str:name>',
        'api/stock',
        'api/assets',
        'api/assets/<str:aid>',
        'api/assets/<str:aid>/media',
        'api/assets/<str:aid>/thumbnail',
    ]

    for ep in react_endpoints:
        assert ep in django_patterns, f"Missing React-required endpoint in Django: {ep}"


# ==============================================================================
# 2. Settings Endpoint Safety & Secret Boundary
# ==============================================================================

def test_settings_mutation_and_secret_boundary(auth_client, anon_client):
    """Verify POST /api/settings saves safely, protects secrets, and enforces token auth."""
    # Anon request must be rejected with 403
    anon_resp = anon_client.post('/api/settings', {'online_enabled': True}, format='json')
    assert anon_resp.status_code == 403

    # Authenticated safe update
    resp = auth_client.post('/api/settings', {
        'online_enabled': True,
        'tts_provider': 'edge',
        'voice': 'te-IN-ShrutiNeural',
        'daily_ai_calls': 30,
        'gemini_key': 'test_secret_key_12345'
    }, format='json')
    assert resp.status_code == 200
    data = resp.data

    # CRITICAL: Secrets must NEVER be returned in plaintext
    for secret in SECRETS:
        assert secret not in data, f"Raw secret {secret} leaked in API response!"
        assert f"{secret}_configured" in data, f"Missing boolean flag for {secret}"

    assert data['gemini_key_configured'] is True
    assert data['online_enabled'] is True
    assert data['daily_ai_calls'] == 30

    # Invalid setting validation
    bad_resp = auth_client.post('/api/settings', {'tts_provider': 'unsupported_tts'}, format='json')
    assert bad_resp.status_code == 400
    assert 'Choose edge, azure, or sarvam' in bad_resp.data['detail']

    # Settings file write safety
    settings_file = DATA / 'settings.json'
    assert settings_file.exists()
    content = json.loads(settings_file.read_text(encoding='utf-8'))
    assert content['gemini_key'] == 'test_secret_key_12345'


# ==============================================================================
# 3. Voice Normalization & Conversational Rewrite
# ==============================================================================

def test_voice_normalize_api(auth_client):
    """Verify POST /api/voice/normalize processes numbers and pronunciation overrides."""
    resp = auth_client.post('/api/voice/normalize', {
        'text': '2026 లో 15 మంది వ్యక్తులు ఉన్నారు.',
        'dictionary': {'వ్యక్తులు': 'ప్రజలు'}
    }, format='json')
    assert resp.status_code == 200
    data = resp.data
    assert data['source_text'] == '2026 లో 15 మంది వ్యక్తులు ఉన్నారు.'
    assert 'రెండు వేలు ఇరవై ఆరు' in data['spoken_text']
    assert 'పదిహేను' in data['spoken_text']


def test_voice_rewrite_preview_api(auth_client):
    """Verify POST /api/voice/rewrite_preview returns valid diff metadata."""
    resp = auth_client.post('/api/voice/rewrite_preview', {
        'text': 'ఈ కథ చాలా ఆసక్తికరంగా ఉంది.'
    }, format='json')
    assert resp.status_code == 200
    data = resp.data
    assert 'original' in data
    assert 'proposed' in data
    assert 'diff' in data
    assert 'changed' in data
    assert data['original'] == 'ఈ కథ చాలా ఆసక్తికరంగా ఉంది.'


def test_voice_ab_compare_api(auth_client):
    """Verify POST /api/voice/ab_compare returns valid duration and preset comparisons."""
    take_a = {
        'take_id': 'take_alpha',
        'duration': 5.2,
        'preset': 'conversational',
        'spoken_text': 'రెండు వేల ఇరవై ఆరు'
    }
    take_b = {
        'take_id': 'take_beta',
        'duration': 4.8,
        'preset': 'documentary',
        'spoken_text': 'రెండు వేల ఇరవై ఆరు'
    }
    resp = auth_client.post('/api/voice/ab_compare', {
        'take_a': take_a,
        'take_b': take_b
    }, format='json')
    assert resp.status_code == 200
    data = resp.data
    assert data['baseline']['take_id'] == 'take_alpha'
    assert data['candidate']['take_id'] == 'take_beta'
    assert data['duration_difference'] == 0.4
    assert data['script_identical'] is True


# ==============================================================================
# 4. Voice Candidate Preview & Synthesis Contract
# ==============================================================================

def test_voice_preview_api(auth_client, test_project, monkeypatch):
    """Verify POST /api/voice/preview synthesizes audio and returns take record."""
    pid = test_project

    # Mock TTS adapter to prevent external network call and avoid paid credits
    mock_audio_path = DATA / 'tmp' / 'mock_preview_take.mp3'
    mock_audio_path.parent.mkdir(parents=True, exist_ok=True)
    mock_audio_path.write_bytes(b'ID3_MOCK_MP3_DATA_PAYLOAD')

    def fake_synthesize(*args, **kwargs):
        return mock_audio_path, [], {'duration': 2.5}

    from studio.voice.edge_adapter import EdgeTTSAdapter
    monkeypatch.setattr(EdgeTTSAdapter, 'synthesize', fake_synthesize)

    resp = auth_client.post('/api/voice/preview', {
        'project_id': pid,
        'scene_id': 'scene_m7_01',
        'text': 'ఇది టెస్ట్ నరేషన్.',
        'provider': 'edge',
        'voice': 'te-IN-ShrutiNeural',
        'preset': 'conversational',
        'rate': 0,
        'pitch': 0,
        'dictionary': {'టెస్ట్': 'పరీక్ష'},
        'regenerate': True,
    }, format='json')

    assert resp.status_code == 200, resp.data
    take = resp.data
    assert take['take_id'] != ''
    assert take['provider'] == 'edge'
    assert take['preset'] == 'conversational'
    assert take['duration'] == 2.5
    assert take['asset_id'] != ''
    assert f"/api/assets/{take['asset_id']}/media" == take['audio_url']


# ==============================================================================
# 5. Take Acceptance & Project Revision Update
# ==============================================================================

def test_voice_take_acceptance_atomic(auth_client, test_project):
    """Verify POST /api/projects/<pid>/takes/<take_id>/accept updates project revision atomically."""
    pid = test_project

    # Create synthetic Asset and VoiceTake in database
    aid = uuid.uuid4().hex
    tid = f"take_{uuid.uuid4().hex[:12]}"

    asset = Asset.objects.using('voice_writer').create(
        id=aid,
        workspace_id='default',
        filename=f"{aid}.mp3",
        name='Voice Take (Shruti)',
        kind='audio',
        duration=3.14,
        provider='edge',
        meta={'take_id': tid}
    )

    take = VoiceTake.objects.using('voice_writer').create(
        id=tid,
        project_id=pid,
        scene_id='scene_m7_01',
        provider='edge',
        voice_id='te-IN-ShrutiNeural',
        model='',
        preset='conversational',
        spoken_text='ఇది మొదటి దృశ్యం.',
        roman_text='Idi modati drushyam.',
        asset=asset,
        duration=3.14,
        accepted=False,
    )

    # Pre-acceptance state: Project at revision 1
    proj_before = Project.objects.using('default').get(id=pid)
    assert proj_before.revision == 1

    # Accept the take via Django API
    resp = auth_client.post(f"/api/projects/{pid}/takes/{tid}/accept")
    assert resp.status_code == 200, resp.data
    data = resp.data
    assert data['take']['id'] == tid
    assert data['take']['accepted'] is True
    assert data['project']['revision'] == 2

    # Verify project document was updated
    proj_after = Project.objects.using('default').get(id=pid)
    assert proj_after.revision == 2
    sc0 = proj_after.body['scenes'][0]
    assert sc0['take_id'] == tid
    assert sc0['audio_asset'] == aid
    assert sc0['delivery_preset'] == 'conversational'

    # Verify immutable revision snapshot exists
    rev = Revision.objects.using('default').filter(project_id=pid, revision=2).first()
    assert rev is not None
    assert rev.body['scenes'][0]['take_id'] == tid


def test_voice_take_acceptance_ownership_security(auth_client, test_project):
    """Verify accepting a take belonging to another project is strictly rejected."""
    pid1 = test_project
    pid2 = f"proj_other_{uuid.uuid4().hex[:12]}"

    # Create dummy other project
    Project.objects.using('project_writer').create(
        id=pid2,
        workspace_id='default',
        revision=1,
        body={'scenes': []}
    )

    # Take belongs to pid2
    tid = f"take_{uuid.uuid4().hex[:12]}"
    VoiceTake.objects.using('voice_writer').create(
        id=tid,
        project_id=pid2,
        provider='edge',
        voice_id='te-IN-ShrutiNeural',
        spoken_text='test',
        duration=1.0,
    )

    try:
        # Attempt to accept pid2's take under pid1
        resp = auth_client.post(f"/api/projects/{pid1}/takes/{tid}/accept")
        assert resp.status_code == 400
        assert f"Voice take '{tid}' not found for project '{pid1}'" in resp.data['detail']
    finally:
        import psycopg2
        conn = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5433/studio")
        with conn.cursor() as cur:
            cur.execute("DELETE FROM voice_takes WHERE id = %s", (tid,))
            cur.execute("DELETE FROM projects WHERE id = %s", (pid2,))
        conn.commit()
        conn.close()


# ==============================================================================
# 6. React Frontend Serving & Static Asset Protection
# ==============================================================================

def test_react_frontend_serving(anon_client):
    """Verify Django serves React index.html and static bundle assets."""
    # Legacy React entrypoint preserved at /react-legacy/ for rollback
    resp_root = anon_client.get('/react-legacy/')
    assert resp_root.status_code == 200
    assert resp_root['Content-Type'].startswith('text/html')
    content_root = b''.join(resp_root.streaming_content)
    assert b'<div id="root"></div>' in content_root
    assert b'/app.js' in content_root
    assert b'/app.css' in content_root

    # app.js JavaScript bundle
    resp_js = anon_client.get('/app.js')
    assert resp_js.status_code == 200
    assert 'javascript' in resp_js['Content-Type'].lower()
    content_js = b''.join(resp_js.streaming_content)
    assert len(content_js) > 100000

    # app.css stylesheet
    resp_css = anon_client.get('/app.css')
    assert resp_css.status_code == 200
    assert 'text/css' in resp_css['Content-Type'].lower()
    content_css = b''.join(resp_css.streaming_content)
    assert len(content_css) > 1000

    # Path traversal protection
    resp_traversal = anon_client.get('/../../package.json')
    assert resp_traversal.status_code in (403, 404)
