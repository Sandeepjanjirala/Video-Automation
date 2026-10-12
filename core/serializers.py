"""DRF serializers for Telugu Reel Studio read-only API contracts (Stage M3).

Faithfully replicates FastAPI response payload structures for:
- Projects (list and detail)
- Revision history
- Autosave drafts
- Render preflight checks
- Distributed jobs
- Voice Lab takes
- Assets and capabilities
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from rest_framework import serializers

from .models import (
    Workspace,
    Project,
    Revision,
    Asset,
    Job,
    Draft,
    VoiceTake,
)


def format_iso(dt: Optional[datetime]) -> str:
    """Format datetime as ISO string with UTC timezone, matching FastAPI."""
    if not dt:
        return datetime.now(timezone.utc).isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def serialize_project_dict(p: Project) -> Dict[str, Any]:
    """Convert Project ORM instance to exact dictionary expected by API/frontend."""
    return {
        'id': p.id,
        'revision': p.revision,
        'approved': p.approved_revision == p.revision,
        'updated': format_iso(p.updated_at),
        'project': p.body
    }


def serialize_revision_dict(r: Revision) -> Dict[str, Any]:
    """Convert Revision ORM instance to exact dictionary expected by API/frontend."""
    return {
        'revision': r.revision,
        'created': format_iso(r.created_at),
        'project': r.body
    }


def serialize_job_dict(j: Job) -> Dict[str, Any]:
    """Convert Job ORM instance to exact dictionary expected by API/frontend."""
    return {
        'id': j.id,
        'project_id': j.project_id,
        'revision': j.revision,
        'kind': j.kind,
        'snapshot': j.snapshot,
        'state': j.state,
        'stage': j.stage or 'queued',
        'progress': j.progress or '',
        'result': j.result or {},
        'operation_key': j.operation_key,
        'lease_token': j.lease_token,
        'lease_expires_at': format_iso(j.lease_expires_at) if j.lease_expires_at else None,
        'cancelled': bool(j.cancelled),
        'created': format_iso(j.created_at),
        'updated': format_iso(j.updated_at),
        'applied': bool(j.applied),
        'applied_at': format_iso(j.applied_at) if j.applied_at else None
    }


def serialize_voice_take_dict(vt: VoiceTake) -> Dict[str, Any]:
    """Convert VoiceTake ORM instance to exact dictionary expected by API/frontend."""
    return {
        'id': vt.id,
        'project_id': vt.project_id,
        'scene_id': vt.scene_id or '',
        'provider': vt.provider,
        'voice_id': vt.voice_id,
        'model': vt.model or '',
        'preset': vt.preset or 'conversational',
        'spoken_text': vt.spoken_text,
        'roman_text': vt.roman_text or '',
        'asset_id': vt.asset_id,
        'audio_url': f'/api/assets/{vt.asset_id}/media' if vt.asset_id else '',
        'duration': vt.duration,
        'timing_data': vt.timing_data or [],
        'accepted': vt.accepted,
        'contract_key': vt.contract_key,
        'meta': vt.meta or {},
        'created_at': format_iso(vt.created_at)
    }


def serialize_asset_dict(a: Asset) -> Dict[str, Any]:
    """Convert Asset ORM instance to exact dictionary expected by API/frontend."""
    meta = dict(a.meta or {})
    meta.update({
        'id': a.id,
        'filename': a.filename,
        'name': a.name,
        'kind': a.kind,
        'duration': a.duration,
        'sha256': a.sha256,
        'provider': a.provider or 'upload',
        'external_id': a.external_id,
        'source_url': a.source_url or '',
        'license_url': a.license_url or '',
        'retrieved': format_iso(a.created_at)
    })
    return meta
