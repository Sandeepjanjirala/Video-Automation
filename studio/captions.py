"""Measured narration timing for the caption editor.

Cue timestamps are relative to each scene's original narration audio, the timebase
that timeline.compute_timeline consumes. Measurements come only from audio the
renderer would use: an attached recording, or prepared narration whose speech
inputs still match the scene. Synthesis is contract-cached, so the latter is the
same file a render reuses. Nothing here estimates a duration from text.
"""
from pathlib import Path

from . import store
from .config import DATA
from .media import auto_captions, duration, voice_for
from .models import Project

# Inputs that change the synthesized audio (see store.invalidate_stale_scene_fields).
SPEECH_PROJECT_FIELDS = ('tts_provider', 'voice', 'speech_rate', 'delivery_preset', 'pronunciation_dict')
SPEECH_SCENE_FIELDS = ('narration', 'spoken_text', 'audio_asset', 'audio_text_hash', 'take_id', 'delivery_preset')


def speech_project(body):
    """Validate only what affects narration, so unsaved caption drafts never block measuring."""
    if not isinstance(body, dict) or not isinstance(body.get('scenes'), list):
        raise ValueError('Project body with scenes is required')
    data = {f: body[f] for f in SPEECH_PROJECT_FIELDS if f in body}
    data['scenes'] = [{**s, 'cues': []} if isinstance(s, dict) else s for s in body['scenes']]
    return Project.model_validate(data).model_dump()


def speech_key(project, scene):
    return store.digest({'project': {f: project.get(f) for f in SPEECH_PROJECT_FIELDS},
                         'scene': {f: scene.get(f) for f in SPEECH_SCENE_FIELDS}})


def servable_audio(path):
    """Prepared audio may only be served from the studio's cache or asset folders."""
    if not path:
        return None
    path = Path(path).resolve()
    allowed = ((DATA / 'cache').resolve(), (DATA / 'assets').resolve())
    return path if path.parent in allowed and path.is_file() else None


def _measured(scene, seconds, boundaries, source, audio_url):
    return {
        'scene_id': scene['id'], 'status': 'measured', 'source': source,
        'duration': seconds, 'audio_url': audio_url,
        'automatic': {
            name: dict(zip(('cues', 'alignment'), auto_captions(text, seconds, boundaries)))
            for name, text in (('roman', scene['roman']), ('telugu', scene['narration']))
        },
    }


def scene_timing(project, scene, voice_jobs):
    if scene.get('audio_asset'):
        try:
            path, boundaries = voice_for(scene, project, 'upload')
            seconds = duration(path)
        except (ValueError, OSError) as exc:
            return {'scene_id': scene['id'], 'status': 'stale', 'message': str(exc)}
        return _measured(scene, seconds, boundaries, 'Attached narration recording',
                         f"/api/assets/{scene['audio_asset']}/media")
    key = speech_key(project, scene)
    for job in voice_jobs:
        snapshot = job['snapshot']['project']
        old = next((s for s in snapshot.get('scenes', []) if s.get('id') == scene['id']), None)
        voice = next((v for v in (job['result'] or {}).get('voices', []) if v.get('scene_id') == scene['id']), None)
        if old and voice and speech_key(snapshot, old) == key and servable_audio(voice.get('audio_path')):
            # Jobs from before Stage 2 lack word timings; reset then estimates over the measured length.
            return _measured(scene, float(voice['duration']), voice.get('boundaries') or [],
                             f"Prepared narration (job {job['id'][:8]})",
                             f"/api/jobs/{job['id']}/audio/{scene['id']}")
    return {'scene_id': scene['id'], 'status': 'unmeasured',
            'message': 'Narration has not been prepared for the current text and voice. '
                       'Save, approve and choose Prepare narration to measure this scene.'}


def project_timing(project, voice_jobs):
    """voice_jobs: succeeded voice jobs for this project, newest first."""
    return [scene_timing(project, scene, voice_jobs) for scene in project['scenes']]
