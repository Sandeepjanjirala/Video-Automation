"""PostgreSQL primary store implementation using Django ORM (Phase M9).
Active transactional persistence layer for projects, revisions, jobs, and outbox.
"""
import copy
import hashlib
import json
import os
import signal
import subprocess
import shutil
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.conf import settings
from django.db import connection, connections, transaction
from django.db.models import Q
from core.models import (
    Workspace, Project, Revision, Asset, Job, JobStage, Draft,
    ApplicationReceipt, OutboxEvent, VoiceTake
)
from .config import DATA, setup
from .framing import segment
from .models import Project as PydanticProject

# Default writer alias under least privilege; falls back to 'default'
DB_ALIAS = 'job_writer' if 'job_writer' in settings.DATABASES else 'default'
PROJECT_DB = 'project_writer' if 'project_writer' in settings.DATABASES else 'default'
MEDIA_DB = 'media_writer' if 'media_writer' in settings.DATABASES else 'default'
VOICE_DB = 'voice_writer' if 'voice_writer' in settings.DATABASES else 'default'

# Each execution carries its fencing token through all nested media/provider calls.
execution_token = ContextVar('execution_token', default=None)


class FencingError(RuntimeError):
    """Execution was cancelled, expired, or replaced by another worker."""


def check_execution(j, token=None):
    token = token or execution_token.get()
    if j.cancelled or j.state == 'cancelled':
        raise FencingError('Job was cancelled')
    if token and (j.state != 'running' or j.lease_token != token or
                  not j.lease_expires_at or j.lease_expires_at <= datetime.now(timezone.utc)):
        raise FencingError('Worker no longer owns this job')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def compute_script_hash(project_dict: Dict[str, Any]) -> str:
    scenes = project_dict.get('scenes', [])
    content = [{'narration': s.get('narration', ''), 'roman': s.get('roman', '')} for s in scenes]
    content.append({'notes': project_dict.get('evidence_notes', '')})
    return digest(content)


def invalidate_stale_scene_fields(old_data, new_data):
    old_scenes = {s['id']: s for s in old_data.get('scenes', [])}
    voice_changed = any(old_data.get(f) != new_data.get(f) for f in
                        ('tts_provider', 'voice', 'speech_rate', 'delivery_preset', 'pronunciation_dict'))
    for scene in new_data.get('scenes', []):
        old = old_scenes.get(scene['id'])
        if not old:
            continue
        speech_changed = voice_changed or any(old.get(f) != scene.get(f) for f in
                                              ('narration', 'spoken_text', 'delivery_preset'))
        # Accepted synthetic takes are attachments too. Never keep an old engine's
        # recording after a voice upgrade. Human uploads remain explicitly attached.
        if speech_changed and old.get('take_id') and scene.get('audio_asset') == old.get('audio_asset'):
            take = get_voice_take(old['take_id'])
            if take and take.get('provider') != 'upload':
                scene.update(audio_asset='', audio_text_hash='', take_id='')
        if speech_changed and scene.get('cues') == old.get('cues'):
            scene['cues'] = []
        if old.get('narration') != scene.get('narration'):
            for field in ('spoken_text', 'take_id'):
                if scene.get(field) == old.get(field):
                    scene[field] = ''
        if any(old.get(f) != scene.get(f) for f in ('narration', 'roman')) and scene.get('cues') == old.get('cues'):
            scene['cues'] = []


def init() -> None:
    """Validate PostgreSQL connectivity and ensure default workspace exists."""
    setup()
    ws = Workspace.objects.using(DB_ALIAS).filter(id='default').first()
    if not ws:
        Workspace.objects.using(DB_ALIAS).create(id='default', name='Local Workspace')


def unpack(p: Project) -> Dict[str, Any]:
    """Convert Project ORM model to dictionary expected by API and frontend."""
    updated_str = p.updated_at.isoformat() if p.updated_at else now()
    return {
        'id': p.id,
        'revision': p.revision,
        'approved': p.approved_revision == p.revision,
        'updated': updated_str,
        'project': p.body
    }


