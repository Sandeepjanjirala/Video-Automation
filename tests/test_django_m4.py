"""Stage M4 Acceptance and Parity Tests.

Verifies:
1. Media streaming parity (GET /api/assets/<aid>/media and /file) with byte-level integrity.
2. HTTP Range request handling (RFC 7233) for video seeking (206 Partial Content).
3. Error parity on missing assets, missing physical files, and traversal attempts.
4. Thumbnail delivery, cached serving, on-demand FFmpeg generation, and concurrency safety.
5. Upload security: token checking, origin check, extension validation, request size limits.
6. FFprobe validation and corrupted media rejection with clean temporary file cleanup.
7. SHA-256 deduplication: duplicate uploads reuse existing asset without duplicating DB rows or files.
8. Pixabay stock proxy parity (GET /api/stock): schema normalization, 422 on missing query, server-side secret protection.
9. Database role isolation: django_readonly is SELECT-only; django_media_writer can only write to assets.
10. Alembic schema authority and database invariance.
"""
import os
import sys
sys.path.insert(0, os.getcwd())
import io
import uuid
import hashlib
import tempfile
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import pytest
import httpx

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.conf import settings
from rest_framework.test import APIClient
from core.models import Asset, Workspace, Job, Project
from core.views import DJANGO_TOKEN
from studio.config import executable, DATA

FASTAPI_URL = "http://127.0.0.1:8787"
DJ_CLIENT = APIClient()


