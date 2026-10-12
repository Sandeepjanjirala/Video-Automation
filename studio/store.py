"""PostgreSQL primary store implementation using SQLAlchemy (Phase 2)."""
import copy
import hashlib
import json
import os
import signal
import subprocess
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func, text, update, delete
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .config import DATA, setup
from .models import Project as PydanticProject
from .db.models import (
    Base, Workspace, Project, Revision, Asset, Job, JobStage, Draft,
    ApplicationReceipt, Outbox, VoiceTake
)
from .db.session import (
    get_engine, session_scope, check_connection, get_database_url,
    DatabaseConnectionError
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def compute_script_hash(project_dict: Dict[str, Any]) -> str:
    scenes = project_dict.get('scenes', [])
    content = [{'narration': s.get('narration', ''), 'roman': s.get('roman', '')} for s in scenes]
    content.append({'notes': project_dict.get('evidence_notes', '')})
    return digest(content)


def init() -> None:
    """Validate PostgreSQL connectivity and ensure default workspace exists."""
    setup()
    check_connection()
    with session_scope() as session:
        ws = session.get(Workspace, 'default')
        if not ws:
            ws = Workspace(id='default', name='Local Workspace')
            session.add(ws)


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


def get_project(pid: str, session: Optional[Session] = None) -> Dict[str, Any]:
    if session is not None:
        p = session.get(Project, pid)
        if not p:
            raise ValueError('Project not found')
        return unpack(p)

    with session_scope() as s:
        p = s.get(Project, pid)
        if not p:
            raise ValueError('Project not found')
        return unpack(p)


def list_projects(workspace_id: str = 'default', session: Optional[Session] = None) -> List[Dict[str, Any]]:
    stmt = (
        select(Project)
        .where(Project.workspace_id == workspace_id)
        .order_by(Project.updated_at.desc())
    )
    if session is not None:
        results = session.execute(stmt).scalars().all()
        return [unpack(p) for p in results]

    with session_scope() as s:
        results = s.execute(stmt).scalars().all()
        return [unpack(p) for p in results]


def save_project(project: Dict[str, Any], pid: Optional[str] = None, expected: Optional[int] = None) -> Dict[str, Any]:
    data = PydanticProject.model_validate(project).model_dump()
    pid = pid or uuid.uuid4().hex

    with session_scope() as session:
        # Row-level lock on existing project to prevent concurrency lost updates
        stmt = select(Project).where(Project.id == pid).with_for_update()
        old = session.execute(stmt).scalar_one_or_none()

        if old and expected is not None and old.revision != expected:
            raise ValueError('This project changed in another tab. Reload before saving.')

        if old:
            old_data = old.body
            if compute_script_hash(old_data) == compute_script_hash(data):
                data['script_approved_hash'] = old_data.get('script_approved_hash', '')
            if old_data == data:
                return unpack(old)
            revision = old.revision + 1
            old.revision = revision
            old.body = data
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(old, 'body')
            old.approved_revision = None
            old.updated_at = datetime.now(timezone.utc)
            target_p = old
        else:
            revision = 1
            target_p = Project(
                id=pid,
                workspace_id='default',
                revision=revision,
                approved_revision=None,
                body=data,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            session.add(target_p)

        # Record revision history
        rev_entry = Revision(
            project_id=pid,
            revision=revision,
            body=data,
            created_at=datetime.now(timezone.utc)
        )
        session.add(rev_entry)

        # Clear autosave draft once project is explicitly saved
        session.execute(delete(Draft).where(Draft.project_id == pid))
        session.flush()
        return unpack(target_p)


def reserve_ai_call(limit: int) -> None:
    day = now()[:10]
    with session_scope() as session:
        from .db.models import Usage
        stmt = select(Usage).where(Usage.day == day, Usage.provider == 'gemini').with_for_update()
        row = session.execute(stmt).scalar_one_or_none()
        count = row.calls if row else 0
        if count >= limit:
            raise ValueError('Daily Gemini request limit reached. Change Settings or try tomorrow (UTC).')
        if row:
            row.calls = count + 1
        else:
            session.add(Usage(day=day, provider='gemini', calls=count + 1))


def save_draft(pid: str, draft_data: Dict[str, Any]) -> Dict[str, Any]:
    with session_scope() as session:
        d = session.get(Draft, pid)
        now_dt = datetime.now(timezone.utc)
        if not d:
            d = Draft(project_id=pid, body=draft_data, updated_at=now_dt)
            session.add(d)
        else:
            d.body = draft_data
            d.updated_at = now_dt
        return {'project_id': pid, 'updated': now_dt.isoformat()}


def get_draft(pid: str) -> Optional[Dict[str, Any]]:
    with session_scope() as session:
        d = session.get(Draft, pid)
        if not d:
            return None
        return {'project_id': pid, 'body': d.body, 'updated': d.updated_at.isoformat()}


def delete_draft(pid: str) -> None:
    with session_scope() as session:
        session.execute(delete(Draft).where(Draft.project_id == pid))


def approve(pid: str, revision: int) -> Dict[str, Any]:
    with session_scope() as session:
        stmt = select(Project).where(Project.id == pid).with_for_update()
        p = session.execute(stmt).scalar_one_or_none()
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
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(p, 'body')
        p.approved_revision = revision
        p.updated_at = datetime.now(timezone.utc)
        session.flush()
        return unpack(p)


def revisions(pid: str) -> List[Dict[str, Any]]:
    with session_scope() as session:
        stmt = select(Revision).where(Revision.project_id == pid).order_by(Revision.revision.desc())
        revs = session.execute(stmt).scalars().all()
        return [{'revision': r.revision, 'project': r.body, 'created': r.created_at.isoformat()} for r in revs]


def asset(aid: str) -> Dict[str, Any]:
    with session_scope() as session:
        a = session.get(Asset, aid)
        if not a:
            raise ValueError('Media asset not found')
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
            'source_url': a.source_url,
            'license_url': a.license_url,
            'retrieved': a.created_at.isoformat() if a.created_at else now()
        })
        return meta