def get_project(pid: str) -> Dict[str, Any]:
    p = Project.objects.using(PROJECT_DB).filter(id=pid).first()
    if not p:
        raise ValueError('Project not found')
    return unpack(p)


def list_projects(workspace_id: str = 'default') -> List[Dict[str, Any]]:
    results = Project.objects.using(PROJECT_DB).filter(workspace_id=workspace_id).order_by('-updated_at')
    return [unpack(p) for p in results]


def save_project(project: Dict[str, Any], pid: Optional[str] = None, expected: Optional[int] = None) -> Dict[str, Any]:
    data = PydanticProject.model_validate(project).model_dump()
    pid = pid or uuid.uuid4().hex

    with transaction.atomic(using=PROJECT_DB):
        old = Project.objects.using(PROJECT_DB).select_for_update().filter(id=pid).first()

        if old and expected is not None and old.revision != expected:
            raise ValueError('This project changed in another tab. Reload before saving.')

        now_dt = datetime.now(timezone.utc)
        if old:
            old_data = old.body
            invalidate_stale_scene_fields(old_data, data)
            if compute_script_hash(old_data) == compute_script_hash(data):
                data['script_approved_hash'] = old_data.get('script_approved_hash', '')
            if old_data == data:
                return unpack(old)
            revision = old.revision + 1
            old.revision = revision
            old.body = data
            old.approved_revision = None
            old.updated_at = now_dt
            old.save(using=PROJECT_DB)
            target_p = old
        else:
            revision = 1
            target_p = Project.objects.using(PROJECT_DB).create(
                id=pid,
                workspace_id='default',
                revision=revision,
                approved_revision=None,
                body=data,
                created_at=now_dt,
                updated_at=now_dt
            )

        # Record revision history
        Revision.objects.using(PROJECT_DB).create(
            project_id=pid,
            revision=revision,
            body=data,
            created_at=now_dt
        )

        # Clear autosave draft once project is explicitly saved
        Draft.objects.using(PROJECT_DB).filter(project_id=pid).delete()
        return unpack(target_p)


def reserve_ai_call(limit: int) -> None:
    day = now()[:10]
    from .ratelimit import _get_db_conn
    with _get_db_conn().cursor() as cursor:
        cursor.execute("""
            INSERT INTO usage (day, provider, calls) VALUES (%s, 'gemini', 1)
            ON CONFLICT (day, provider) DO UPDATE SET calls = usage.calls + 1
            WHERE usage.calls < %s RETURNING calls
        """, (day, limit))
        if cursor.fetchone() is None:
            raise ValueError('Daily Gemini request limit reached. Change Settings or try tomorrow (UTC).')


def save_draft(pid: str, draft_data: Dict[str, Any]) -> Dict[str, Any]:
    now_dt = datetime.now(timezone.utc)
    with transaction.atomic(using=PROJECT_DB):
        d = Draft.objects.using(PROJECT_DB).filter(project_id=pid).first()
        if not d:
            Draft.objects.using(PROJECT_DB).create(project_id=pid, body=draft_data, updated_at=now_dt)
        else:
            d.body = draft_data
            d.updated_at = now_dt
            d.save(using=PROJECT_DB)
        return {'project_id': pid, 'updated': now_dt.isoformat()}


def get_draft(pid: str) -> Optional[Dict[str, Any]]:
    d = Draft.objects.using(PROJECT_DB).filter(project_id=pid).first()
    if not d:
        return None
    return {'project_id': pid, 'body': d.body, 'updated': d.updated_at.isoformat()}


def delete_draft(pid: str) -> None:
    Draft.objects.using(PROJECT_DB).filter(project_id=pid).delete()