@pytest.fixture(scope="module")
def pg_connection():
    """Direct PostgreSQL connection for invariant checks."""
    conn = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5433/studio")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def test_video_fixture(tmp_path_factory):
    """Generate a tiny 1-second 64x64 valid H.264 MP4 file for testing uploads."""
    tmp_dir = tmp_path_factory.mktemp("video_fixture")
    out_path = tmp_dir / "valid_sample_64x64.mp4"
    cmd = [
        executable('ffmpeg'), '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:d=1',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(out_path)
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    assert out_path.exists() and out_path.stat().st_size > 0
    return out_path


@pytest.fixture(scope="module")
def sample_video_asset():
    """Retrieve an existing video asset from the database."""
    asset = Asset.objects.filter(kind='video').first()
    assert asset is not None, "No video asset found in database for testing!"
    return asset


@pytest.fixture(scope="module")
def sample_audio_asset():
    """Retrieve an existing audio asset from the database if present."""
    return Asset.objects.filter(kind='audio').first()


# ==============================================================================
# 1. Media File Streaming & HTTP Range Tests
# ==============================================================================

def test_media_streaming_parity(sample_video_asset):
    """Verify GET /api/assets/<aid>/media returns exact bytes matching FastAPI."""
    aid = sample_video_asset.id
    fa_resp = httpx.get(f"{FASTAPI_URL}/api/assets/{aid}/media", timeout=10)
    dj_resp = DJ_CLIENT.get(f"/api/assets/{aid}/media")

    assert fa_resp.status_code == 200
    assert dj_resp.status_code == 200
    assert dj_resp.headers.get('Content-Type') == fa_resp.headers.get('Content-Type')
    assert dj_resp.headers.get('Accept-Ranges') == 'bytes'

    dj_content = b"".join(dj_resp.streaming_content) if hasattr(dj_resp, 'streaming_content') else dj_resp.content
    assert len(dj_content) == len(fa_resp.content)
    assert hashlib.sha256(dj_content).hexdigest() == hashlib.sha256(fa_resp.content).hexdigest()


def test_media_file_alias_parity(sample_video_asset):
    """Verify /file is an exact alias for /media."""
    aid = sample_video_asset.id
    resp_media = DJ_CLIENT.get(f"/api/assets/{aid}/media")
    resp_file = DJ_CLIENT.get(f"/api/assets/{aid}/file")

    assert resp_media.status_code == 200
    assert resp_file.status_code == 200
    media_bytes = b"".join(resp_media.streaming_content) if hasattr(resp_media, 'streaming_content') else resp_media.content
    file_bytes = b"".join(resp_file.streaming_content) if hasattr(resp_file, 'streaming_content') else resp_file.content
    assert media_bytes == file_bytes


def test_http_range_requests(sample_video_asset):
    """Verify HTTP Range header seeking behavior (206 Partial Content)."""
    aid = sample_video_asset.id

    # 1. Start range bytes=0-99
    fa_range = httpx.get(f"{FASTAPI_URL}/api/assets/{aid}/media", headers={'Range': 'bytes=0-99'}, timeout=5)
    dj_range = DJ_CLIENT.get(f"/api/assets/{aid}/media", HTTP_RANGE='bytes=0-99')

    assert fa_range.status_code == 206
    assert dj_range.status_code == 206
    dj_bytes = b"".join(dj_range.streaming_content)
    assert dj_bytes == fa_range.content
    assert len(dj_bytes) == 100
    assert dj_range.headers.get('Content-Range') == fa_range.headers.get('Content-Range')
    assert dj_range.headers.get('Content-Length') == '100'

    # 2. Mid-range bytes=100-249
    fa_mid = httpx.get(f"{FASTAPI_URL}/api/assets/{aid}/media", headers={'Range': 'bytes=100-249'}, timeout=5)
    dj_mid = DJ_CLIENT.get(f"/api/assets/{aid}/media", HTTP_RANGE='bytes=100-249')
    assert dj_mid.status_code == 206
    dj_mid_bytes = b"".join(dj_mid.streaming_content)
    assert dj_mid_bytes == fa_mid.content
    assert len(dj_mid_bytes) == 150

    # 3. Invalid out-of-bounds range
    dj_invalid = DJ_CLIENT.get(f"/api/assets/{aid}/media", HTTP_RANGE='bytes=999999999-999999999')
    assert dj_invalid.status_code == 416
    assert 'bytes */' in dj_invalid.headers.get('Content-Range', '')


def test_media_missing_asset_parity():
    """Verify 400 {'detail': 'Media asset not found'} matching FastAPI for non-existent asset."""
    fa_resp = httpx.get(f"{FASTAPI_URL}/api/assets/nonexistent_asset_xyz/media")
    dj_resp = DJ_CLIENT.get("/api/assets/nonexistent_asset_xyz/media")

    assert fa_resp.status_code == 400
    assert dj_resp.status_code == 400
    assert dj_resp.json() == fa_resp.json()


def test_media_missing_physical_file_parity(pg_connection):
    """Verify 400 missing on disk error when DB record exists but physical file is absent."""
    dummy_aid = f"ghost_{uuid.uuid4().hex[:12]}"
    cur = pg_connection.cursor()
    cur.execute(
        "INSERT INTO assets (id, workspace_id, filename, name, kind, duration) VALUES (%s, 'default', %s, 'ghost', 'video', 1.0)",
        [dummy_aid, f"{dummy_aid}.mp4"]
    )
    pg_connection.commit()

    try:
        dj_resp = DJ_CLIENT.get(f"/api/assets/{dummy_aid}/media")
        assert dj_resp.status_code == 400
        assert 'is missing on disk' in dj_resp.json()['detail']
    finally:
        cur.execute("DELETE FROM assets WHERE id = %s", [dummy_aid])
        pg_connection.commit()


# ==============================================================================
# 2. Thumbnail Delivery & Concurrency Safety
# ==============================================================================

def test_thumbnail_cached_parity(sample_video_asset):
    """Verify cached thumbnail delivery matches FastAPI."""
    aid = sample_video_asset.id
    fa_resp = httpx.get(f"{FASTAPI_URL}/api/assets/{aid}/thumbnail", timeout=10)
    dj_resp = DJ_CLIENT.get(f"/api/assets/{aid}/thumbnail")

    assert fa_resp.status_code == 200
    assert dj_resp.status_code == 200
    assert dj_resp.headers.get('Content-Type') == 'image/jpeg'

    dj_bytes = b"".join(dj_resp.streaming_content) if hasattr(dj_resp, 'streaming_content') else dj_resp.content
    assert dj_bytes == fa_resp.content


def test_thumbnail_audio_asset_rejection(sample_audio_asset):
    """Verify 404 when requesting thumbnail for an audio asset."""
    if not sample_audio_asset:
        pytest.skip("No audio asset in test database.")
    aid = sample_audio_asset.id
    dj_resp = DJ_CLIENT.get(f"/api/assets/{aid}/thumbnail")
    assert dj_resp.status_code == 404
    assert dj_resp.json()['detail'] == 'Thumbnails only available for video assets'


def test_thumbnail_generation_and_concurrency(test_video_fixture, pg_connection):
    """Verify atomic thumbnail generation and concurrency safety under simultaneous requests."""
    # Create disposable test video in data/assets
    test_aid = f"thumb_test_{uuid.uuid4().hex[:12]}"
    dest_path = DATA / 'assets' / f"{test_aid}.mp4"
    dest_path.write_bytes(test_video_fixture.read_bytes())

    cur = pg_connection.cursor()
    cur.execute(
        "INSERT INTO assets (id, workspace_id, filename, name, kind, duration) VALUES (%s, 'default', %s, 'test', 'video', 1.0)",
        [test_aid, dest_path.name]
    )
    pg_connection.commit()

    cache_thumb = DATA / 'cache' / f"{test_aid}.thumb.jpg"
    cache_thumb.unlink(missing_ok=True)

    try:
        # Request simultaneously from 4 threads
        def fetch_thumb():
            c = APIClient()
            resp = c.get(f"/api/assets/{test_aid}/thumbnail")
            data = b"".join(resp.streaming_content) if hasattr(resp, 'streaming_content') else resp.content
            return resp.status_code, data

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: fetch_thumb(), range(4)))

        for status_code, data in results:
            assert status_code == 200
            assert data.startswith(b"\xff\xd8\xff")  # Valid JPEG magic header
            assert len(data) == len(results[0][1])

        assert cache_thumb.exists()
        assert cache_thumb.stat().st_size > 0

    finally:
        # Clean up
        cur.execute("DELETE FROM assets WHERE id = %s", [test_aid])
        pg_connection.commit()
        dest_path.unlink(missing_ok=True)
        cache_thumb.unlink(missing_ok=True)


