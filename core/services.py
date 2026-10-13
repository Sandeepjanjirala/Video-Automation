"""Domain mutation services for Telugu Reel Studio (Stage M6).

All mutating operations run inside an atomic transaction on the dedicated
`project_writer` database alias, backed by the least-privilege
`django_project_writer` PostgreSQL role.
"""
import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction

from studio.models import Project as PydanticProject
from studio.store import compute_script_hash, digest
from .models import (
    Workspace,
    Project,
    Revision,
    Draft,
    Job,
    ApplicationReceipt,
    Asset,
    VoiceTake,
)
from .serializers import serialize_project_dict, serialize_voice_take_dict


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def save_project_mutation(
    project_dict: Dict[str, Any],
    pid: Optional[str] = None,
    expected_revision: Optional[int] = None
) -> Tuple[Project, bool]:
    """Create or update a Project document with optimistic locking and immutable revision history.
    
    Returns (project, was_modified).
    """
    validated = PydanticProject.model_validate(project_dict).model_dump()
    pid = pid or uuid.uuid4().hex

    with transaction.atomic(using='project_writer'):
        old = Project.objects.using('project_writer').select_for_update().filter(id=pid).first()

        if old and expected_revision is not None and old.revision != expected_revision:
            raise ValueError('This project changed in another tab. Reload before saving.')

        now_dt = now_utc()

        if old:
            old_data = old.body
            # Preserve script_approved_hash if script content didn't change
            if compute_script_hash(old_data) == compute_script_hash(validated):
                validated['script_approved_hash'] = old_data.get('script_approved_hash', '')
            
            # Check for exact equality (no-op save)
            if old_data == validated:
                return old, False

            new_revision = old.revision + 1
            old.revision = new_revision
            old.body = validated
            old.approved_revision = None  # Modifications invalidate approval
            old.updated_at = now_dt
            old.save(using='project_writer', update_fields=['revision', 'body', 'approved_revision', 'updated_at'])
            target_p = old
        else:
            new_revision = 1
            target_p = Project(
                id=pid,
                workspace_id='default',
                revision=new_revision,
                approved_revision=None,
                body=validated,
                created_at=now_dt,
                updated_at=now_dt,
            )
            target_p.save(using='project_writer')

        # Insert immutable revision snapshot
        Revision.objects.using('project_writer').create(
            project_id=pid,
            revision=new_revision,
            body=validated,
            created_at=now_dt,
        )

        # Clear autosave draft once project is saved
        Draft.objects.using('project_writer').filter(project_id=pid).delete()

        return target_p, True


def approve_project(pid: str, revision: int) -> Project:
    """Approve a specific project revision if validation criteria are met."""
    with transaction.atomic(using='project_writer'):
        p = Project.objects.using('project_writer').select_for_update().filter(id=pid).first()
        if not p or p.revision != revision:
            raise ValueError('Save and reload the current revision before approving')

        model = PydanticProject.model_validate(p.body)
        if not model.scenes or any(not s.narration.strip() or not s.roman.strip() for s in model.scenes):
            raise ValueError('Every scene needs Telugu narration text and Roman Telugu captions')
        if not model.evidence_notes.strip():
            raise ValueError('Add your factual review notes, or label fictional content, before approval')

        now_dt = now_utc()
        p_dict = model.model_dump()
        p_dict['script_approved_hash'] = compute_script_hash(p_dict)
        p.body = p_dict
        p.approved_revision = revision
        p.updated_at = now_dt
        p.save(using='project_writer', update_fields=['body', 'approved_revision', 'updated_at'])
        return p