def approve(pid: str, revision: int) -> Dict[str, Any]:
    with transaction.atomic(using=PROJECT_DB):
        p = Project.objects.using(PROJECT_DB).select_for_update().filter(id=pid).first()
        if not p or p.revision != revision:
            raise ValueError('Save and reload the current revision before approving')

        model = PydanticProject.model_validate(p.body)
        if not model.scenes or any(not s.narration.strip() or not s.roman.strip() for s in model.scenes):
            raise ValueError('Every scene needs Telugu narration text and Roman Telugu captions')
        if not model.evidence_notes.strip():
            raise ValueError('Add your factual review notes, or label fictional content, before approval')

        p_dict = model.model_dump()
        p_dict['script_approved_hash'] = compute_script_hash(p_dict)
        p.body = p_dict
        p.approved_revision = revision
        p.updated_at = datetime.now(timezone.utc)
        p.save(using=PROJECT_DB)
        return unpack(p)


def revisions(pid: str) -> List[Dict[str, Any]]:
    revs = Revision.objects.using(PROJECT_DB).filter(project_id=pid).order_by('-revision')
    return [{'revision': r.revision, 'project': r.body, 'created': r.created_at.isoformat()} for r in revs]


def asset(aid: str) -> Dict[str, Any]:
    a = Asset.objects.using(MEDIA_DB).filter(id=aid).first()
    if not a:
        raise ValueError('Asset not found')
    meta = dict(a.meta or {})
    meta.update({
        'id': a.id,
        'filename': a.filename,
        'name': a.name,
        'kind': a.kind,
        'duration': a.duration,
        'sha256': a.sha256,
        'provider': a.provider,
        'external_id': a.external_id,
        'source_url': a.source_url or '',
        'license_url': a.license_url or '',
        'created_at': a.created_at.isoformat() if a.created_at else now()
    })
    return meta


def add_asset(body: Dict[str, Any]) -> Dict[str, Any]:
    aid = body['id']
    sha = body.get('sha256')
    prov = body.get('provider') or 'upload'
    ext_id = str(body.get('external_id') or '') or None

    with transaction.atomic(using=MEDIA_DB):
        existing = Asset.objects.using(MEDIA_DB).filter(id=aid).first()
        if existing:
            existing.filename = body.get('filename') or existing.filename
            existing.name = body.get('name') or existing.name
            existing.kind = body.get('kind') or existing.kind
            existing.duration = float(body.get('duration', 0.0))
            existing.sha256 = sha
            existing.provider = prov
            existing.external_id = ext_id
            existing.source_url = body.get('source_url', '')
            existing.license_url = body.get('license_url', '')
            existing.meta = body
            existing.save(using=MEDIA_DB)
        else:
            Asset.objects.using(MEDIA_DB).create(
                id=aid,
                workspace_id='default',
                filename=body.get('filename') or f"{aid}.mp4",
                name=body.get('name') or aid,
                kind=body.get('kind', 'video'),
                duration=float(body.get('duration', 0.0)),
                sha256=sha,
                provider=prov,
                external_id=ext_id,
                source_url=body.get('source_url', ''),
                license_url=body.get('license_url', ''),
                meta=body,
                created_at=datetime.now(timezone.utc)
            )
    return body


