"""Core views for Telugu Reel Studio (Phase M1)."""
import subprocess
from django.conf import settings
from django.db import connection
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
import redis


class HealthCheckView(APIView):
    """Read-only health verification for Django, PostgreSQL, Redis, and Media tools."""

    def get(self, request, *args, **kwargs):
        # 1. Database Connectivity & Schema Verification (Read-Only)
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
                # Query public tables
                cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public';
                """)
                visible_tables = {row[0] for row in cursor.fetchall()}
                db_info["table_count"] = len(visible_tables)
                db_info["tables_visible"] = expected_tables.issubset(visible_tables)

                # Query alembic version
                if "alembic_version" in visible_tables:
                    cursor.execute("SELECT version_num FROM alembic_version LIMIT 1;")
                    row = cursor.fetchone()
                    if row:
                        db_info["current_alembic_revision"] = row[0]

                # Representative read-only query
                if "projects" in visible_tables:
                    cursor.execute("SELECT count(*) FROM projects;")
                    count_val = cursor.fetchone()[0]
                    db_info["read_query_verified"] = count_val >= 0

                db_info["connected"] = True
        except Exception as exc:
            db_info["error"] = str(exc)

        # 2. Redis Reachability (Non-mutating PING)
        redis_info = {
            "connected": False,
        }
        try:
            r = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2.0)
            redis_info["connected"] = bool(r.ping())
        except Exception as exc:
            redis_info["error"] = str(exc)

        # 3. Media Processing Tools (FFmpeg / FFprobe)
        from studio.config import executable
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