def save_draft_data(pid: str, draft_data: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert client autosave draft."""
    now_dt = now_utc()
    with transaction.atomic(using='project_writer'):
        Draft.objects.using('project_writer').update_or_create(
            project_id=pid,
            defaults={'body': draft_data, 'updated_at': now_dt}
        )
    return {'project_id': pid, 'updated': now_dt.isoformat()}


def delete_draft_data(pid: str) -> None:
    """Discard client autosave draft."""
    with transaction.atomic(using='project_writer'):
        Draft.objects.using('project_writer').filter(project_id=pid).delete()


def apply_job_result(pid: str, jid: str, force: bool = False) -> Dict[str, Any]:
    """Apply completed background job results to project with field/scene conflict detection."""
    with transaction.atomic(using='project_writer'):
        j = Job.objects.using('project_writer').select_for_update().filter(id=jid, project_id=pid).first()
        if not j:
            raise ValueError('Job not found for this project')

        p = Project.objects.using('project_writer').select_for_update().filter(id=pid).first()
        if not p:
            raise ValueError('Project not found')

        current_project = copy.deepcopy(p.body)
        current_rev = p.revision

        # Idempotent apply: return previous result if already applied
        if j.applied:
            return {
                'project': serialize_project_dict(p),
                'applied': True,
                'conflicts': [],
                'revision': current_rev,
                'message': 'Job results were already applied.'
            }

        if j.state != 'succeeded':
            raise ValueError(f"Job is in state '{j.state}'; only succeeded jobs can be applied")

        kind = j.kind
        conflicts: List[Dict[str, Any]] = []
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
                    conflicts.append({
                        'scene_id': target_scene_id,
                        'reason': 'Scene has a newer manual video assignment; edit was preserved'
                    })
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
                    conflicts.append({
                        'scene_id': sid,
                        'reason': f'Scene {sid} has a newer manual video selection; edit was preserved'
                    })
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
                conflicts.append({
                    'scene_id': 'all',
                    'reason': 'Current project has newer script edits; draft replacement skipped to prevent overwrite'
                })
            else:
                current_project['scenes'] = draft_scenes
                modified = True

        else:
            raise ValueError(f"Job kind '{kind}' does not produce an applicable storyboard result")

        now_dt = now_utc()
        new_rev = current_rev

        if modified:
            new_rev = current_rev + 1
            validated_project = PydanticProject.model_validate(current_project).model_dump()
            p.revision = new_rev
            p.body = validated_project
            p.approved_revision = None
            p.updated_at = now_dt
            p.save(using='project_writer', update_fields=['revision', 'body', 'approved_revision', 'updated_at'])

            Revision.objects.using('project_writer').create(
                project_id=pid,
                revision=new_rev,
                body=validated_project,
                created_at=now_dt
            )
            Draft.objects.using('project_writer').filter(project_id=pid).delete()

        # Update job applied status
        j.applied = True
        j.applied_at = now_dt
        j.save(using='project_writer', update_fields=['applied', 'applied_at'])

        # Record receipt
        ApplicationReceipt.objects.using('project_writer').create(
            job_id=jid,
            project_id=pid,
            applied_revision=new_rev,
            conflicts=conflicts,
            applied_at=now_dt
        )

        return {
            'project': serialize_project_dict(p),
            'applied': True,
            'conflicts': conflicts,
            'revision': new_rev
        }


def assign_media_to_scenes(pid: str, asset_id: str, scene_ids: List[str]) -> Project:
    """Assign an uploaded/sensed video or audio asset to project scenes."""
    with transaction.atomic(using='project_writer'):
        p = Project.objects.using('project_writer').select_for_update().filter(id=pid).first()
        if not p:
            raise ValueError('Project not found')

        asset_obj = Asset.objects.using('project_writer').filter(id=asset_id).first()
        if not asset_obj:
            raise ValueError('Media asset not found')

        p_data = copy.deepcopy(p.body)
        scene_id_set = set(scene_ids)
        all_scenes = 'all' in scene_id_set

        for s in p_data.get('scenes', []):
            if all_scenes or s['id'] in scene_id_set:
                if asset_obj.kind == 'video':
                    s['video_asset'] = asset_id
                    s['visual'] = 'upload'
                    s['trim_start'] = 0
                    s['rights_reviewed'] = True
                elif asset_obj.kind == 'audio':
                    s['audio_asset'] = asset_id
                    s['audio_text_hash'] = digest(s.get('narration', ''))

        updated_p, _ = save_project_mutation(p_data, pid=pid, expected_revision=p.revision)
        return updated_p


def set_project_voice_lock(pid: str, locked: bool) -> Project:
    """Toggle voice profile lock on project."""
    with transaction.atomic(using='project_writer'):
        p = Project.objects.using('project_writer').select_for_update().filter(id=pid).first()
        if not p:
            raise ValueError('Project not found')
        p_data = copy.deepcopy(p.body)
        p_data['voice_locked'] = locked
        updated_p, _ = save_project_mutation(p_data, pid=pid, expected_revision=p.revision)
        return updated_p


def set_project_pronunciation_dict(pid: str, dictionary: Dict[str, str]) -> Project:
    """Update custom pronunciation dictionary overrides on project."""
    with transaction.atomic(using='project_writer'):
        p = Project.objects.using('project_writer').select_for_update().filter(id=pid).first()
        if not p:
            raise ValueError('Project not found')
        p_data = copy.deepcopy(p.body)
        p_data['pronunciation_dict'] = dictionary
        updated_p, _ = save_project_mutation(p_data, pid=pid, expected_revision=p.revision)
        return updated_p


def accept_voice_take_mutation(pid: str, take_id: str) -> Dict[str, Any]:
    """Atomically accept a voice take and update the project revision.
    
    Uses dedicated django_voice_writer role via voice_writer database alias.
    """
    with transaction.atomic(using='voice_writer'):
        take = VoiceTake.objects.using('voice_writer').select_for_update().filter(id=take_id).first()
        if not take or take.project_id != pid:
            raise ValueError(f"Voice take '{take_id}' not found for project '{pid}'")

        if take.scene_id:
            VoiceTake.objects.using('voice_writer').filter(
                project_id=pid,
                scene_id=take.scene_id
            ).exclude(id=take_id).update(accepted=False)
        take.accepted = True
        take.save(using='voice_writer', update_fields=['accepted'])

        proj = Project.objects.using('voice_writer').select_for_update().filter(id=pid).first()
        if not proj:
            raise ValueError(f"Project '{pid}' not found")

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

        now_dt = now_utc()
        new_rev = proj.revision + 1
        validated = PydanticProject.model_validate(p_data).model_dump()
        proj.revision = new_rev
        proj.body = validated
        proj.approved_revision = None  # Modifications invalidate approval
        proj.updated_at = now_dt
        proj.save(using='voice_writer', update_fields=['revision', 'body', 'approved_revision', 'updated_at'])

        Revision.objects.using('voice_writer').create(
            project_id=pid,
            revision=new_rev,
            body=validated,
            created_at=now_dt
        )

        return {
            'take': serialize_voice_take_dict(take),
            'project': serialize_project_dict(proj)
        }