def find_asset(sha256: Optional[str] = None, provider: Optional[str] = None, external_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    qs = Asset.objects.using(MEDIA_DB)
    if sha256:
        a = qs.filter(sha256=sha256).first()
    elif provider and external_id:
        a = qs.filter(provider=provider, external_id=str(external_id)).first()
    else:
        return None
    if not a:
        return None
    return asset(a.id)


def list_assets(kind: Optional[str] = None, workspace_id: str = 'default') -> List[Dict[str, Any]]:
    qs = Asset.objects.using(MEDIA_DB).filter(workspace_id=workspace_id)
    if kind:
        qs = qs.filter(kind=kind)
    return [asset(a.id) for a in qs.order_by('-created_at')]


def asset_path(aid: str, kind: Optional[str] = None) -> Path:
    if not aid:
        raise ValueError('No media asset ID specified')
    meta = asset(aid)
    if kind and meta['kind'] != kind:
        raise ValueError(f'This asset is not {kind}')
    path = (DATA / 'assets' / meta['filename']).resolve()
    if path.parent != (DATA / 'assets').resolve() or not path.is_file():
        raise ValueError(f'Media file {meta.get("name") or aid} is missing on disk')
    return path


def preflight(project_data: Any) -> Dict[str, Any]:
    if isinstance(project_data, str):
        project_data = json.loads(project_data)
    errors = []
    warnings = []
    scenes = project_data.get('scenes', [])
    if not scenes:
        errors.append({'scene_index': 0, 'scene_id': '', 'field': 'scenes', 'message': 'Add at least one scene before rendering'})

    for i, s in enumerate(scenes):
        idx = i + 1
        sid = s.get('id', f'scene_{idx}')
        if not s.get('narration', '').strip():
            errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'narration', 'message': f'Scene {idx}: narration is empty'})
        if not s.get('roman', '').strip() and project_data.get('captions_enabled', True):
            errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'roman', 'message': f'Scene {idx}: Roman caption text is empty'})
        if s.get('narration_source_roman') and s['narration_source_roman'] != s.get('roman'):
            errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'narration', 'message': f'Scene {idx}: Roman source changed; review and apply Telugu narration again'})
        vis = s.get('visual', 'graphic')
        if vis in ('upload', 'stock', 'image') and not (s.get('video_asset') or s.get('asset_id')):
            errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'video_asset', 'message': f'Scene {idx}: select or upload a video clip'})
        if not s.get('rights_reviewed', False):
            errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'rights_reviewed', 'message': f'Scene {idx}: review rights before rendering'})
        for field, kind in (('video_asset', 'video'), ('audio_asset', 'audio')):
            if not s.get(field) or (field == 'video_asset' and vis == 'graphic'):
                continue
            try:
                asset_path(s[field], 'image' if field == 'video_asset' and vis == 'image' else kind)
                if field == 'audio_asset' and s.get('audio_text_hash') != digest(s.get('narration', '')):
                    raise ValueError('Narration recording no longer matches the script; re-attach it or generate a new take')
                if field == 'video_asset' and vis != 'image':
                    clip_seconds = asset(s[field]).get('duration', 0)
                    segment(s, clip_seconds)
                    if s.get('trim_end', 0) > clip_seconds + 0.05:
                        warnings.append({'scene_index': idx, 'scene_id': sid, 'field': 'trim_end',
                                         'message': f"Scene {idx}: trim end is past the clip's end ({clip_seconds:.2f} s); the clip's end is used"})
            except ValueError as exc:
                errors.append({'scene_index': idx, 'scene_id': sid, 'field': field, 'message': f'Scene {idx}: {exc}'})
        if vis in ('upload', 'stock'):
            for shot in s.get('extra_shots') or []:
                try:
                    asset_path(shot, 'video')
                except ValueError as exc:
                    errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'extra_shots',
                                   'message': f'Scene {idx}: an extra shot is unavailable ({exc}); remove the extra shots'})

    if project_data.get('music_asset'):
        if not project_data.get('music_rights_reviewed'):
            errors.append({'field': 'music_rights_reviewed', 'message': 'Review music usage rights before rendering'})
        try:
            asset_path(project_data['music_asset'], 'audio')
        except ValueError as exc:
            errors.append({'field': 'music_asset', 'message': str(exc)})

    return {'ready': len(errors) == 0, 'errors': errors, 'warnings': warnings}


def get_database_url() -> str:
    return os.environ.get('STUDIO_DATABASE_URL', 'postgresql://postgres:postgres@127.0.0.1:5433/studio')


def job_to_dict(j: Job) -> Dict[str, Any]:
    return {
        'id': j.id,
        'project_id': j.project_id,
        'revision': j.revision,
        'kind': j.kind,
        'snapshot': j.snapshot,
        'state': j.state,
        'stage': getattr(j, 'stage', 'queued') or 'queued',
        'progress': j.progress,
        'result': j.result or {},
        'operation_key': getattr(j, 'operation_key', None),
        'lease_token': getattr(j, 'lease_token', None),
        'lease_expires_at': j.lease_expires_at.isoformat() if getattr(j, 'lease_expires_at', None) else None,
        'cancelled': bool(getattr(j, 'cancelled', False)),
        'created': j.created_at.isoformat() if j.created_at else now(),
        'updated': j.updated_at.isoformat() if j.updated_at else now(),
        'applied': bool(j.applied),
        'applied_at': j.applied_at.isoformat() if j.applied_at else None
    }


