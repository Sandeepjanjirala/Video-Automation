"""Core views for Telugu Reel Studio (Stages M1, M2, M3, M4).

Stages M1-M3: Strictly read-only views matching FastAPI API contracts.
Stage M4: Media delivery, thumbnail caching/generation, controlled asset uploads,
and Pixabay search proxy.
"""
import os
import uuid
import hashlib
import mimetypes
import subprocess
import secrets
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from django.conf import settings
from django.db import connection, transaction
from django.http import StreamingHttpResponse, HttpResponse, FileResponse
import redis
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from studio.config import public_settings, executable, DATA
from studio.media import probe, run as media_run
from studio import store
from studio.models import Project as PydanticProject
import pydantic
from core import services
from .models import (
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
from .serializers import (
    serialize_project_dict,
    serialize_revision_dict,
    serialize_job_dict,
    serialize_voice_take_dict,
    serialize_asset_dict,
)

# Persistent session token for bootstrap parity
DJANGO_TOKEN = secrets.token_urlsafe(32)


def ranged_file_response(request, file_path: Path, content_type: Optional[str] = None):
    """Serve binary file with support for HTTP Range requests (RFC 7233) and chunked streaming."""
    stat = file_path.stat()
    total_size = stat.st_size
    range_header = request.META.get('HTTP_RANGE', '')

    if not content_type:
        content_type, _ = mimetypes.guess_type(str(file_path))
        content_type = content_type or 'application/octet-stream'

    # If no Range header, stream standard FileResponse with Accept-Ranges
    if not range_header or not range_header.startswith('bytes='):
        f = open(file_path, 'rb')
        resp = FileResponse(f, content_type=content_type)
        resp['Accept-Ranges'] = 'bytes'
        resp['Content-Length'] = str(total_size)
        return resp

    range_spec = range_header[6:].strip()
    if ',' in range_spec:
        f = open(file_path, 'rb')
        resp = FileResponse(f, content_type=content_type)
        resp['Accept-Ranges'] = 'bytes'
        return resp

    parts = range_spec.split('-')
    if len(parts) != 2:
        f = open(file_path, 'rb')
        resp = FileResponse(f, content_type=content_type)
        resp['Accept-Ranges'] = 'bytes'
        return resp

    start_str, end_str = parts[0].strip(), parts[1].strip()
    try:
        if start_str and end_str:
            start = int(start_str)
            end = int(end_str)
        elif start_str:
            start = int(start_str)
            end = total_size - 1
        elif end_str:
            length = int(end_str)
            start = max(0, total_size - length)
            end = total_size - 1
        else:
            raise ValueError()
    except ValueError:
        f = open(file_path, 'rb')
        resp = FileResponse(f, content_type=content_type)
        resp['Accept-Ranges'] = 'bytes'
        return resp

    if start < 0 or start >= total_size or end < start:
        resp = HttpResponse(status=416)
        resp['Content-Range'] = f'bytes */{total_size}'
        return resp

    end = min(end, total_size - 1)
    content_length = (end - start) + 1

    def file_chunk_generator(path: Path, start_pos: int, count: int, chunk_size: int = 64 * 1024):
        remaining = count
        with open(path, 'rb') as fp:
            fp.seek(start_pos)
            while remaining > 0:
                read_len = min(chunk_size, remaining)
                data = fp.read(read_len)
                if not data:
                    break
                remaining -= len(data)
                yield data

    resp = StreamingHttpResponse(
        file_chunk_generator(file_path, start, content_length),
        status=206,
        content_type=content_type
    )
    resp['Accept-Ranges'] = 'bytes'
    resp['Content-Range'] = f'bytes {start}-{end}/{total_size}'
    resp['Content-Length'] = str(content_length)
    return resp



# ==============================================================================
# 1. Health & Diagnostics
# ==============================================================================

class HealthCheckView(APIView):
    """Read-only health verification for Django, PostgreSQL, Redis, and Media tools."""

    def get(self, request, *args, **kwargs):
        db_info = {
            "engine": "postgresql",
            "connected": False,
            "current_alembic_revision": None,
            "tables_visible": False,
            "table_count": 0,
            "read_query_verified": False,
        }

        expected_tables = {
            "workspaces", "projects", "revisions", "assets", "jobs",
            "job_stages", "drafts", "application_receipts", "outbox",
            "usage", "voice_takes", "alembic_version"
        }

        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public';
                """)
                visible_tables = {row[0] for row in cursor.fetchall()}
                db_info["table_count"] = len(visible_tables)
                db_info["tables_visible"] = expected_tables.issubset(visible_tables)

                if "alembic_version" in visible_tables:
                    cursor.execute("SELECT version_num FROM alembic_version LIMIT 1;")
                    row = cursor.fetchone()
                    if row:
                        db_info["current_alembic_revision"] = row[0]

                if "projects" in visible_tables:
                    cursor.execute("SELECT count(*) FROM projects;")
                    count_val = cursor.fetchone()[0]
                    db_info["read_query_verified"] = count_val >= 0

                db_info["connected"] = True
        except Exception as exc:
            db_info["error"] = str(exc)

        redis_info = {"connected": False}
        try:
            r = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2.0)
            redis_info["connected"] = bool(r.ping())
        except Exception as exc:
            redis_info["error"] = str(exc)

        media_tools = {}
        for binary in ("ffmpeg", "ffprobe"):
            try:
                exe = executable(binary)
                res = subprocess.run(
                    [exe, "-version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5
                )
                if res.returncode == 0:
                    media_tools[binary] = res.stdout.splitlines()[0]
                else:
                    media_tools[binary] = f"Error code {res.returncode}"
            except Exception as exc:
                media_tools[binary] = str(exc)

        overall_status = "ok" if (db_info["connected"] and db_info["tables_visible"]) else "degraded"
        http_status = status.HTTP_200_OK if overall_status == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE

        response_payload = {
            "status": overall_status,
            "service": "django",
            "version": "5.1.15",
            "database": db_info,
            "redis": redis_info,
            "media_tools": media_tools,
        }
        return Response(response_payload, status=http_status)


class SchemaStatusView(APIView):
    """Internal M2 diagnostic endpoint reporting unmanaged model status and counts."""

    def get(self, request, *args, **kwargs):
        model_counts = {
            "Workspace": Workspace.objects.count(),
            "Project": Project.objects.count(),
            "Revision": Revision.objects.count(),
            "Asset": Asset.objects.count(),
            "Job": Job.objects.count(),
            "JobStage": JobStage.objects.count(),
            "Draft": Draft.objects.count(),
            "ApplicationReceipt": ApplicationReceipt.objects.count(),
            "OutboxEvent": OutboxEvent.objects.count(),
            "VoiceTake": VoiceTake.objects.count(),
        }

        alembic_rev = None
        usage_count = 0
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version LIMIT 1;")
            row = cursor.fetchone()
            if row:
                alembic_rev = row[0]
            cursor.execute("SELECT count(*) FROM usage;")
            usage_count = cursor.fetchone()[0]

        return Response({
            "stage": "M2",
            "schema_authority": "alembic",
            "alembic_revision": alembic_rev,
            "mapped_models_count": len(model_counts),
            "model_counts": model_counts,
            "unmapped_tables": {
                "usage": {
                    "reason": "Composite primary key (day, provider); queried via core.repository functions",
                    "row_count": usage_count,
                },
                "alembic_version": {
                    "reason": "Alembic internal schema migration table",
                    "version": alembic_rev,
                }
            },
            "read_only": True
        })


# ==============================================================================
# 2. Bootstrap Handshake
# ==============================================================================

class BootstrapView(APIView):
    """Replicates GET /api/bootstrap handshake returning settings, token and projects."""

    def get(self, request, *args, **kwargs):
        ws_id = request.query_params.get('workspace_id', 'default')
        projs = Project.objects.filter(workspace_id=ws_id).order_by('-updated_at')
        projects_payload = [serialize_project_dict(p) for p in projs]
        return Response({
            'token': DJANGO_TOKEN,
            'settings': public_settings(),
            'projects': projects_payload,
        })


# ==============================================================================
# 3. Projects & Revisions
# ==============================================================================

class ProjectListView(APIView):
    """GET /api/projects - Lists all projects scoped to workspace.
    POST /api/projects - Creates a new project document with initial revision 1.
    """

    def get(self, request, *args, **kwargs):
        ws_id = request.query_params.get('workspace_id', 'default')
        projs = Project.objects.filter(workspace_id=ws_id).order_by('-updated_at')
        return Response([serialize_project_dict(p) for p in projs])

    def post(self, request, *args, **kwargs):
        data = request.data
        if not isinstance(data, dict):
            return Response({'detail': 'Invalid JSON body'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        try:
            PydanticProject.model_validate(data)
        except pydantic.ValidationError as err:
            return Response({'detail': err.errors()}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            p, _ = services.save_project_mutation(data, pid=data.get('id'))
            return Response(serialize_project_dict(p), status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ProjectDetailView(APIView):
    """GET /api/projects/{pid} - Returns project detail or 400 if not found.
    PUT /api/projects/{pid} - Updates project with optimistic lock and bumps revision.
    """

    def get(self, request, pid: str, *args, **kwargs):
        try:
            proj = Project.objects.get(id=pid)
            return Response(serialize_project_dict(proj))
        except Project.DoesNotExist:
            return Response({'detail': 'Project not found'}, status=status.HTTP_400_BAD_REQUEST)

    def put(self, request, pid: str, *args, **kwargs):
        data = request.data
        if not isinstance(data, dict) or 'revision' not in data or 'project' not in data:
            return Response(
                {'detail': [{'loc': ['body'], 'msg': 'Body must contain revision and project', 'type': 'value_error'}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        if not isinstance(data['revision'], int):
            return Response(
                {'detail': [{'loc': ['body', 'revision'], 'msg': 'Input should be a valid integer', 'type': 'int_parsing'}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        if not isinstance(data['project'], dict):
            return Response(
                {'detail': [{'loc': ['body', 'project'], 'msg': 'Input should be a valid dictionary', 'type': 'dict_type'}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        try:
            PydanticProject.model_validate(data['project'])
        except pydantic.ValidationError as err:
            return Response({'detail': err.errors()}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            p, _ = services.save_project_mutation(data['project'], pid=pid, expected_revision=data['revision'])
            return Response(serialize_project_dict(p), status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ProjectHistoryView(APIView):
    """GET /api/projects/{pid}/history - Returns revision history ordered newest first."""

    def get(self, request, pid: str, *args, **kwargs):
        revs = Revision.objects.filter(project_id=pid).order_by('-revision')[:50]
        return Response([serialize_revision_dict(r) for r in revs])


class ProjectApproveView(APIView):
    """POST /api/projects/{pid}/approve - Approves project revision after validating scenes and evidence."""

    def post(self, request, pid: str, *args, **kwargs):
        data = request.data
        if not isinstance(data, dict) or 'revision' not in data or not isinstance(data['revision'], int):
            return Response(
                {'detail': [{'loc': ['body', 'revision'], 'msg': 'Input should be a valid integer', 'type': 'int_parsing'}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        try:
            p = services.approve_project(pid, revision=data['revision'])
            return Response(serialize_project_dict(p), status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ProjectDraftView(APIView):
    """GET /api/projects/{pid}/draft - Returns autosave draft body or empty dict.
    PUT /api/projects/{pid}/draft - Upserts client autosave draft.
    DELETE /api/projects/{pid}/draft - Discards client autosave draft.
    """

    def get(self, request, pid: str, *args, **kwargs):
        draft = Draft.objects.filter(project_id=pid).first()
        if draft and draft.body:
            return Response(draft.body)
        return Response({})

    def put(self, request, pid: str, *args, **kwargs):
        data = request.data
        if not isinstance(data, dict):
            return Response({'detail': 'Invalid draft body'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        res = services.save_draft_data(pid, data)
        return Response(res, status=status.HTTP_200_OK)

    def delete(self, request, pid: str, *args, **kwargs):
        services.delete_draft_data(pid)
        return Response({'ok': True}, status=status.HTTP_200_OK)


class ProjectApplyJobView(APIView):
    """POST /api/projects/{pid}/jobs/{jid}/apply (and GET)
    Applies completed job results to project with field/scene conflict detection.
    """

    def _apply(self, request, pid: str, jid: str):
        force = request.query_params.get('force', 'false').lower() in ('true', '1')
        try:
            res = services.apply_job_result(pid, jid, force=force)
            return Response(res, status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    def post(self, request, pid: str, jid: str, *args, **kwargs):
        return self._apply(request, pid, jid)

    def get(self, request, pid: str, jid: str, *args, **kwargs):
        return self._apply(request, pid, jid)


class ProjectAssignMediaView(APIView):
    """POST /api/projects/{pid}/assign-media - Assigns video or audio asset to project scenes."""

    def post(self, request, pid: str, *args, **kwargs):
        data = request.data
        if not isinstance(data, dict) or 'asset_id' not in data or 'scene_ids' not in data:
            return Response(
                {'detail': [{'loc': ['body'], 'msg': 'Fields asset_id and scene_ids required', 'type': 'value_error'}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        if not isinstance(data['asset_id'], str) or not isinstance(data['scene_ids'], list):
            return Response(
                {'detail': [{'loc': ['body'], 'msg': 'asset_id must be string and scene_ids list', 'type': 'value_error'}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        try:
            p = services.assign_media_to_scenes(pid, data['asset_id'], data['scene_ids'])
            return Response(serialize_project_dict(p), status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ProjectVoiceLockView(APIView):
    """POST /api/projects/{pid}/voice_lock - Toggles voice profile lock on project."""

    def post(self, request, pid: str, *args, **kwargs):
        data = request.data
        if not isinstance(data, dict) or 'locked' not in data or not isinstance(data['locked'], bool):
            return Response(
                {'detail': [{'loc': ['body'], 'msg': 'Field locked (bool) required', 'type': 'value_error'}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        try:
            p = services.set_project_voice_lock(pid, data['locked'])
            return Response(serialize_project_dict(p), status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ProjectPronunciationDictView(APIView):
    """POST /api/projects/{pid}/pronunciation_dict - Updates pronunciation overrides."""

    def post(self, request, pid: str, *args, **kwargs):
        data = request.data
        if not isinstance(data, dict) or 'dictionary' not in data or not isinstance(data['dictionary'], dict):
            return Response(
                {'detail': [{'loc': ['body', 'dictionary'], 'msg': 'Field dictionary (dict) required', 'type': 'value_error'}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        try:
            p = services.set_project_pronunciation_dict(pid, data['dictionary'])
            return Response(serialize_project_dict(p), status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ProjectPreflightView(APIView):
    """GET /api/projects/{pid}/preflight - Pure validation check for render readiness."""

    def get(self, request, pid: str, *args, **kwargs):
        try:
            proj = Project.objects.get(id=pid)
        except Project.DoesNotExist:
            return Response({'detail': 'Project not found'}, status=status.HTTP_400_BAD_REQUEST)

        # Reuse framework-independent preflight validation
        report = store.preflight(proj.body)
        return Response(report)


# ==============================================================================
# 4. Jobs & Background Operations (Stage M5)
# ==============================================================================

class ProjectJobsView(APIView):
    """GET /api/projects/{pid}/jobs - Returns list of jobs for project.
    POST /api/projects/{pid}/jobs - Idempotently enqueues a background job and creates
    an atomic transactional outbox record using scoped django_job_writer identity.
    """

    def get(self, request, pid: str, *args, **kwargs):
        job_objs = Job.objects.filter(project_id=pid).order_by('-created_at')[:30]
        return Response([serialize_job_dict(j) for j in job_objs])

    def post(self, request, pid: str, *args, **kwargs):
        data = request.data
        if not isinstance(data, dict):
            return Response({'detail': 'Invalid JSON body'}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        errors = []
        if 'revision' not in data:
            errors.append({'type': 'missing', 'loc': ['body', 'revision'], 'msg': 'Field required'})
        elif not isinstance(data['revision'], int):
            errors.append({'type': 'int_parsing', 'loc': ['body', 'revision'], 'msg': 'Input should be a valid integer'})

        valid_kinds = ('research', 'draft', 'voice', 'render', 'stock', 'stock_all')
        if 'kind' not in data:
            errors.append({'type': 'missing', 'loc': ['body', 'kind'], 'msg': 'Field required'})
        elif data['kind'] not in valid_kinds:
            errors.append({'type': 'literal_error', 'loc': ['body', 'kind'], 'msg': f"Input should be one of {valid_kinds}"})

        quality = data.get('quality', 'final')
        if quality not in ('draft', 'final'):
            errors.append({'type': 'literal_error', 'loc': ['body', 'quality'], 'msg': "Input should be 'draft' or 'final'"})

        if errors:
            return Response({'detail': errors}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        revision = data['revision']
        kind = data['kind']
        script = str(data.get('script', ''))[:20000]
        video_id = int(data.get('video_id', 0))
        scene_id = str(data.get('scene_id', ''))[:48]
        operation_key = data.get('operation_key')
        if operation_key is not None:
            operation_key = str(operation_key)[:128]

        from studio.config import settings as studio_settings
        tts_provider = studio_settings().get('tts_provider', 'edge')
        options = {
            'tts_provider': tts_provider,
            'quality': quality,
            'script': script,
            'video_id': video_id,
            'scene_id': scene_id,
        }

        with transaction.atomic(using='job_writer'):
            # 1. Operation key idempotency check
            if operation_key:
                existing_op = Job.objects.using('job_writer').filter(operation_key=operation_key).first()
                if existing_op:
                    return Response(serialize_job_dict(existing_op), status=status.HTTP_200_OK)

            # 2. Check for duplicate pending job of same kind on this project
            dup_exists = Job.objects.using('job_writer').filter(
                project_id=pid,
                kind=kind,
                state__in=['queued', 'running']
            ).exists()
            if dup_exists:
                return Response(
                    {'detail': f'A {kind} job is already in progress for this project'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # 3. Project existence and revision check
            p = Project.objects.using('job_writer').filter(id=pid).first()
            if not p or p.revision != revision:
                return Response({'detail': 'Save the current revision first'}, status=status.HTTP_400_BAD_REQUEST)

            if kind in ('voice', 'render') and p.approved_revision != revision:
                return Response({'detail': 'Approve this exact script revision first'}, status=status.HTTP_400_BAD_REQUEST)

            # 4. Render preflight check
            if kind == 'render':
                pf = store.preflight(p.body)
                if not pf['ready']:
                    msg = pf['errors'][0]['message'] if pf['errors'] else 'Preflight checks failed'
                    return Response({'detail': f'Cannot queue render: {msg}'}, status=status.HTTP_400_BAD_REQUEST)

            # 5. Create new Job and transactional Outbox event
            jid = uuid.uuid4().hex
            now_dt = datetime.now(timezone.utc)
            snapshot = {'project': p.body, 'options': options}

            new_job = Job.objects.using('job_writer').create(
                id=jid,
                workspace_id=p.workspace_id,
                project_id=pid,
                revision=revision,
                kind=kind,
                state='queued',
                stage='queued',
                progress='Waiting for worker',
                snapshot=snapshot,
                result={},
                applied=False,
                operation_key=operation_key,
            )

            JobStage.objects.using('job_writer').create(
                job=new_job,
                attempt=1,
                stage='queued',
                state='queued',
                details=options,
            )

            OutboxEvent.objects.using('job_writer').create(
                event_type='job.enqueued',
                payload={
                    'job_id': jid,
                    'project_id': pid,
                    'revision': revision,
                    'kind': kind,
                    'options': options,
                    'operation_key': operation_key,
                },
                processed=False,
                attempts=0,
            )

            return Response(serialize_job_dict(new_job), status=status.HTTP_200_OK)


class JobRetryView(APIView):
    """POST /api/jobs/{jid}/retry - Retries a failed job in 'original' or 'current' mode."""

    def post(self, request, jid: str, *args, **kwargs):
        mode = None
        if isinstance(request.data, dict) and 'mode' in request.data:
            mode = request.data['mode']
        if not mode:
            mode = request.query_params.get('mode', 'original')

        if mode not in ('original', 'current'):
            return Response({'detail': "Mode must be 'original' or 'current'"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic(using='job_writer'):
            j = Job.objects.using('job_writer').select_for_update().filter(id=jid).first()
            if not j:
                return Response({'detail': 'Job not found'}, status=status.HTTP_400_BAD_REQUEST)
            if j.state != 'failed':
                return Response({'detail': 'Only failed jobs can be retried'}, status=status.HTTP_400_BAD_REQUEST)

            now_dt = datetime.now(timezone.utc)
            if mode == 'current':
                p = Project.objects.using('job_writer').filter(id=j.project_id).first()
                if not p:
                    return Response({'detail': 'Project not found'}, status=status.HTTP_400_BAD_REQUEST)
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
            j.attempt_count = (j.attempt_count or 1) + 1
            j.updated_at = now_dt
            j.save(using='job_writer', update_fields=[
                'snapshot', 'revision', 'progress', 'state', 'stage',
                'result', 'cancelled', 'cancelled_at', 'lease_token',
                'lease_expires_at', 'attempt_count', 'updated_at'
            ])

            JobStage.objects.using('job_writer').create(
                job=j,
                attempt=j.attempt_count,
                stage=f'retry_{mode}',
                state='queued',
                details={'mode': mode},
            )

            OutboxEvent.objects.using('job_writer').create(
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
            )

            return Response(serialize_job_dict(j), status=status.HTTP_200_OK)


class JobCancelView(APIView):
    """POST /api/jobs/{jid}/cancel - Idempotently cancels a running or queued job."""

    def post(self, request, jid: str, *args, **kwargs):
        sub_pid = None
        with transaction.atomic(using='job_writer'):
            j = Job.objects.using('job_writer').select_for_update().filter(id=jid).first()
            if not j:
                return Response({'detail': 'Job not found'}, status=status.HTTP_400_BAD_REQUEST)
            if j.state in ('succeeded', 'cancelled'):
                return Response(serialize_job_dict(j), status=status.HTTP_200_OK)

            now_dt = datetime.now(timezone.utc)
            j.cancelled = True
            j.cancelled_at = now_dt
            j.state = 'cancelled'
            j.progress = 'Cancelled by user'
            j.updated_at = now_dt
            sub_pid = j.subprocess_pid
            j.subprocess_pid = None
            j.save(using='job_writer', update_fields=[
                'cancelled', 'cancelled_at', 'state', 'progress',
                'subprocess_pid', 'updated_at'
            ])

        if sub_pid:
            store.terminate_subprocess_tree(sub_pid)

        return Response(serialize_job_dict(j), status=status.HTTP_200_OK)


class JobAudioArtifactView(APIView):
    """GET /api/jobs/{jid}/audio/{scene_id} - Serves generated voice preview audio."""

    def get(self, request, jid: str, scene_id: str, *args, **kwargs):
        try:
            j = Job.objects.get(id=jid)
        except Job.DoesNotExist:
            return Response({'detail': 'Job not found'}, status=status.HTTP_400_BAD_REQUEST)

        if j.state != 'succeeded' or j.kind != 'voice':
            return Response({'detail': 'Voice preview is not ready'}, status=status.HTTP_400_BAD_REQUEST)

        voices = j.result.get('voices', []) if isinstance(j.result, dict) else []
        item = next((v for v in voices if v.get('scene_id') == scene_id), None)
        if not item or not item.get('audio_path'):
            return Response({'detail': 'Scene audio not found'}, status=status.HTTP_400_BAD_REQUEST)

        audio_path = Path(item['audio_path']).resolve()
        cache_dir = (DATA / 'cache').resolve()
        assets_dir = (DATA / 'assets').resolve()

        if audio_path.parent not in (cache_dir, assets_dir) or not audio_path.is_file():
            return Response({'detail': 'Invalid audio path'}, status=status.HTTP_400_BAD_REQUEST)

        return FileResponse(open(audio_path, 'rb'), content_type='audio/mpeg')


class ExportArtifactView(APIView):
    """GET /api/exports/{jid}/{name} - Serves finalized job export artifacts."""

    def get(self, request, jid: str, name: str, *args, **kwargs):
        import re
        if not re.fullmatch(r'[a-f0-9]{32}', jid) or name not in ('reel.mp4', 'captions.srt', 'source-report.json', 'project.json'):
            return Response({'detail': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            j = Job.objects.get(id=jid)
        except Job.DoesNotExist:
            return Response({'detail': 'Export is incomplete'}, status=status.HTTP_400_BAD_REQUEST)

        if j.state != 'succeeded':
            return Response({'detail': 'Export is incomplete'}, status=status.HTTP_400_BAD_REQUEST)

        exports_base = (DATA / 'exports').resolve()
        export_path = (exports_base / jid / name).resolve()

        # Path traversal guard
        if export_path.parent != (exports_base / jid).resolve() or not export_path.is_file():
            return Response({'detail': 'Export artifact not found'}, status=status.HTTP_404_NOT_FOUND)

        if name == 'reel.mp4':
            return ranged_file_response(request, export_path, content_type='video/mp4')

        content_type, _ = mimetypes.guess_type(str(export_path))
        return FileResponse(open(export_path, 'rb'), content_type=content_type or 'application/octet-stream')


class ProjectTakesView(APIView):
    """GET /api/projects/{pid}/takes - Returns Voice Lab takes for project."""

    def get(self, request, pid: str, *args, **kwargs):
        scene_id = request.query_params.get('scene_id')
        qs = VoiceTake.objects.filter(project_id=pid)
        if scene_id:
            qs = qs.filter(scene_id=scene_id)
        takes = qs.order_by('-created_at')
        return Response([serialize_voice_take_dict(t) for t in takes])


# ==============================================================================
# 5. Voice Capabilities
# ==============================================================================

class VoiceCapabilitiesView(APIView):
    """GET /api/voice/capabilities - Returns verified TTS voice capabilities."""

    def get(self, request, *args, **kwargs):
        from studio.voice.registry import list_voices
        caps = list_voices()
        return Response([
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
        ])


# ==============================================================================
# 6. Assets & Media Delivery (Stage M4)
# ==============================================================================

class AssetListCreateView(APIView):
    """GET /api/assets - List assets.
    POST /api/assets - Stream and validate upload, compute SHA-256, deduplicate,
    validate streams with FFprobe, and write transactional DB record using scoped
    django_media_writer identity.
    """
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request, *args, **kwargs):
        ws_id = request.query_params.get('workspace_id', 'default')
        kind = request.query_params.get('kind')
        qs = Asset.objects.filter(workspace_id=ws_id)
        if kind:
            qs = qs.filter(kind=kind)
        assets = qs.order_by('-created_at')
        return Response([serialize_asset_dict(a) for a in assets])

    def post(self, request, *args, **kwargs):
        file = request.FILES.get('file')
        if not file:
            return Response({'detail': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

        extension = Path(file.name or '').suffix.lower()
        if extension not in ('.mp4', '.mov', '.mkv', '.webm', '.mp3', '.wav', '.m4a', '.ogg', '.aac'):
            return Response(
                {'detail': 'Upload MP4/MOV/MKV/WEBM video or MP3/WAV/M4A/OGG/AAC audio'},
                status=status.HTTP_400_BAD_REQUEST
            )

        aid = uuid.uuid4().hex
        assets_dir = (Path(settings.BASE_DIR) / 'data' / 'assets').resolve()
        assets_dir.mkdir(parents=True, exist_ok=True)
        path = assets_dir / f"{aid}{extension}"
        hasher = hashlib.sha256()

        try:
            size = 0
            with open(path, 'wb') as out:
                for chunk in file.chunks(chunk_size=1024 * 1024):
                    size += len(chunk)
                    if size > 200 * 1024 * 1024:
                        raise ValueError('Upload limit is 200 MB per file')
                    out.write(chunk)
                    hasher.update(chunk)
            file_hash = hasher.hexdigest()

            # Deduplication: reuse existing asset if identical content hash exists
            existing = Asset.objects.filter(sha256=file_hash).first()
            if existing:
                path.unlink(missing_ok=True)
                return Response(serialize_asset_dict(existing))

            # Media stream validation via FFprobe
            info = probe(path)
            video = any(
                s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')
                for s in info.get('streams', [])
            )
            audio = any(s.get('codec_type') == 'audio' for s in info.get('streams', []))
            if not video and not audio:
                raise ValueError('No usable video or audio stream found')

            now_iso = datetime.now(timezone.utc).isoformat()
            duration_val = float(info.get('format', {}).get('duration', 0.0))
            asset_meta = {
                'id': aid,
                'filename': path.name,
                'name': Path(file.name or '').name[:180],
                'kind': 'video' if video else 'audio',
                'duration': duration_val,
                'sha256': file_hash,
                'provider': 'upload',
                'retrieved': now_iso,
                'source_url': '',
                'license_url': 'User supplied; review required',
            }

            # Scoped transactional write via media_writer identity
            with transaction.atomic(using='media_writer'):
                # Safe concurrency double-check
                existing_race = Asset.objects.using('media_writer').filter(sha256=file_hash).first()
                if existing_race:
                    path.unlink(missing_ok=True)
                    return Response(serialize_asset_dict(existing_race))

                new_asset = Asset.objects.using('media_writer').create(
                    id=aid,
                    workspace_id='default',
                    filename=path.name,
                    name=Path(file.name or '').name[:180],
                    kind='video' if video else 'audio',
                    duration=duration_val,
                    sha256=file_hash,
                    provider='upload',
                    external_id=None,
                    source_url='',
                    license_url='User supplied; review required',
                    meta=asset_meta,
                )

            return Response(serialize_asset_dict(new_asset))

        except Exception as exc:
            path.unlink(missing_ok=True)
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class AssetDetailView(APIView):
    """GET /api/assets/{aid} - Returns asset metadata or 400 if not found."""

    def get(self, request, aid: str, *args, **kwargs):
        try:
            a = Asset.objects.get(id=aid)
            return Response(serialize_asset_dict(a))
        except Asset.DoesNotExist:
            return Response({'detail': 'Media asset not found'}, status=status.HTTP_400_BAD_REQUEST)


class AssetMediaView(APIView):
    """GET /api/assets/{aid}/media and /api/assets/{aid}/file - Stream binary media file."""

    def get(self, request, aid: str, *args, **kwargs):
        try:
            a = Asset.objects.get(id=aid)
        except Asset.DoesNotExist:
            return Response({'detail': 'Media asset not found'}, status=status.HTTP_400_BAD_REQUEST)

        assets_dir = (Path(settings.BASE_DIR) / 'data' / 'assets').resolve()
        file_path = (assets_dir / a.filename).resolve()

        # Path traversal and existence check
        if file_path.parent != assets_dir or not file_path.is_file():
            return Response(
                {'detail': f'Media file {a.name or aid} is missing on disk'},
                status=status.HTTP_400_BAD_REQUEST
            )

        return ranged_file_response(request, file_path)


class AssetThumbnailView(APIView):
    """GET /api/assets/{aid}/thumbnail - Return cached thumbnail or generate via FFmpeg."""

    def get(self, request, aid: str, *args, **kwargs):
        try:
            a = Asset.objects.get(id=aid)
        except Asset.DoesNotExist:
            return Response({'detail': 'Media asset not found'}, status=status.HTTP_400_BAD_REQUEST)

        if a.kind != 'video':
            return Response(
                {'detail': 'Thumbnails only available for video assets'},
                status=status.HTTP_404_NOT_FOUND
            )

        cache_dir = (Path(settings.BASE_DIR) / 'data' / 'cache').resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        thumb = cache_dir / f'{aid}.thumb.jpg'

        if thumb.is_file() and thumb.stat().st_size > 0:
            return FileResponse(open(thumb, 'rb'), content_type='image/jpeg')

        assets_dir = (Path(settings.BASE_DIR) / 'data' / 'assets').resolve()
        video_p = (assets_dir / a.filename).resolve()
        if not video_p.is_file():
            return Response({'detail': 'Could not generate thumbnail'}, status=status.HTTP_404_NOT_FOUND)

        # Concurrency safety: generate to unique temporary file and atomically replace
        part_thumb = cache_dir / f'{aid}.thumb.{uuid.uuid4().hex}.part.jpg'
        try:
            media_run([
                executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y',
                '-ss', '0.5', '-i', str(video_p), '-vframes', '1',
                '-vf', 'scale=270:480:force_original_aspect_ratio=decrease,pad=270:480:(ow-iw)/2:(oh-ih)/2:color=black',
                '-q:v', '3', str(part_thumb)
            ], timeout=15)

            if not part_thumb.is_file() or part_thumb.stat().st_size == 0:
                part_thumb.unlink(missing_ok=True)
                if thumb.is_file() and thumb.stat().st_size > 0:
                    return FileResponse(open(thumb, 'rb'), content_type='image/jpeg')
                return Response({'detail': 'Could not generate thumbnail'}, status=status.HTTP_404_NOT_FOUND)

            # Atomic rename avoids race conditions / corrupted reads
            try:
                os.replace(part_thumb, thumb)
            except (PermissionError, OSError):
                # Another concurrent worker already finalized and locked/opened the file
                part_thumb.unlink(missing_ok=True)
                if thumb.is_file() and thumb.stat().st_size > 0:
                    return FileResponse(open(thumb, 'rb'), content_type='image/jpeg')
                raise

        except Exception:
            part_thumb.unlink(missing_ok=True)
            if thumb.is_file() and thumb.stat().st_size > 0:
                return FileResponse(open(thumb, 'rb'), content_type='image/jpeg')
            return Response({'detail': 'Could not generate thumbnail'}, status=status.HTTP_404_NOT_FOUND)

        if not thumb.is_file():
            return Response({'detail': 'Thumbnail not found'}, status=status.HTTP_404_NOT_FOUND)

        return FileResponse(open(thumb, 'rb'), content_type='image/jpeg')


# ==============================================================================
# 7. Stock Provider Proxy (Stage M4)
# ==============================================================================

class StockSearchView(APIView):
    """GET /api/stock - Proxies stock video search to Pixabay provider."""

    def get(self, request, *args, **kwargs):
        q = request.query_params.get('q')
        if q is None:
            return Response(
                {'detail': [{'type': 'missing', 'loc': ['query', 'q'], 'msg': 'Field required', 'input': None}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        try:
            from studio.providers import stock_search
            results = stock_search(q)
            return Response(results)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response({'detail': f'Stock provider error: {str(exc)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

