"""Shared render timing, independent of Django, providers, and filesystem state.

Input cues are relative to the original narration audio. Output scene cues are
speed-adjusted; global cues additionally include the scene's frame-aligned offset.
Caption generation/alignment remains in media.captions during Stage 1.
"""
import math
from dataclasses import asdict, dataclass

FPS = 30


@dataclass(frozen=True)
class SceneTimeline:
    scene_id: str
    raw_voice_duration: float
    effective_voice_duration: float
    hold_seconds: float
    frame_count: int
    start_frame: int
    end_frame: int
    start_offset: float
    end_offset: float
    scene_duration: float
    cues: list
    alignment: str
    audio_offset: float = 0.0  # where this scene's narration starts in the export's sound


@dataclass(frozen=True)
class ProjectTimeline:
    duration_mode: str
    target_seconds: float
    speed: float
    total_frames: int
    total_duration: float
    scenes: list
    all_cues: list

    def to_manifest(self, *, project_id, revision, job_id, dimensions, measured_duration):
        return {
            'schema_version': 1,
            'project_id': project_id, 'revision': revision, 'job_id': job_id,
            'frame_rate': FPS, 'dimensions': list(dimensions),
            'time_unit': 'seconds',
            'scene_cue_timebase': 'scene-local after playback speed adjustment',
            'global_cue_timebase': 'reel-global after playback speed adjustment',
            'measured_export_duration': measured_duration,
            'caption_note': 'Automatic alignment is approximate; review the preview.',
            **asdict(self),
        }


def _number(value, label):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{label} must be finite')
    return value


def compute_timeline(project, prepared_voices):
    scenes = project.get('scenes', [])
    ids = [s['id'] for s in scenes]
    voices = {v['scene_id']: v for v in prepared_voices}
    if not scenes or len(set(ids)) != len(ids) or len(voices) != len(prepared_voices) or set(ids) != set(voices):
        raise ValueError('Every scene must have exactly one matching prepared narration')
    durations = [_number(voices[sid]['duration'], 'Narration duration') for sid in ids]
    holds = [_number(s.get('hold_seconds', 0), 'Scene hold') for s in scenes]
    if any(d <= 0 for d in durations) or any(h < 0 for h in holds):
        raise ValueError('Narration durations must be positive and scene holds nonnegative')
    mode = project.get('duration_mode', 'natural')
    if mode not in ('natural', 'target'):
        raise ValueError('Unknown duration mode')
    target = _number(project.get('target_seconds', 60), 'Target duration')
    speed = 1.0
    if mode == 'target':
        if target <= sum(holds):
            raise ValueError('Target duration is shorter than or equal to scene holds')
        speed = sum(durations) / (target - sum(holds))
        if not 0.8 <= speed <= 1.25:
            raise ValueError('Target needs excessive speed change. Edit the script, change the target, or use natural duration.')
    from .formats import of
    limits = of(project)
    if sum(durations) / speed + sum(holds) > limits['max_seconds'] + 0.5:
        raise ValueError(f"{limits['label']} supports up to {limits['max_seconds'] // 60} minutes. "
                         'Shorten the script or increase speech rate'
                         + (', or choose YouTube video.' if limits is not of({'format': 'youtube'}) else '.'))

    # One recording for the whole video plays without a gap or pad between scenes, so each picture cut
    # goes on the frame nearest its sound cut (the last one rounded up): never a drift, at most half a
    # frame off. Scenes with their own audio are each padded to whole frames, as before.
    continuous = project.get('narration_mode') == 'track'
    result, global_cues, cursor, audio = [], [], 0, 0.0
    for k, (scene, duration, hold) in enumerate(zip(scenes, durations, holds)):
        voice = voices[scene['id']]
        if continuous:
            sound_end = audio + duration / speed + hold
            frames = max(1, (math.ceil(sound_end * FPS - 1e-9) if k == len(scenes) - 1 else round(sound_end * FPS)) - cursor)
        else:
            frames = math.ceil((duration / speed + hold) * FPS)
            audio = cursor / FPS
        shift = audio - cursor / FPS  # the narration starts this much after the scene's first frame
        cues, last = [], 0.0
        for cue in voice.get('cues', []):
            start = _number(cue['start'], 'Caption start')
            end = _number(cue['end'], 'Caption end')
            # Retain the existing 100ms manual end tolerance; never silently
            # replace invalid manual edits with automatically estimated cues.
            if start < last or end <= start or end > duration + 0.1:
                raise ValueError('Caption cues must be ordered, non-overlapping, and within narration duration')
            adjusted = {**cue, 'start': max(0.0, start / speed + shift), 'end': min(frames / FPS, end / speed + shift)}
            cues.append(adjusted)
            global_cues.append({**adjusted, 'scene_id': scene['id'],
                                'start': cursor / FPS + adjusted['start'],
                                'end': cursor / FPS + adjusted['end']})
            last = end
        result.append(SceneTimeline(scene['id'], duration, duration / speed, hold,
                                    frames, cursor, cursor + frames, cursor / FPS,
                                    (cursor + frames) / FPS, frames / FPS, cues,
                                    voice.get('alignment', 'unknown'), audio_offset=audio))
        cursor += frames
        audio = sound_end if continuous else cursor / FPS
    return ProjectTimeline(mode, target, speed, cursor, cursor / FPS, result, global_cues)


def stamp(seconds, ass=False):
    units = round(seconds * (100 if ass else 1000))
    base = 100 if ass else 1000
    hours, rem = divmod(units, 3600 * base)
    mins, rem = divmod(rem, 60 * base)
    secs, fraction = divmod(rem, base)
    return f'{hours}:{mins:02}:{secs:02}.{fraction:02}' if ass else f'{hours:02}:{mins:02}:{secs:02},{fraction:03}'


def to_srt(cues):
    return '\n\n'.join(f"{i+1}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}"
                       for i, c in enumerate(cues)) + ('\n' if cues else '')