def enqueue(
    pid: str,
    revision: int,
    kind: str,
    options: Optional[Dict[str, Any]] = None,
    operation_key: Optional[str] = None
) -> Dict[str, Any]:
    with transaction.atomic(using=DB_ALIAS):
        lock_enqueue(pid, operation_key, DB_ALIAS)
        p = Project.objects.using(DB_ALIAS).filter(id=pid).first()
        if not p:
            raise ValueError('Project not found')
        if p.revision != revision:
            raise ValueError('This project changed in another tab. Reload before enqueueing.')

        if operation_key:
            existing = Job.objects.using(DB_ALIAS).filter(operation_key=operation_key).first()
            if existing:
                if existing.project_id != pid or existing.kind != kind or existing.revision != revision:
                    raise ValueError('Operation key belongs to another request')
                return job_to_dict(existing)

        # Prevent duplicate pending job
        existing_pending = Job.objects.using(DB_ALIAS).filter(
            project_id=pid,
            kind=kind,
            state__in=['queued', 'running']
        ).first()
        if existing_pending:
            raise ValueError(f'A job for this project is already in progress ({existing_pending.kind}).')

        if kind == 'render':
            if p.approved_revision != revision:
                raise ValueError('Approve this revision before rendering.')
            pref = preflight(p.body)
            if not pref['ready']:
                msg = '; '.join(e['message'] for e in pref['errors'])
                raise ValueError(f'Cannot queue render: {msg}')

        now_dt = datetime.now(timezone.utc)
        jid = uuid.uuid4().hex
        options = {**(options or {}), 'tts_provider': p.body.get('tts_provider') or 'edge'}
        snap = {
            'project': p.body,
            'options': options or {},
            'enqueued_at': now_dt.isoformat()
        }

        new_job = Job.objects.using(DB_ALIAS).create(
            id=jid,
            workspace_id=p.workspace_id,
            project_id=pid,
            revision=revision,
            kind=kind,
            state='queued',
            stage='queued',
            progress='Queued',
            snapshot=snap,
            result={},
            applied=False,
            operation_key=operation_key,
            created_at=now_dt,
            updated_at=now_dt
        )

        JobStage.objects.using(DB_ALIAS).create(
            job=new_job,
            attempt=1,
            stage='queued',
            state='queued',
            details={},
            created_at=now_dt,
            updated_at=now_dt
        )

        OutboxEvent.objects.using(DB_ALIAS).create(
            event_type='job.enqueued',
            payload={
                'job_id': jid,
                'project_id': pid,
                'revision': revision,
                'kind': kind,
                'options': options or {},
                'operation_key': operation_key
            },
            processed=False,
            attempts=0,
            created_at=now_dt
        )
        return job_to_dict(new_job)


def lock_enqueue(pid, operation_key=None, using=DB_ALIAS):
    """Serialize queue decisions without granting the job role UPDATE on projects."""
    with connections[using].cursor() as cursor:
        if operation_key:
            cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))', ('studio-operation', operation_key))
        cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))', ('studio-enqueue', pid))


def job(jid: str) -> Dict[str, Any]:
    j = Job.objects.using(DB_ALIAS).filter(id=jid).first()
    if not j:
        raise ValueError('Job not found')
    return job_to_dict(j)


def jobs(pid: str) -> List[Dict[str, Any]]:
    jlist = Job.objects.using(DB_ALIAS).filter(project_id=pid).order_by('-created_at')[:30]
    return [job_to_dict(j) for j in jlist]


