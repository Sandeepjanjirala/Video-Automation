"""Read-only repository service layer for Telugu Reel Studio (Stage M2).

Provides encapsulated read-only query access to unmanaged domain entities.
No write/mutating operations are implemented here.
"""
from typing import List, Optional, Dict, Any
from django.db import connection
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


# ==============================================================================
# Workspaces
# ==============================================================================

def list_workspaces() -> List[Workspace]:
    """Return all workspaces."""
    return list(Workspace.objects.all().order_by('id'))


def get_workspace(workspace_id: str = 'default') -> Optional[Workspace]:
    """Get workspace by ID or return None."""
    try:
        return Workspace.objects.get(id=workspace_id)
    except Workspace.DoesNotExist:
        return None


# ==============================================================================
# Projects
# ==============================================================================

def list_projects(workspace_id: Optional[str] = None) -> List[Project]:
    """List projects, optionally filtered by workspace."""
    qs = Project.objects.all()
    if workspace_id:
        qs = qs.filter(workspace_id=workspace_id)
    return list(qs.order_by('-updated_at'))


def get_project(project_id: str) -> Optional[Project]:
    """Get project by ID or return None."""
    try:
        return Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return None


# ==============================================================================
# Revisions & Drafts
# ==============================================================================

def get_revision_history(project_id: str) -> List[Revision]:
    """Get all immutable revision snapshots for a project in ascending order."""
    return list(
        Revision.objects.filter(project_id=project_id).order_by('revision')
    )


def get_revision(project_id: str, revision_num: int) -> Optional[Revision]:
    """Get a specific revision snapshot by project ID and revision number."""
    try:
        return Revision.objects.get(project_id=project_id, revision=revision_num)
    except Revision.DoesNotExist:
        return None


def get_draft(project_id: str) -> Optional[Draft]:
    """Get draft editor state for a project or return None."""
    try:
        return Draft.objects.get(project_id=project_id)
    except Draft.DoesNotExist:
        return None


# ==============================================================================
# Assets
# ==============================================================================

def list_assets(workspace_id: Optional[str] = None, kind: Optional[str] = None) -> List[Asset]:
    """List assets with optional workspace and kind filters."""
    qs = Asset.objects.all()
    if workspace_id:
        qs = qs.filter(workspace_id=workspace_id)
    if kind:
        qs = qs.filter(kind=kind)
    return list(qs.order_by('-created_at'))


def get_asset(asset_id: str) -> Optional[Asset]:
    """Get asset metadata by ID or return None."""
    try:
        return Asset.objects.get(id=asset_id)
    except Asset.DoesNotExist:
        return None


# ==============================================================================
# Jobs & Stages
# ==============================================================================

def list_jobs(project_id: Optional[str] = None, state: Optional[str] = None) -> List[Job]:
    """List jobs with optional project and state filters."""
    qs = Job.objects.all()
    if project_id:
        qs = qs.filter(project_id=project_id)
    if state:
        qs = qs.filter(state=state)
    return list(qs.order_by('-created_at'))


def get_job(job_id: str) -> Optional[Job]:
    """Get job by ID or return None."""
    try:
        return Job.objects.get(id=job_id)
    except Job.DoesNotExist:
        return None


def get_job_stages(job_id: str) -> List[JobStage]:
    """Get stages for a specific job ordered chronologically."""
    return list(
        JobStage.objects.filter(job_id=job_id).order_by('created_at')
    )


# ==============================================================================
# Application Receipts & Outbox
# ==============================================================================

def list_receipts(project_id: Optional[str] = None, job_id: Optional[str] = None) -> List[ApplicationReceipt]:
    """List application receipts."""
    qs = ApplicationReceipt.objects.all()
    if project_id:
        qs = qs.filter(project_id=project_id)
    if job_id:
        qs = qs.filter(job_id=job_id)
    return list(qs.order_by('-applied_at'))


def list_outbox_events(processed: Optional[bool] = None) -> List[OutboxEvent]:
    """List outbox events."""
    qs = OutboxEvent.objects.all()
    if processed is not None:
        qs = qs.filter(processed=processed)
    return list(qs.order_by('-created_at'))


# ==============================================================================
# Voice Takes (Phase 4)
# ==============================================================================

def list_voice_takes(project_id: Optional[str] = None, scene_id: Optional[str] = None) -> List[VoiceTake]:
    """List voice takes with optional project and scene filters."""
    qs = VoiceTake.objects.all()
    if project_id:
        qs = qs.filter(project_id=project_id)
    if scene_id:
        qs = qs.filter(scene_id=scene_id)
    return list(qs.order_by('-created_at'))


def get_voice_take(take_id: str) -> Optional[VoiceTake]:
    """Get voice take by ID or return None."""
    try:
        return VoiceTake.objects.get(id=take_id)
    except VoiceTake.DoesNotExist:
        return None


# ==============================================================================
# Usage & Rate Limits (Composite PK: day, provider — Read-Only Direct Queries)
# ==============================================================================

def list_usage() -> List[Dict[str, Any]]:
    """List all usage tracking records via read-only SQL connection."""
    with connection.cursor() as cur:
        cur.execute("""
            SELECT day, provider, calls, estimated_billable_units,
                   actual_billable_units, uncertain_calls, last_call_at
            FROM usage
            ORDER BY day DESC, provider ASC
        """)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_usage(day: str, provider: str) -> Optional[Dict[str, Any]]:
    """Get specific daily usage record by composite key (day, provider)."""
    with connection.cursor() as cur:
        cur.execute("""
            SELECT day, provider, calls, estimated_billable_units,
                   actual_billable_units, uncertain_calls, last_call_at
            FROM usage
            WHERE day = %s AND provider = %s
        """, [day, provider])
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, row))
