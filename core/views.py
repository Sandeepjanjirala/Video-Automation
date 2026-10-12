"""Core read-only views for Telugu Reel Studio (Stages M1, M2, M3).

All views in Stage M3 are strictly read-only, matching FastAPI's API contracts.
Reuses domain business logic from studio.store, studio.config, and studio.voice.
"""
import subprocess
import secrets
from typing import Optional
from django.conf import settings
from django.db import connection
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
import redis

from studio.config import public_settings, executable
from studio import store
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
    """GET /api/projects - Lists all projects scoped to workspace."""

    def get(self, request, *args, **kwargs):
        ws_id = request.query_params.get('workspace_id', 'default')
        projs = Project.objects.filter(workspace_id=ws_id).order_by('-updated_at')
        return Response([serialize_project_dict(p) for p in projs])


class ProjectDetailView(APIView):
    """GET /api/projects/{pid} - Returns project detail or 400 if not found."""

    def get(self, request, pid: str, *args, **kwargs):
        try:
            proj = Project.objects.get(id=pid)
            return Response(serialize_project_dict(proj))
        except Project.DoesNotExist:
            return Response({'detail': 'Project not found'}, status=status.HTTP_400_BAD_REQUEST)


class ProjectHistoryView(APIView):
    """GET /api/projects/{pid}/history - Returns revision history ordered newest first."""

    def get(self, request, pid: str, *args, **kwargs):
        revs = Revision.objects.filter(project_id=pid).order_by('-revision')[:50]
        return Response([serialize_revision_dict(r) for r in revs])


class ProjectDraftView(APIView):
    """GET /api/projects/{pid}/draft - Returns autosave draft body or empty dict."""

    def get(self, request, pid: str, *args, **kwargs):
        draft = Draft.objects.filter(project_id=pid).first()
        if draft and draft.body:
            return Response(draft.body)
        return Response({})


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
# 4. Jobs & Takes
# ==============================================================================

class ProjectJobsView(APIView):
    """GET /api/projects/{pid}/jobs - Returns list of jobs for project."""

    def get(self, request, pid: str, *args, **kwargs):
        job_objs = Job.objects.filter(project_id=pid).order_by('-created_at')[:30]
        return Response([serialize_job_dict(j) for j in job_objs])


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
# 6. Assets
# ==============================================================================

class AssetListView(APIView):
    """GET /api/assets - Returns list of asset metadata."""

    def get(self, request, *args, **kwargs):
        ws_id = request.query_params.get('workspace_id', 'default')
        kind = request.query_params.get('kind')
        qs = Asset.objects.filter(workspace_id=ws_id)
        if kind:
            qs = qs.filter(kind=kind)
        assets = qs.order_by('-created_at')
        return Response([serialize_asset_dict(a) for a in assets])


class AssetDetailView(APIView):
    """GET /api/assets/{aid} - Returns asset metadata or 400 if not found."""

    def get(self, request, aid: str, *args, **kwargs):
        try:
            a = Asset.objects.get(id=aid)
            return Response(serialize_asset_dict(a))
        except Asset.DoesNotExist:
            return Response({'detail': 'Media asset not found'}, status=status.HTTP_400_BAD_REQUEST)