def progress(
    jid: str,
    message: str,
    state: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
    stage_name: Optional[str] = None,
    billable_units: int = 0,
    ambiguous: bool = False,
    lease_token: Optional[str] = None
) -> None:
    with transaction.atomic(using=DB_ALIAS):
        j = Job.objects.using(DB_ALIAS).select_for_update().filter(id=jid).first()
        if not j:
            return
        check_execution(j, lease_token)
        if j.state in ('succeeded', 'failed'):
            return
        now_dt = datetime.now(timezone.utc)
        j.progress = message
        j.updated_at = now_dt
        if stage_name:
            j.stage = stage_name
        if state:
            j.state = state
            if result is not None:
                j.result = result
            JobStage.objects.using(DB_ALIAS).create(
                job=j,
                attempt=j.attempt_count or 1,
                stage=stage_name or ('complete' if state in ('succeeded', 'failed') else 'running'),
                state=state,
                details=result or {},
                billable_units=billable_units,
                ambiguous_outcome=ambiguous,
                created_at=now_dt,
                updated_at=now_dt
            )
        j.save(using=DB_ALIAS)


def claim() -> Optional[Dict[str, Any]]:
    """Claim a queued job using PostgreSQL SKIP LOCKED to prevent concurrency contention."""
    with transaction.atomic(using=DB_ALIAS):
        j = Job.objects.using(DB_ALIAS).select_for_update(skip_locked=True).filter(state='queued').order_by('created_at').first()
        if not j:
            return None
        now_dt = datetime.now(timezone.utc)
        j.state = 'running'
        j.stage = 'running'
        j.updated_at = now_dt
        j.attempt_count = (j.attempt_count or 0) + 1
        j.save(using=DB_ALIAS)
        return job_to_dict(j)


def publish_export(jid: str, directory: Path, result: Dict[str, Any]) -> None:
    """Publish only while owning the job; downloads stay hidden until commit.

    Keep the job locked across publication and completion so an expired attempt
    cannot overwrite another attempt's finished files.
    """
    with transaction.atomic(using=DB_ALIAS):
        j = Job.objects.using(DB_ALIAS).select_for_update().get(id=jid)
        check_execution(j)
        destination = DATA / 'exports' / jid
        destination.mkdir(parents=True, exist_ok=True)
        for name in ('reel.mp4', 'captions.srt', 'source-report.json', 'project.json'):
            shutil.copyfile(directory / name, destination / name)
        if 'timing-manifest.json' in result.get('files', []):
            shutil.copyfile(directory / 'timing-manifest.json', destination / 'timing-manifest.json')
        progress(jid, 'Complete', 'succeeded', result, stage_name='validation')


def retry(jid: str, mode: str = 'original') -> Dict[str, Any]:
    with transaction.atomic(using=DB_ALIAS):
        j = Job.objects.using(DB_ALIAS).select_for_update().filter(id=jid).first()
        if not j:
            raise ValueError('Job not found')
        if j.state != 'failed':
            raise ValueError('Only failed jobs can be retried')

        now_dt = datetime.now(timezone.utc)
        if mode == 'current':
            p = Project.objects.using(PROJECT_DB).filter(id=j.project_id).first()
            if not p:
                raise ValueError('Project not found')
            if j.kind in ('voice', 'render') and p.approved_revision != p.revision:
                raise ValueError('Approve this exact script revision first')
            snap = dict(j.snapshot)
            snap['project'] = p.body
            snap['options'] = {**snap.get('options', {}), 'tts_provider': p.body.get('tts_provider', 'edge')}
            j.snapshot = snap
            j.revision = p.revision
            j.progress = 'Retry queued (current project)'
        else:
            j.progress = 'Retry queued (original inputs)'

        j.state = 'queued'
        j.stage = 'queued'
        j.result = {}
        j.cancelled = False
        j.cancelled_at = None
        j.lease_token = None
        j.lease_expires_at = None
        j.updated_at = now_dt
        j.attempt_count = (j.attempt_count or 1) + 1
        j.save(using=DB_ALIAS)

        JobStage.objects.using(DB_ALIAS).create(
            job=j,
            attempt=j.attempt_count,
            stage=f'retry_{mode}',
            state='queued',
            details={'mode': mode},
            created_at=now_dt,
            updated_at=now_dt
        )

        OutboxEvent.objects.using(DB_ALIAS).create(
            event_type='job.enqueued',
            payload={
                'job_id': jid,
                'project_id': j.project_id,
                'revision': j.revision,
                'kind': j.kind,
                'options': j.snapshot.get('options', {}),
            },
            processed=False,
            attempts=0,
            created_at=now_dt
        )
        return job_to_dict(j)


