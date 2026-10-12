import hashlib
import json
import re
import secrets
import threading
import uuid
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from . import store
from .config import DATA, ROOT, public_settings, save_settings, settings, executable
from .models import Project

store.init()
app = FastAPI(title='Telugu Reel Studio', docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])
TOKEN = secrets.token_urlsafe(32)
SETTINGS_LOCK = threading.Lock()


@app.middleware('http')
async def local_guard(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if request.headers.get('x-studio-token') != TOKEN:
            return JSONResponse({'detail': 'Reload the app before saving'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': 'Cross-origin changes are not allowed'}, status_code=403)
        limit = 220 * 1024 * 1024 if request.url.path == '/api/assets' else 1024 * 1024
        try:
            oversized = int(request.headers.get('content-length', '0')) > limit
        except ValueError:
            oversized = True
        if oversized:
            return JSONResponse({'detail': 'Request is too large'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)


@app.get('/api/bootstrap')
def bootstrap():
    return {'token': TOKEN, 'settings': public_settings(), 'projects': store.list_projects()}


@app.get('/api/health')
def health():
    from .media import run
    result = {}
    for binary in ('ffmpeg', 'ffprobe'):
        try:
            result[binary] = run([executable(binary), '-version'], timeout=10).splitlines()[0]
        except ValueError as exc:
            result[binary] = str(exc)
    return result


@app.post('/api/settings')
def update_settings(body: dict):
    with SETTINGS_LOCK:
        return save_settings(body)


@app.post('/api/projects')
def create(body: Project):
    return store.save_project(body.model_dump())


class SaveBody(BaseModel):
    revision: int
    project: Project


@app.put('/api/projects/{pid}')
def save(pid: str, body: SaveBody):
    return store.save_project(body.project.model_dump(), pid, body.revision)


@app.get('/api/projects/{pid}')
def get(pid: str):
    return store.get_project(pid)


class RevisionBody(BaseModel):
    revision: int


@app.post('/api/projects/{pid}/approve')
def approve(pid: str, body: RevisionBody):
    return store.approve(pid, body.revision)


@app.get('/api/projects/{pid}/history')
def history(pid: str):
    with store.connection() as db:
        return [{'revision': row['revision'], 'created': row['created'], 'project': json.loads(row['body'])}
                for row in db.execute('SELECT * FROM revisions WHERE project_id=? ORDER BY revision DESC LIMIT 50', (pid,))]


class JobBody(BaseModel):
    revision: int
    kind: Literal['research', 'draft', 'voice', 'render', 'stock', 'stock_all']
    quality: Literal['draft', 'final'] = 'final'
    script: str = Field(default='', max_length=20000)
    video_id: int = Field(default=0, ge=0)
    scene_id: str = Field(default='', max_length=48)
    operation_key: Optional[str] = Field(default=None, max_length=128)


@app.post('/api/projects/{pid}/jobs')
def queue(pid: str, body: JobBody):
    options = {'tts_provider': settings()['tts_provider'], 'quality': body.quality,
               'script': body.script, 'video_id': body.video_id, 'scene_id': body.scene_id}
    return store.enqueue(pid, body.revision, body.kind, options, operation_key=body.operation_key)


@app.get('/api/projects/{pid}/jobs')
def jobs(pid: str):
    return store.jobs(pid)


class RetryBody(BaseModel):
    mode: Literal['original', 'current'] = 'original'


@app.post('/api/jobs/{jid}/retry')
def retry(jid: str, mode: str = 'original', body: Optional[RetryBody] = None):
    actual_mode = body.mode if body else mode
    return store.retry(jid, mode=actual_mode)


@app.post('/api/jobs/{jid}/cancel')
def cancel_job(jid: str):
    return store.cancel(jid)


@app.post('/api/projects/{pid}/apply-job/{jid}')
@app.get('/api/projects/{pid}/apply-job/{jid}')
@app.post('/api/projects/{pid}/jobs/{jid}/apply')
@app.get('/api/projects/{pid}/jobs/{jid}/apply')
def apply_job(pid: str, jid: str, force: bool = False):
    return store.apply_job(pid, jid, force=force)


@app.get('/api/projects/{pid}/preflight')
def preflight(pid: str):
    p_row = store.get_project(pid)
    return store.preflight(p_row['project'])


@app.put('/api/projects/{pid}/draft')
def save_draft(pid: str, body: dict):
    return store.save_draft(pid, body)


@app.get('/api/projects/{pid}/draft')
def get_draft(pid: str):
    d = store.get_draft(pid)
    return d or {}


@app.delete('/api/projects/{pid}/draft')
def delete_draft(pid: str):
    store.delete_draft(pid)
    return {'ok': True}


class AssignMediaBody(BaseModel):
    asset_id: str
    scene_ids: list[str]


@app.post('/api/projects/{pid}/assign-media')
def assign_media(pid: str, body: AssignMediaBody):
    proj = store.get_project(pid)
    p_data = proj['project']
    asset_meta = store.asset(body.asset_id)
    scene_id_set = set(body.scene_ids)
    all_scenes = 'all' in scene_id_set
    for s in p_data['scenes']:
        if all_scenes or s['id'] in scene_id_set:
            if asset_meta['kind'] == 'video':
                s['video_asset'] = body.asset_id
                s['visual'] = 'upload'
                s['trim_start'] = 0
                s['rights_reviewed'] = True
            elif asset_meta['kind'] == 'audio':
                s['audio_asset'] = body.asset_id
                s['audio_text_hash'] = store.digest(s['narration'])
    return store.save_project(p_data, pid, proj['revision'])


@app.get('/api/stock')
def stock(q: str):
    from .providers import stock_search
    return stock_search(q)


@app.get('/api/assets')
def list_assets(kind: Optional[str] = None):
    return store.list_assets(kind=kind)


@app.post('/api/assets')
async def upload(file: UploadFile = File(...)):
    from .media import probe
    extension = Path(file.filename or '').suffix.lower()
    if extension not in ('.mp4', '.mov', '.mkv', '.webm', '.mp3', '.wav', '.m4a', '.ogg', '.aac'):
        raise ValueError('Upload MP4/MOV/MKV/WEBM video or MP3/WAV/M4A/OGG/AAC audio')
    aid = uuid.uuid4().hex
    path = DATA / 'assets' / (aid + extension)
    hasher = hashlib.sha256()
    try:
        size = 0
        with path.open('wb') as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 200 * 1024 * 1024:
                    raise ValueError('Upload limit is 200 MB per file')
                out.write(chunk)
                hasher.update(chunk)
        file_hash = hasher.hexdigest()

        # Deduplicate: reuse existing asset if identical content hash exists
        existing = store.find_asset(sha256=file_hash)
        if existing:
            path.unlink(missing_ok=True)
            return existing

        info = await __import__('asyncio').to_thread(probe, path)
        video = any(s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic') for s in info['streams'])
        audio = any(s['codec_type'] == 'audio' for s in info['streams'])
        if not video and not audio:
            raise ValueError('No usable video or audio stream found')
        return store.add_asset({'id': aid, 'filename': path.name, 'name': Path(file.filename or '').name[:180],
                                'kind': 'video' if video else 'audio', 'duration': float(info['format']['duration']),
                                'sha256': file_hash, 'provider': 'upload', 'retrieved': store.now(),
                                'source_url': '', 'license_url': 'User supplied; review required'})
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.get('/api/assets/{aid}')
def asset_meta(aid: str):
    return store.asset(aid)


@app.get('/api/assets/{aid}/media')
@app.get('/api/assets/{aid}/file')
def asset_media(aid: str):
    return FileResponse(store.asset_path(aid))


@app.get('/api/assets/{aid}/thumbnail')
def asset_thumbnail(aid: str):
    meta = store.asset(aid)
    if meta.get('kind') != 'video':
        raise HTTPException(status_code=404, detail='Thumbnails only available for video assets')
    thumb = DATA / 'cache' / f'{aid}.thumb.jpg'
    if not thumb.exists():
        from .media import run
        video_p = store.asset_path(aid, 'video')
        try:
            run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y',
                 '-ss', '0.5', '-i', str(video_p), '-vframes', '1',
                 '-vf', 'scale=270:480:force_original_aspect_ratio=decrease,pad=270:480:(ow-iw)/2:(oh-ih)/2:color=black',
                 '-q:v', '3', str(thumb)], timeout=15)
        except Exception:
            raise HTTPException(status_code=404, detail='Could not generate thumbnail')
    if not thumb.exists():
        raise HTTPException(status_code=404, detail='Thumbnail not found')
    return FileResponse(thumb, media_type='image/jpeg')


@app.get('/api/jobs/{jid}/audio/{scene_id}')
def job_audio(jid: str, scene_id: str):
    job = store.job(jid)
    if job['state'] != 'succeeded' or job['kind'] != 'voice':
        raise ValueError('Voice preview is not ready')
    item = next((v for v in job['result']['voices'] if v['scene_id'] == scene_id), None)
    if not item:
        raise ValueError('Scene audio not found')
    path = Path(item['audio_path']).resolve()
    if path.parent not in ((DATA / 'cache').resolve(), (DATA / 'assets').resolve()):
        raise ValueError('Invalid audio path')
    return FileResponse(path)


@app.get('/api/exports/{jid}/{name}')
def export(jid: str, name: str):
    if not re.fullmatch('[a-f0-9]{32}', jid) or name not in ('reel.mp4', 'captions.srt', 'source-report.json', 'project.json'):
        raise HTTPException(404)
    if store.job(jid)['state'] != 'succeeded':
        raise ValueError('Export is incomplete')
    path = DATA / 'exports' / jid / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=name if name != 'reel.mp4' else None)



# ---------------- Voice Lab (Phase 4) Endpoints ----------------

@app.get('/api/voice/capabilities')
def voice_capabilities():
    from .voice.registry import list_voices
    caps = list_voices()
    return [
        {
            'voice_id': c.voice_id,
            'display_name': c.display_name,
            'provider': c.provider,
            'gender': c.gender,
            'language_code': c.language_code,
            'model': c.model,
            'description': c.description,
            'supported_controls': c.supported_controls,
            'unsupported_controls': c.unsupported_controls,
            'max_characters_per_request': c.max_characters_per_request,
            'audio_formats': c.audio_formats,
            'timing_metadata': c.timing_metadata,
            'estimated_cost_per_1k_chars': c.estimated_cost_per_1k_chars
        }
        for c in caps
    ]


class NormalizeBody(BaseModel):
    text: str
    dictionary: Optional[dict[str, str]] = None


@app.post('/api/voice/normalize')
def voice_normalize(body: NormalizeBody):
    from .voice.normalizer import normalize_narration
    norm = normalize_narration(body.text, pronunciation_dict=body.dictionary)
    return {
        'source_text': norm.source_text,
        'spoken_text': norm.spoken_text,
        'roman_text': norm.roman_text,
        'overrides_applied': norm.overrides_applied
    }


class PreviewBody(BaseModel):
    project_id: str
    scene_id: Optional[str] = ''
    text: str
    provider: str = 'edge'
    voice: str = 'te-IN-ShrutiNeural'
    preset: str = 'conversational'
    rate: int = 0
    pitch: int = 0
    dictionary: Optional[dict[str, str]] = None
    regenerate: bool = False


@app.post('/api/voice/preview')
def voice_preview(body: PreviewBody):
    from .voice.lab import generate_voice_take
    take = generate_voice_take(
        project_id=body.project_id,
        scene_id=body.scene_id or '',
        text=body.text,
        provider=body.provider,
        voice=body.voice,
        delivery_preset=body.preset,
        rate=body.rate,
        pitch=body.pitch,
        pronunciation_dict=body.dictionary,
        explicit_regenerate=body.regenerate
    )
    return take


@app.get('/api/projects/{pid}/takes')
def project_takes(pid: str, scene_id: Optional[str] = None):
    return store.list_voice_takes(pid, scene_id=scene_id)


@app.post('/api/projects/{pid}/takes/{take_id}/accept')
def accept_take(pid: str, take_id: str):
    return store.accept_voice_take(pid, take_id)


class ABCompareBody(BaseModel):
    take_a: dict
    take_b: dict


@app.post('/api/voice/ab_compare')
def voice_ab_compare(body: ABCompareBody):
    from .voice.lab import compare_takes_ab
    return compare_takes_ab(body.take_a, body.take_b)


class RewritePreviewBody(BaseModel):
    text: str


@app.post('/api/voice/rewrite_preview')
def voice_rewrite_preview(body: RewritePreviewBody):
    from .voice.lab import preview_conversational_rewrite
    return preview_conversational_rewrite(body.text)


class VoiceLockBody(BaseModel):
    locked: bool


@app.post('/api/projects/{pid}/voice_lock')
def set_voice_lock(pid: str, body: VoiceLockBody):
    proj = store.get_project(pid)
    p_data = proj['project']
    p_data['voice_locked'] = body.locked
    return store.save_project(p_data, pid, proj['revision'])


class DictUpdateBody(BaseModel):
    dictionary: dict[str, str]


@app.post('/api/projects/{pid}/pronunciation_dict')
def update_pronunciation_dict(pid: str, body: DictUpdateBody):
    proj = store.get_project(pid)
    p_data = proj['project']
    p_data['pronunciation_dict'] = body.dictionary
    return store.save_project(p_data, pid, proj['revision'])


app.mount('/', StaticFiles(directory=ROOT / 'studio' / 'static', html=True), name='ui')