def add_asset(body: Dict[str, Any]) -> Dict[str, Any]:
    aid = body['id']
    sha = body.get('sha256')
    prov = body.get('provider') or 'upload'
    ext_id = str(body.get('external_id') or '') or None

    with session_scope() as session:
        existing = session.get(Asset, aid)
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
        else:
            new_a = Asset(
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
            session.add(new_a)
    return body


def find_asset(sha256: Optional[str] = None, provider: Optional[str] = None, external_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with session_scope() as session:
        stmt = select(Asset)
        if sha256:
            stmt = stmt.where(Asset.sha256 == sha256)
        elif provider and external_id:
            stmt = stmt.where(Asset.provider == provider, Asset.external_id == str(external_id))
        else:
            return None
        res = session.execute(stmt.limit(1)).scalar_one_or_none()
        if not res:
            return None
        return asset(res.id)


def list_assets(kind: Optional[str] = None, workspace_id: str = 'default') -> List[Dict[str, Any]]:
    with session_scope() as session:
        stmt = select(Asset).where(Asset.workspace_id == workspace_id)
        if kind:
            stmt = stmt.where(Asset.kind == kind)
        stmt = stmt.order_by(Asset.created_at.desc())
        assets = session.execute(stmt).scalars().all()
        return [asset(a.id) for a in assets]


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
        sid = s.get('id', '')
        if not s.get('narration', '').strip() or not s.get('roman', '').strip():
            errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'narration',
                           'message': f'Scene {idx}: Missing Telugu narration or Roman captions'})
        if s.get('visual') == 'graphic':
            if not s.get('graphic_title', '').strip():
                errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'graphic_title',
                               'message': f'Scene {idx} (graphic title card): Needs a title'})
        else:
            vid = s.get('video_asset')
            if not vid:
                errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'video_asset',
                               'message': f'Scene {idx}: Missing video footage (upload footage, pick stock, or choose title card)'})
            else:
                try:
                    vpath = asset_path(vid, 'video')
                    from .media import duration as media_duration
                    dur = media_duration(vpath)
                    trim = float(s.get('trim_start', 0))
                    if trim < 0:
                        errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'trim_start',
                                       'message': f'Scene {idx}: Invalid negative trim start ({trim}s)'})
                    elif trim >= dur:
                        errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'trim_start',
                                       'message': f'Scene {idx}: Trim start ({trim:.1f}s) exceeds clip duration ({dur:.1f}s)'})
                except Exception as exc:
                    errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'video_asset',
                                   'message': f'Scene {idx}: Video footage error ({exc})'})

        if not s.get('rights_reviewed'):
            errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'rights_reviewed',
                           'message': f'Scene {idx}: Visual relevance and rights review checkbox not confirmed'})

        if s.get('audio_asset'):
            try:
                asset_path(s['audio_asset'], 'audio')
                if s.get('audio_text_hash') != digest(s.get('narration', '')):
                    errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'audio_text_hash',
                                   'message': f'Scene {idx}: Uploaded narration does not match current script text'})
            except Exception as exc:
                errors.append({'scene_index': idx, 'scene_id': sid, 'field': 'audio_asset',
                                   'message': f'Scene {idx}: Audio narration file error ({exc})'})

    if project_data.get('music_asset'):
        try:
            asset_path(project_data['music_asset'], 'audio')
            if not project_data.get('music_rights_reviewed'):
                errors.append({'scene_index': 0, 'scene_id': '', 'field': 'music_rights_reviewed',
                               'message': 'Confirm background music rights before rendering'})
        except Exception as exc:
            errors.append({'scene_index': 0, 'scene_id': '', 'field': 'music_asset',
                           'message': f'Background music file error ({exc})'})

    expected_hash = compute_script_hash(project_data)
    approved_hash = project_data.get('script_approved_hash', '')
    if approved_hash != expected_hash:
        warnings.append({'field': 'script_approved_hash',
                         'message': 'Script has changed since last approval. Re-approve before final export.'})

    return {'ready': len(errors) == 0, 'errors': errors, 'warnings': warnings}


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
    """Enqueue a job and write outbox dispatch message in the same PostgreSQL transaction."""
    with session_scope() as session:
        # 1. Operation key idempotency check
        if operation_key:
            stmt_op = select(Job).where(Job.operation_key == operation_key).limit(1)
            existing_job = session.execute(stmt_op).scalar_one_or_none()
            if existing_job:
                return job_to_dict(existing_job)

        # 2. Check for duplicate pending job of same kind on this project
        stmt = (
            select(Job.id)
            .where(Job.project_id == pid, Job.kind == kind, Job.state.in_(['queued', 'running']))
            .limit(1)
        )
        if session.execute(stmt).scalar_one_or_none():
            raise ValueError(f'A {kind} job is already in progress for this project')

        p = session.get(Project, pid)
        if not p or p.revision != revision:
            raise ValueError('Save the current revision first')
        if kind in ('voice', 'render') and p.approved_revision != revision:
            raise ValueError('Approve this exact script revision first')

        proj = p.body
        if kind == 'render':
            pf = preflight(proj)
            if not pf['ready']:
                msg = pf['errors'][0]['message'] if pf['errors'] else 'Preflight checks failed'
                raise ValueError(f'Cannot queue render: {msg}')

        jid = uuid.uuid4().hex
        body = {'project': proj, 'options': options or {}}
        now_dt = datetime.now(timezone.utc)
        new_job = Job(
            id=jid,
            workspace_id=p.workspace_id,
            project_id=pid,
            revision=revision,
            kind=kind,
            state='queued',
            stage='queued',
            progress='Waiting for worker',
            snapshot=body,
            result={},
            applied=False,
            operation_key=operation_key,
            created_at=now_dt,
            updated_at=now_dt
        )
        session.add(new_job)

        stage = JobStage(
            job_id=jid,
            attempt=1,
            stage='queued',
            state='queued',
            details=options or {},
            created_at=now_dt,
            updated_at=now_dt
        )
        session.add(stage)

        # Transactional outbox event creation in the same transaction
        outbox_entry = Outbox(
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
        session.add(outbox_entry)

        session.flush()
        return job_to_dict(new_job)


def job(jid: str) -> Dict[str, Any]:
    with session_scope() as session:
        j = session.get(Job, jid)
        if not j:
            raise ValueError('Job not found')
        return job_to_dict(j)


def jobs(pid: str) -> List[Dict[str, Any]]:
    with session_scope() as session:
        stmt = select(Job).where(Job.project_id == pid).order_by(Job.created_at.desc()).limit(30)
        job_objs = session.execute(stmt).scalars().all()
        return [job_to_dict(j) for j in job_objs]


def progress(
    jid: str,
    message: str,
    state: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
    stage_name: Optional[str] = None,
    billable_units: int = 0,
    ambiguous: bool = False
) -> None:
    with session_scope() as session:
        j = session.get(Job, jid)
        if not j:
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
            stage = JobStage(
                job_id=jid,
                attempt=j.attempt_count or 1,
                stage=stage_name or ('complete' if state in ('succeeded', 'failed') else 'running'),
                state=state,
                details=result or {},
                billable_units=billable_units,
                ambiguous_outcome=ambiguous,
                created_at=now_dt,
                updated_at=now_dt
            )
            session.add(stage)


def claim() -> Optional[Dict[str, Any]]:
    """Claim a queued job using PostgreSQL SKIP LOCKED to prevent concurrency contention."""
    with session_scope() as session:
        stmt = (
            select(Job)
            .where(Job.state == 'queued')
            .order_by(Job.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        j = session.execute(stmt).scalar_one_or_none()
        if not j:
            return None
        now_dt = datetime.now(timezone.utc)
        j.state = 'running'
        j.stage = 'running'
        j.updated_at = now_dt
        j.attempt_count = (j.attempt_count or 0) + 1
        session.flush()
        return job_to_dict(j)


def retry(jid: str, mode: str = 'original') -> Dict[str, Any]:
    with session_scope() as session:
        stmt = select(Job).where(Job.id == jid).with_for_update()
        j = session.execute(stmt).scalar_one_or_none()
        if not j:
            raise ValueError('Job not found')
        if j.state != 'failed':
            raise ValueError('Only failed jobs can be retried')

        now_dt = datetime.now(timezone.utc)
        if mode == 'current':
            p = session.get(Project, j.project_id)
            if not p:
                raise ValueError('Project not found')
            snap = dict(j.snapshot)
            snap['project'] = p.body
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

        stage = JobStage(
            job_id=jid,
            attempt=j.attempt_count,
            stage=f'retry_{mode}',
            state='queued',
            details={'mode': mode},
            created_at=now_dt,
            updated_at=now_dt
        )
        session.add(stage)

        # Enqueue to outbox for distributed dispatcher
        outbox_msg = Outbox(
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
        session.add(outbox_msg)

        session.flush()
        return job_to_dict(j)


def acquire_lease(jid: str, worker_id: str, duration_seconds: int = 60) -> Optional[str]:
    """Acquire or extend execution lease for a job."""
    with session_scope() as session:
        stmt = select(Job).where(Job.id == jid).with_for_update()
        j = session.execute(stmt).scalar_one_or_none()
        if not j:
            return None
        now_dt = datetime.now(timezone.utc)
        if j.cancelled:
            return None
        # Check existing lease
        if j.state == 'running' and j.lease_token and j.lease_token != worker_id:
            if j.lease_expires_at and j.lease_expires_at > now_dt:
                return None  # Still owned by an active worker
        j.state = 'running'
        j.lease_token = worker_id
        j.lease_expires_at = now_dt + timedelta(seconds=duration_seconds)
        j.heartbeat_at = now_dt
        j.updated_at = now_dt
        session.flush()
        return worker_id


def renew_lease(jid: str, lease_token: str, extension_seconds: int = 60) -> bool:
    """Heartbeat lease renewal during long-running worker tasks."""
    with session_scope() as session:
        stmt = select(Job).where(Job.id == jid).with_for_update()
        j = session.execute(stmt).scalar_one_or_none()
        if not j or j.cancelled or j.lease_token != lease_token:
            return False
        now_dt = datetime.now(timezone.utc)
        j.lease_expires_at = now_dt + timedelta(seconds=extension_seconds)
        j.heartbeat_at = now_dt
        j.updated_at = now_dt
        session.flush()
        return True


def verify_lease_and_not_cancelled(jid: str, lease_token: str) -> bool:
    """Verify that caller holds the active unexpired lease and job is not cancelled."""
    with session_scope() as session:
        j = session.get(Job, jid)
        if not j or j.cancelled or j.lease_token != lease_token:
            return False
        now_dt = datetime.now(timezone.utc)
        if j.lease_expires_at and j.lease_expires_at < now_dt:
            return False
        return True


def register_subprocess(jid: str, pid: int) -> None:
    """Associate active OS subprocess PID with the job."""
    with session_scope() as session:
        j = session.get(Job, jid)
        if j:
            j.subprocess_pid = pid


def unregister_subprocess(jid: str) -> None:
    """Clear subprocess PID when process finishes."""
    with session_scope() as session:
        j = session.get(Job, jid)
        if j:
            j.subprocess_pid = None


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
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except Exception:
                    os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def cancel(jid: str) -> Dict[str, Any]:
    """Persist job cancellation and stop only the job's active subprocess tree."""
    with session_scope() as session:
        stmt = select(Job).where(Job.id == jid).with_for_update()
        j = session.execute(stmt).scalar_one_or_none()
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
        session.flush()

        if sub_pid:
            terminate_subprocess_tree(sub_pid)

        return job_to_dict(j)


def recover_expired_leases() -> int:
    """Recover ONLY expired ownerships; never reset running jobs with active leases."""
    with session_scope() as session:
        now_dt = datetime.now(timezone.utc)
        stmt = (
            select(Job)
            .where(
                Job.state == 'running',
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at < now_dt
            )
            .with_for_update()
        )
        expired_jobs = session.execute(stmt).scalars().all()
        count = 0
        for j in expired_jobs:
            j.state = 'failed'
            j.error_message = 'Worker execution lease expired without renewal'
            j.progress = 'Worker lost ownership or crashed. Retry to resume.'
            j.lease_token = None
            j.lease_expires_at = None
            j.updated_at = now_dt
            count += 1
        session.flush()
        return count


def apply_job(pid: str, jid: str, force: bool = False) -> Dict[str, Any]:
    with session_scope() as session:
        stmt_j = select(Job).where(Job.id == jid, Job.project_id == pid).with_for_update()
        j = session.execute(stmt_j).scalar_one_or_none()
        if not j:
            raise ValueError('Job not found for this project')

        stmt_p = select(Project).where(Project.id == pid).with_for_update()
        p = session.execute(stmt_p).scalar_one_or_none()
        if not p:
            raise ValueError('Project not found')

        current_project = copy.deepcopy(p.body)
        current_rev = p.revision

        if j.applied:
            return {
                'project': unpack(p),
                'applied': True,
                'conflicts': [],
                'revision': current_rev,
                'message': 'Job results were already applied.'
            }

        if j.state != 'succeeded':
            raise ValueError(f"Job is in state '{j.state}'; only succeeded jobs can be applied")

        kind = j.kind
        conflicts = []
        modified = False
        snap_project = j.snapshot.get('project', {})
        base_scenes = {s['id']: s for s in snap_project.get('scenes', [])}
        curr_scenes = {s['id']: s for s in current_project.get('scenes', [])}

        if kind == 'stock':
            target_scene_id = j.snapshot.get('options', {}).get('scene_id')
            asset_info = j.result.get('asset')
            if not target_scene_id or not asset_info:
                raise ValueError('Invalid stock job result')
            if target_scene_id not in curr_scenes:
                conflicts.append({'scene_id': target_scene_id, 'reason': 'Scene was deleted from the project'})
            else:
                curr_s = curr_scenes[target_scene_id]
                base_s = base_scenes.get(target_scene_id, {})
                base_asset = base_s.get('video_asset', '')
                current_asset = curr_s.get('video_asset', '')
                if current_asset and current_asset != base_asset and current_asset != asset_info['id'] and not force:
                    conflicts.append({'scene_id': target_scene_id,
                                      'reason': 'Scene has a newer manual video assignment; edit was preserved'})
                else:
                    curr_s['video_asset'] = asset_info['id']
                    curr_s['visual'] = 'stock'
                    curr_s['rights_reviewed'] = True
                    curr_s['trim_start'] = 0
                    modified = True

        elif kind == 'stock_all':
            selections = j.result.get('selections', [])
            for sel in selections:
                sid = sel.get('scene_id')
                asset_info = sel.get('asset')
                if not sid or not asset_info:
                    continue
                if sid not in curr_scenes:
                    conflicts.append({'scene_id': sid, 'reason': 'Scene was deleted from project'})
                    continue
                curr_s = curr_scenes[sid]
                base_s = base_scenes.get(sid, {})
                base_asset = base_s.get('video_asset', '')
                current_asset = curr_s.get('video_asset', '')
                if current_asset and current_asset != base_asset and current_asset != asset_info['id'] and not force:
                    conflicts.append({'scene_id': sid,
                                      'reason': f'Scene {sid} has a newer manual video selection; edit was preserved'})
                else:
                    curr_s['video_asset'] = asset_info['id']
                    curr_s['visual'] = 'stock'
                    curr_s['rights_reviewed'] = True
                    curr_s['trim_start'] = 0
                    modified = True

        elif kind == 'research':
            current_project['research_report'] = j.result.get('text', '')
            current_project['research_sources'] = j.result.get('sources', [])
            current_project['research_grounding'] = j.result.get('grounding', {})
            modified = True

        elif kind == 'draft':
            draft_scenes = j.result.get('scenes', [])
            if not draft_scenes:
                raise ValueError('Draft result contains no scenes')
            custom_edits = False
            for sid, s in curr_scenes.items():
                if sid not in base_scenes or s.get('narration') != base_scenes[sid].get('narration'):
                    custom_edits = True
                    break
            if custom_edits and not force:
                conflicts.append({'scene_id': 'all',
                                  'reason': 'Current project has newer script edits; draft replacement skipped to prevent overwrite'})
            else:
                current_project['scenes'] = draft_scenes
                modified = True

        else:
            raise ValueError(f"Job kind '{kind}' does not produce an applicable storyboard result")

        new_rev = current_rev
        now_dt = datetime.now(timezone.utc)
        if modified:
            new_rev = current_rev + 1
            validated_project = PydanticProject.model_validate(current_project).model_dump()
            p.revision = new_rev
            p.body = validated_project
            flag_modified(p, 'body')
            p.approved_revision = None
            p.updated_at = now_dt

            rev_entry = Revision(
                project_id=pid,
                revision=new_rev,
                body=validated_project,
                created_at=now_dt
            )
            session.add(rev_entry)
            session.execute(delete(Draft).where(Draft.project_id == pid))

        j.applied = True
        j.applied_at = now_dt

        # Record receipt
        receipt = ApplicationReceipt(
            job_id=jid,
            project_id=pid,
            applied_revision=new_rev,
            conflicts=conflicts,
            applied_at=now_dt
        )
        session.add(receipt)
        session.flush()

        return {
            'project': unpack(p),
            'applied': True,
            'conflicts': conflicts,
            'revision': new_rev
        }


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
    session: Optional[Session] = None
) -> Dict[str, Any]:
    tid = take_id or f'take_{uuid.uuid4().hex[:12]}'

    def _do_add(s: Session) -> Dict[str, Any]:
        take = VoiceTake(
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
        s.add(take)
        s.flush()
        return unpack_voice_take(take)

    if session is not None:
        return _do_add(session)
    with session_scope() as s:
        return _do_add(s)


def get_voice_take(take_id: str, session: Optional[Session] = None) -> Optional[Dict[str, Any]]:
    if session is not None:
        vt = session.get(VoiceTake, take_id)
        return unpack_voice_take(vt) if vt else None
    with session_scope() as s:
        vt = s.get(VoiceTake, take_id)
        return unpack_voice_take(vt) if vt else None


def list_voice_takes(project_id: str, scene_id: Optional[str] = None, session: Optional[Session] = None) -> List[Dict[str, Any]]:
    stmt = select(VoiceTake).where(VoiceTake.project_id == project_id)
    if scene_id:
        stmt = stmt.where(VoiceTake.scene_id == scene_id)
    stmt = stmt.order_by(VoiceTake.created_at.desc())

    if session is not None:
        takes = session.execute(stmt).scalars().all()
        return [unpack_voice_take(t) for t in takes]
    with session_scope() as s:
        takes = s.execute(stmt).scalars().all()
        return [unpack_voice_take(t) for t in takes]


def accept_voice_take(project_id: str, take_id: str, session: Optional[Session] = None) -> Dict[str, Any]:
    def _do_accept(s: Session) -> Dict[str, Any]:
        take = s.get(VoiceTake, take_id)
        if not take or take.project_id != project_id:
            raise ValueError(f"Voice take '{take_id}' not found for project '{project_id}'")

        if take.scene_id:
            stmt = (
                update(VoiceTake)
                .where(VoiceTake.project_id == project_id, VoiceTake.scene_id == take.scene_id)
                .values(accepted=False)
            )
            s.execute(stmt)
        take.accepted = True

        proj = s.execute(select(Project).where(Project.id == project_id).with_for_update()).scalar_one_or_none()
        if not proj:
            raise ValueError(f"Project '{project_id}' not found")

        p_data = copy.deepcopy(proj.body)
        if take.scene_id:
            for sc in p_data.get('scenes', []):
                if sc.get('id') == take.scene_id:
                    sc['take_id'] = take.id
                    if take.asset_id:
                        sc['audio_asset'] = take.asset_id
                        sc['audio_text_hash'] = digest(sc.get('narration', ''))
                    if take.spoken_text:
                        sc['spoken_text'] = take.spoken_text
                    sc['delivery_preset'] = take.preset
                    break

        if not p_data.get('voice_locked'):
            p_data['tts_provider'] = take.provider
            p_data['voice'] = take.voice_id
            p_data['delivery_preset'] = take.preset

        now_dt = datetime.now(timezone.utc)
        new_rev = proj.revision + 1
        validated = PydanticProject.model_validate(p_data).model_dump()
        proj.revision = new_rev
        proj.body = validated
        flag_modified(proj, 'body')
        proj.approved_revision = None
        proj.updated_at = now_dt

        rev_entry = Revision(
            project_id=project_id,
            revision=new_rev,
            body=validated,
            created_at=now_dt
        )
        s.add(rev_entry)
        s.flush()

        return {
            'take': unpack_voice_take(take),
            'project': unpack(proj)
        }

    if session is not None:
        return _do_accept(session)
    with session_scope() as s:
        return _do_accept(s)