def acquire_lease(jid: str, worker_id: str, duration_seconds: int = 60) -> Optional[str]:
    """Acquire or extend execution lease for a job."""
    with transaction.atomic(using=DB_ALIAS):
        j = Job.objects.using(DB_ALIAS).select_for_update().filter(id=jid).first()
        if not j or j.cancelled or j.state not in ('queued', 'running'):
            return None
        now_dt = datetime.now(timezone.utc)
        if j.state == 'running' and j.lease_token:
            if j.lease_expires_at and j.lease_expires_at > now_dt:
                return None  # Still owned by an active worker
        j.state = 'running'
        j.lease_token = uuid.uuid4().hex
        j.lease_expires_at = now_dt + timedelta(seconds=duration_seconds)
        j.heartbeat_at = now_dt
        j.updated_at = now_dt
        j.save(using=DB_ALIAS)
        return j.lease_token


def renew_lease(jid: str, lease_token: str, extension_seconds: int = 60) -> bool:
    """Heartbeat lease renewal during long-running worker tasks."""
    with transaction.atomic(using=DB_ALIAS):
        j = Job.objects.using(DB_ALIAS).select_for_update().filter(id=jid).first()
        if (not j or j.cancelled or j.state != 'running' or j.lease_token != lease_token
                or not j.lease_expires_at or j.lease_expires_at <= datetime.now(timezone.utc)):
            return False
        now_dt = datetime.now(timezone.utc)
        j.lease_expires_at = now_dt + timedelta(seconds=extension_seconds)
        j.heartbeat_at = now_dt
        j.updated_at = now_dt
        j.save(using=DB_ALIAS)
        return True


def verify_lease_and_not_cancelled(jid: str, lease_token: str) -> bool:
    """Verify that caller holds the active unexpired lease and job is not cancelled."""
    j = Job.objects.using(DB_ALIAS).filter(id=jid).first()
    if not j or j.cancelled or j.state != 'running' or j.lease_token != lease_token or not j.lease_expires_at:
        return False
    now_dt = datetime.now(timezone.utc)
    if j.lease_expires_at and j.lease_expires_at < now_dt:
        return False
    return True


def register_subprocess(jid: str, pid: int) -> None:
    """Associate active OS subprocess PID with the job."""
    with transaction.atomic(using=DB_ALIAS):
        j = Job.objects.using(DB_ALIAS).select_for_update().get(id=jid)
        check_execution(j)
        j.subprocess_pid = pid
        j.save(using=DB_ALIAS, update_fields=['subprocess_pid'])


def unregister_subprocess(jid: str, pid: Optional[int] = None) -> None:
    """Clear subprocess PID when process finishes."""
    query = Job.objects.using(DB_ALIAS).filter(id=jid)
    if pid is not None:
        query = query.filter(subprocess_pid=pid)
    query.update(subprocess_pid=None)


def terminate_subprocess_tree(pid: int) -> None:
    """Terminate only the specific job's subprocess tree."""
    if not pid:
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], capture_output=True, timeout=5)
        else:
            try:
                import psutil
                parent = psutil.Process(pid)
                for child in parent.children(recursive=True):
                    child.kill()
                parent.kill()
            except Exception:
                try:
                    # Never kill a shared process group containing the API/worker.
                    if os.getpgid(pid) == pid:
                        os.killpg(pid, signal.SIGKILL)
                    else:
                        os.kill(pid, signal.SIGKILL)
                except Exception:
                    os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def cancel(jid: str) -> Dict[str, Any]:
    """Persist job cancellation and stop only the job's active subprocess tree."""
    with transaction.atomic(using=DB_ALIAS):
        j = Job.objects.using(DB_ALIAS).select_for_update().filter(id=jid).first()
        if not j:
            raise ValueError('Job not found')
        if j.state in ('succeeded', 'cancelled'):
            return job_to_dict(j)
        now_dt = datetime.now(timezone.utc)
        j.cancelled = True
        j.cancelled_at = now_dt
        j.state = 'cancelled'
        j.progress = 'Cancelled by user'
        j.updated_at = now_dt
        sub_pid = j.subprocess_pid
        j.subprocess_pid = None
        j.save(using=DB_ALIAS)

        if sub_pid:
            terminate_subprocess_tree(sub_pid)

        return job_to_dict(j)