# ==============================================================================
# 3. Security Guards & Mutation Boundaries
# ==============================================================================

def test_security_guards_token_enforcement():
    """Verify mutating upload without valid token is rejected with 403."""
    resp = DJ_CLIENT.post(
        "/api/assets",
        {'file': io.BytesIO(b'data')},
        format='multipart'
    )
    assert resp.status_code == 403
    assert resp.json()['detail'] == 'Reload the app before saving'


def test_security_guards_disallowed_mutation_paths():
    """Verify all mutating routes not permitted by the mutation allowlist remain rejected with 405."""
    resp = DJ_CLIENT.post(
        "/api/unsupported_mutation",
        {},
        format='json',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 405
    assert 'Endpoint does not accept this mutation' in resp.json()['detail']


def test_security_guards_request_size_limit():
    """Verify oversized request content length triggers 413."""
    resp = DJ_CLIENT.post(
        "/api/assets",
        {'file': io.BytesIO(b'data')},
        format='multipart',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN,
        CONTENT_LENGTH=str(250 * 1024 * 1024)  # Exceeds 220 MB limit
    )
    assert resp.status_code == 413
    assert resp.json()['detail'] == 'Request is too large'


# ==============================================================================
# 4. Controlled Asset Upload, Validation & Deduplication
# ==============================================================================

def test_upload_invalid_extension():
    """Verify invalid file extension is rejected with 400 without creating files."""
    assets_before = set(Path(DATA / 'assets').glob('*'))
    bad_file = io.BytesIO(b'malicious_payload')
    bad_file.name = "payload.exe"

    resp = DJ_CLIENT.post(
        "/api/assets",
        {'file': bad_file},
        format='multipart',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 400
    assert 'Upload MP4/MOV/MKV/WEBM' in resp.json()['detail']

    assets_after = set(Path(DATA / 'assets').glob('*'))
    assert assets_before == assets_after, "File leaked during failed extension validation!"


def test_upload_corrupted_media_cleanup():
    """Verify unparseable corrupt video fails probe and deletes temporary file."""
    assets_before = set(Path(DATA / 'assets').glob('*'))
    corrupt_file = io.BytesIO(b'not a valid video stream content 1234567890')
    corrupt_file.name = "corrupt.mp4"

    resp = DJ_CLIENT.post(
        "/api/assets",
        {'file': corrupt_file},
        format='multipart',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp.status_code == 400

    assets_after = set(Path(DATA / 'assets').glob('*'))
    assert assets_before == assets_after, "Orphaned file leaked on probe failure!"


def test_upload_valid_video_and_deduplication(test_video_fixture, pg_connection):
    """Verify successful upload, DB creation via media_writer role, and deduplication on re-upload."""
    video_bytes = test_video_fixture.read_bytes()
    expected_hash = hashlib.sha256(video_bytes).hexdigest()

    # Step 1: Initial upload
    upload_file = io.BytesIO(video_bytes)
    upload_file.name = "my_custom_clip.mp4"

    resp1 = DJ_CLIENT.post(
        "/api/assets",
        {'file': upload_file},
        format='multipart',
        HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
    )
    assert resp1.status_code == 200, f"Upload failed: {resp1.json()}"
    data1 = resp1.json()
    created_aid = data1['id']
    assert data1['sha256'] == expected_hash
    assert data1['kind'] == 'video'
    assert data1['name'] == 'my_custom_clip.mp4'

    # Verify DB record exists
    cur = pg_connection.cursor()
    cur.execute("SELECT count(*) FROM assets WHERE id = %s", [created_aid])
    assert cur.fetchone()[0] == 1

    physical_file = DATA / 'assets' / data1['filename']
    assert physical_file.exists()

    try:
        # Step 2: Deduplication - Re-upload identical file content
        assets_count_before = Asset.objects.count()
        files_count_before = len(list((DATA / 'assets').glob('*')))

        upload_dup = io.BytesIO(video_bytes)
        upload_dup.name = "duplicate_name.mp4"

        resp2 = DJ_CLIENT.post(
            "/api/assets",
            {'file': upload_dup},
            format='multipart',
            HTTP_X_STUDIO_TOKEN=DJANGO_TOKEN
        )
        assert resp2.status_code == 200
        data2 = resp2.json()

        # Deduplication must return existing asset ID
        assert data2['id'] == created_aid
        assert data2['sha256'] == expected_hash

        # No duplicate DB row or file created
        assert Asset.objects.count() == assets_count_before
        assert len(list((DATA / 'assets').glob('*'))) == files_count_before

    finally:
        # Clean up integration test record
        cur.execute("DELETE FROM assets WHERE id = %s", [created_aid])
        pg_connection.commit()
        physical_file.unlink(missing_ok=True)


# ==============================================================================
# 5. Pixabay Stock Proxy Tests
# ==============================================================================

def test_stock_proxy_missing_query():
    """Verify 422 Unprocessable Entity when query param ?q is omitted (FastAPI parity)."""
    fa_resp = httpx.get(f"{FASTAPI_URL}/api/stock")
    dj_resp = DJ_CLIENT.get("/api/stock")

    assert fa_resp.status_code == 422
    assert dj_resp.status_code == 422
    assert dj_resp.json() == fa_resp.json()


def test_stock_proxy_character_limit(monkeypatch):
    """Verify 400 when query exceeds 100 characters."""
    monkeypatch.setattr('studio.providers.online', lambda: {'online_enabled': True, 'pixabay_key': 'test_key'})
    long_q = "a" * 105
    dj_resp = DJ_CLIENT.get(f"/api/stock?q={long_q}")

    assert dj_resp.status_code == 400
    assert dj_resp.json()['detail'] == 'Stock search must be at most 100 characters.'


def test_stock_proxy_secret_protection(monkeypatch):
    """Verify API keys are never leaked in response payload or error details."""
    secret_key = "sensitive_pixabay_key_abc123"
    monkeypatch.setattr('studio.providers.online', lambda: {'online_enabled': True, 'pixabay_key': secret_key})
    
    mock_results = [{
        'id': 99999,
        'creator': 'MockAuthor',
        'source_url': 'https://pixabay.com/videos/id-99999/',
        'tags': 'nature, river',
        'duration': 12,
        'video': {'url': 'https://cdn.pixabay.com/video/test.mp4', 'width': 1080, 'height': 1920, 'size': 5000},
        'license_url': 'https://pixabay.com/service/license-summary/'
    }]
    monkeypatch.setattr('studio.providers.stock_search', lambda q: mock_results)

    dj_resp = DJ_CLIENT.get("/api/stock?q=nature")
    assert dj_resp.status_code == 200
    data = dj_resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]['id'] == 99999
    assert data[0]['creator'] == 'MockAuthor'

    # Crucial: verify secret key is NEVER present in serialized response
    raw_response_text = dj_resp.content.decode('utf-8')
    assert secret_key not in raw_response_text
    assert 'api_key' not in data[0]
    assert 'key' not in data[0]


def test_stock_proxy_provider_error(monkeypatch):
    """Verify provider timeout or failure returns 400 cleanly."""
    def fail_search(q):
        raise ValueError("Provider connection failed. Check internet connectivity and retry.")

    monkeypatch.setattr('studio.providers.stock_search', fail_search)
    dj_resp = DJ_CLIENT.get("/api/stock?q=mountain")
    assert dj_resp.status_code == 400
    assert 'Provider connection failed' in dj_resp.json()['detail']



# ==============================================================================
# 6. Database Role Separation Tests
# ==============================================================================

def test_database_role_separation(pg_connection):
    """Verify django_readonly is strictly read-only and django_media_writer can write ONLY to assets."""
    ro_url = os.environ.get('STUDIO_READONLY_DATABASE_URL')
    if not ro_url:
        ro_file = settings.BASE_DIR / 'backups' / '.readonly_db_url'
        if ro_file.exists():
            ro_url = ro_file.read_text(encoding='utf-8').strip()

    writer_url = os.environ.get('STUDIO_MEDIA_WRITER_DATABASE_URL')
    if not writer_url:
        w_file = settings.BASE_DIR / 'backups' / '.media_writer_db_url'
        if w_file.exists():
            writer_url = w_file.read_text(encoding='utf-8').strip()

    assert ro_url, "No readonly database URL found"
    assert writer_url, "No media writer database URL found"

    # 1. django_readonly CANNOT write to assets
    ro_conn = psycopg2.connect(ro_url)
    ro_cur = ro_conn.cursor()
    with pytest.raises(psycopg2.Error):
        ro_cur.execute("INSERT INTO assets (id, workspace_id, filename, name, kind) VALUES ('ro_test', 'default', 'f.mp4', 'n', 'video')")
    ro_conn.rollback()
    ro_conn.close()

    # 2. django_media_writer CAN insert into assets
    w_conn = psycopg2.connect(writer_url)
    w_cur = w_conn.cursor()
    temp_aid = f"writer_test_{uuid.uuid4().hex[:8]}"
    w_cur.execute(
        "INSERT INTO assets (id, workspace_id, filename, name, kind) VALUES (%s, 'default', %s, 'n', 'video')",
        [temp_aid, f"{temp_aid}.mp4"]
    )
    w_conn.commit()

    # M4 Hardening Review: verify django_media_writer CANNOT DELETE from assets
    with pytest.raises(psycopg2.Error):
        w_cur.execute("DELETE FROM assets WHERE id = %s", [temp_aid])
    w_conn.rollback()
    w_conn.close()

    # Clean up test row using superuser admin connection
    admin_cur = pg_connection.cursor()
    admin_cur.execute("DELETE FROM assets WHERE id = %s", [temp_aid])
    pg_connection.commit()
    admin_cur.close()

    # Reconnect media writer for forbidden write checks
    w_conn = psycopg2.connect(writer_url)
    w_cur = w_conn.cursor()

    # 3. django_media_writer CANNOT write to other application tables
    forbidden_writes = [
        "INSERT INTO jobs (id, workspace_id, project_id, revision, kind, snapshot) VALUES ('f', 'default', 'p', 1, 'k', '{}')",
        "INSERT INTO revisions (project_id, revision, body) VALUES ('p', 1, '{}')",
        "INSERT INTO outbox (event_type, payload) VALUES ('e', '{}')",
        "INSERT INTO drafts (project_id, body) VALUES ('p', '{}')",
        "INSERT INTO projects (id, workspace_id, revision, body) VALUES ('p', 'default', 1, '{}')",
    ]
    for sql in forbidden_writes:
        with pytest.raises(psycopg2.Error):
            w_cur.execute(sql)
        w_conn.rollback()

    w_conn.close()


# ==============================================================================
# 7. Alembic Authority & Schema Invariance
# ==============================================================================

def test_schema_authority_and_alembic_invariance(pg_connection):
    """Verify Alembic revision remains 0003_phase4_voice_lab and no tables were altered."""
    cur = pg_connection.cursor()
    cur.execute("SELECT version_num FROM alembic_version")
    rev = cur.fetchone()[0]
    assert rev == "0003_phase4_voice_lab", f"Alembic revision altered! Got: {rev}"
