import json
import math
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from .config import DATA, executable, settings
from . import store


def run(args, cwd=None, timeout=1800, job_id=None):
    proc = None
    try:
        proc = subprocess.Popen(
            [str(a) for a in args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            start_new_session=os.name != 'nt',
        )
        if job_id:
            store.register_subprocess(job_id, proc.pid)
        stdout, stderr = proc.communicate(timeout=timeout)
        if proc.returncode:
            raise ValueError('Media processing failed: ' + stderr[-1500:])
        return stdout
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
            proc.communicate()
        raise ValueError('Media processing timed out. Shorten the source clip or try a draft render.') from None
    except OSError:
        raise ValueError('Unable to launch FFmpeg/FFprobe. Check executable paths in Settings.') from None
    finally:
        if proc and proc.poll() is None:
            proc.kill()
            proc.communicate()
        if job_id:
            store.unregister_subprocess(job_id, proc.pid if proc else None)


def probe(path):
    value = json.loads(run([executable('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path], timeout=45))
    duration = float(value.get('format', {}).get('duration', 0))
    if not math.isfinite(duration) or not 0 < duration <= 36000:
        raise ValueError('Media must have a valid duration between 0 and 36,000 seconds')
    return value


def duration(path):
    return float(probe(path)['format']['duration'])


def voice_for(scene, project, provider):
    if scene.get('audio_asset'):
        if scene.get('audio_text_hash') != store.digest(scene['narration']):
            raise ValueError('An uploaded narration no longer matches the script revision. Re-attach or reconfirm it.')
        boundaries = []
        if scene.get('take_id'):
            take = store.get_voice_take(scene['take_id'])
            if take and take.get('asset_id') == scene['audio_asset']:
                boundaries = take.get('timing_data') or []
        return store.asset_path(scene['audio_asset'], 'audio'), boundaries
    from .providers import synthesize
    text = scene.get('spoken_text') or scene['narration']
    preset = (scene.get('delivery_preset') if scene.get('take_id') else None) or project.get('delivery_preset', 'conversational')
    pdict = project.get('pronunciation_dict') or {}
    return synthesize(
        text=text,
        voice=project.get('voice', 'te-IN-ShrutiNeural'),
        rate=project.get('speech_rate', 0),
        provider=provider,
        delivery_preset=preset,
        pronunciation_dict=pdict
    )


def captions(scene, audio_duration, boundaries):
    if scene['cues']:
        if scene['cues'][-1]['end'] > audio_duration + 0.1:
            raise ValueError('A manual caption cue extends beyond its narration. Correct its timestamp.')
        return scene['cues'], 'manual'
    words = scene['roman'].split()
    if not words:
        return [], 'empty'
    # Index mapping is a useful initial alignment, not forced phonetic alignment.
    mapped = len(boundaries) == len(words) and all(b['end'] <= audio_duration + 0.2 for b in boundaries)
    weights = [max(len(w), 1) for w in words]
    total = sum(weights)
    cues, offset = [], 0
    for start in range(0, len(words), 4):
        stop = min(start + 4, len(words))
        if mapped:
            begin = boundaries[start]['start']
            end = boundaries[stop - 1]['end']
        else:
            begin = audio_duration * sum(weights[:start]) / total
            end = audio_duration * sum(weights[:stop]) / total
        cues.append({'start': max(offset, begin), 'end': min(audio_duration, end), 'text': ' '.join(words[start:stop])})
        offset = end
    return cues, 'word-index mapping; review required' if mapped else 'estimated from text length; review required'


def stamp(seconds, ass=False):
    units = round(seconds * (100 if ass else 1000))
    base = 100 if ass else 1000
    hours, rem = divmod(units, 3600 * base)
    mins, rem = divmod(rem, 60 * base)
    secs, fraction = divmod(rem, base)
    return f'{hours}:{mins:02}:{secs:02}.{fraction:02}' if ass else f'{hours:02}:{mins:02}:{secs:02},{fraction:03}'


def ass_text(text, width=32):
    safe = text.replace('\\', '/').replace('{', '(').replace('}', ')').replace('\r', ' ').replace('\n', ' ')
    return r'\N'.join(textwrap.wrap(safe, width=width))


def write_ass(path, cues, project, title=None, total=0):
    # Fixed 1080p design coordinates are scaled by libass for draft resolution.
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{project['caption_size']},&H00FFFFFF,&H00FFFFFF,&H00120E0B,&H88000000,-1,0,0,0,100,100,0,0,1,3,1,2,90,140,{project['caption_bottom']},1
Style: Title,Arial,78,&H00B9F8CF,&H00FFFFFF,&H00120E0B,&H00000000,-1,0,0,0,100,100,0,0,1,1,0,5,100,100,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    rows = [f"Dialogue: 0,{stamp(c['start'], True)},{stamp(c['end'], True)},Default,,0,0,0,,{ass_text(c['text'])}" for c in cues]
    if title:
        rows.insert(0, f'Dialogue: 0,0:00:00.00,{stamp(total, True)},Title,,0,0,0,,{{\\fad(250,200)}}{ass_text(title, 20)}')
    path.write_text(header + '\n'.join(rows), 'utf-8')


def prepare_voices(job):
    p = job['snapshot']['project']
    provider = job['snapshot'].get('options', {}).get('tts_provider') or settings()['tts_provider']
    prepared = []
    for i, scene in enumerate(p['scenes']):
        store.progress(job['id'], f"Preparing narration {i + 1}/{len(p['scenes'])}")
        path, boundaries = voice_for(scene, p, provider)
        seconds = duration(path)
        cues, alignment = captions(scene, seconds, boundaries)
        prepared.append({'scene_id': scene['id'], 'audio_path': str(path), 'duration': seconds,
                         'cues': cues, 'alignment': alignment})
    return prepared


def render(job):
    """Compatibility entry point for the shared leased rendering pipeline."""
    from .tasks import task_render_pipeline
    outcome = task_render_pipeline.run(job['id'])
    if outcome['status'] != 'succeeded':
        raise ValueError('Render is already owned, completed, or cancelled')
    return store.job(job['id'])['result']