def recover_expired_leases() -> int:
    """Recover ONLY expired ownerships; never reset running jobs with active leases."""
    now_dt = datetime.now(timezone.utc)
    count = 0
    with transaction.atomic(using=DB_ALIAS):
        expired_jobs = list(
            Job.objects.using(DB_ALIAS).select_for_update()
            .filter(state='running', lease_expires_at__isnull=False, lease_expires_at__lt=now_dt)
        )
        for j in expired_jobs:
            j.state = 'failed'
            j.error_message = 'Worker execution lease expired without renewal'
            j.progress = 'Worker lost ownership or crashed. Retry to resume.'
            j.lease_token = None
            j.lease_expires_at = None
            j.updated_at = now_dt
            j.save(using=DB_ALIAS)
            count += 1
    return count


def apply_job(pid: str, jid: str, force: bool = False) -> Dict[str, Any]:
    from core.services import apply_job_result
    return apply_job_result(pid, jid, force=force)


def unpack_voice_take(vt: VoiceTake) -> Dict[str, Any]:
    return {
        'id': vt.id,
        'take_id': vt.id,
        'project_id': vt.project_id,
        'scene_id': vt.scene_id,
        'provider': vt.provider,
        'voice_id': vt.voice_id,
        'model': vt.model,
        'preset': vt.preset,
        'spoken_text': vt.spoken_text,
        'roman_text': vt.roman_text,
        'asset_id': vt.asset_id,
        'audio_url': f'/api/assets/{vt.asset_id}/media' if vt.asset_id else '',
        'duration': vt.duration,
        'timing_data': vt.timing_data or [],
        'accepted': vt.accepted,
        'contract_key': vt.contract_key,
        'meta': vt.meta or {},
        'created_at': vt.created_at.isoformat() if vt.created_at else now()
    }


def add_voice_take(
    project_id: str,
    scene_id: Optional[str],
    provider: str,
    voice_id: str,
    spoken_text: str,
    duration: float,
    timing_data: Optional[List[Any]] = None,
    roman_text: Optional[str] = '',
    asset_id: Optional[str] = None,
    model: Optional[str] = None,
    preset: str = 'conversational',
    contract_key: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    accepted: bool = False,
    take_id: Optional[str] = None,
    session: Optional[Any] = None
) -> Dict[str, Any]:
    tid = take_id or f'take_{uuid.uuid4().hex[:12]}'
    take = VoiceTake.objects.using(VOICE_DB).create(
        id=tid,
        project_id=project_id,
        scene_id=scene_id or None,
        provider=provider,
        voice_id=voice_id,
        model=model,
        preset=preset,
        spoken_text=spoken_text,
        roman_text=roman_text or '',
        asset_id=asset_id,
        duration=duration,
        timing_data=timing_data or [],
        accepted=accepted,
        contract_key=contract_key,
        meta=meta or {}
    )
    return unpack_voice_take(take)


def get_voice_take(take_id: str, session: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    vt = VoiceTake.objects.using(VOICE_DB).filter(id=take_id).first()
    return unpack_voice_take(vt) if vt else None


def list_voice_takes(project_id: str, scene_id: Optional[str] = None, session: Optional[Any] = None) -> List[Dict[str, Any]]:
    qs = VoiceTake.objects.using(VOICE_DB).filter(project_id=project_id)
    if scene_id:
        qs = qs.filter(scene_id=scene_id)
    return [unpack_voice_take(t) for t in qs.order_by('-created_at')]


def accept_voice_take(project_id: str, take_id: str, session: Optional[Any] = None) -> Dict[str, Any]:
    from core.services import accept_voice_take_mutation
    return accept_voice_take_mutation(project_id, take_id)
