import json
import math
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from .config import DATA, executable, settings
from . import formats, store


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


def probe(path, allow_still=False):
    value = json.loads(run([executable('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path], timeout=45))
    if allow_still:
        streams = value.get('streams', [])
        if (len(streams) != 1 or streams[0].get('codec_name') not in ('png', 'mjpeg', 'webp')
                or value.get('format', {}).get('format_name') not in ('image2', 'png_pipe', 'jpeg_pipe', 'webp_pipe')
                or not 0 < streams[0].get('width', 0) * streams[0].get('height', 0) <= 40000000):
            raise ValueError('Upload a still JPEG, PNG or WebP image, at most 40 megapixels.')
        value.setdefault('format', {})['duration'] = '0'
        return value
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
        voice=project.get('voice', 'te-IN-MohanNeural'),
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
    return auto_captions(scene['roman'], audio_duration, boundaries)


def auto_captions(text, audio_duration, boundaries):
    words = text.split()
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


# Compatibility import for callers; ASS and SRT share timestamp formatting.
from .timeline import stamp


def ass_text(text, width=32):
    safe = text.replace('\\', '/').replace('{', '(').replace('}', ')').replace('\r', ' ').replace('\n', ' ')
    return r'\N'.join(textwrap.wrap(safe, width=width))


# Captions are always ASS. The ass filter shapes complex scripts by default on every FFmpeg
# version; FFmpeg 7.1's subtitles filter lays Telugu out without shaping (broken conjuncts).
CAPTION_FILTER = 'ass=captions.ass'

# Arial has no Telugu glyphs. Nirmala UI ships with Windows; the Docker image installs Noto.
TELUGU_FONT = 'Nirmala UI' if os.name == 'nt' else 'Noto Sans Telugu'
TELUGU_SCRIPT = re.compile(r'[ఀ-౿]')


# Caption style presets as ASS colours (&HAABBGGRR, alpha 00 = opaque) and border settings.
# 'classic' is the original style and must stay byte-identical. Box styles pad by a quarter
# of the font size: BorderStyle 3 draws an OutlineColour box per line, BorderStyle 4 (libass
# 0.17+) one BackColour box per caption, so translucent lines never overlap and darken.
# libass also outlines BorderStyle 4 text, so its OutlineColour is fully transparent.
CAPTION_STYLES = {
    'classic': {'primary': '&H00FFFFFF', 'outline_colour': '&H00120E0B', 'back': '&H88000000',
                'border': 1, 'outline': 3, 'shadow': 1},
    'black_box': {'primary': '&H00FFFFFF', 'outline_colour': '&H00000000', 'back': '&H00000000',
                  'border': 3, 'outline': 'pad', 'shadow': 0},
    'yellow_accent': {'primary': '&H0000D7FF', 'outline_colour': '&H00000000', 'back': '&H88000000',
                      'border': 1, 'outline': 4, 'shadow': 2},
    'translucent': {'primary': '&H00FFFFFF', 'outline_colour': '&HFF000000', 'back': '&H66000000',
                    'border': 4, 'outline': 'pad', 'shadow': 0},
}


def caption_style_line(name, font, project):
    size = project['caption_size']
    preset = CAPTION_STYLES[project.get('caption_style') or 'classic']
    outline = round(size / 4) if preset['outline'] == 'pad' else preset['outline']
    # Reels keep their original margins; a landscape frame centres captions with wider side margins.
    left, right = (200, 200) if formats.landscape(project) else (90, 140)
    return (f"Style: {name},{font},{size},{preset['primary']},&H00FFFFFF,{preset['outline_colour']},{preset['back']},"
            f"-1,0,0,0,100,100,0,0,{preset['border']},{outline},{preset['shadow']},2,{left},{right},{formats.caption_margin(project)},1")


def write_ass(path, cues, project, title=None, total=0):
    # Fixed 1080p design coordinates are scaled by libass for draft resolution.
    width, height = formats.size(project)
    wrap = formats.of(project)['caption_chars']
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{caption_style_line('Default', 'Arial', project)}
{caption_style_line('Telugu', TELUGU_FONT, project)}
Style: Title,Arial,78,&H00B9F8CF,&H00FFFFFF,&H00120E0B,&H00000000,-1,0,0,0,100,100,0,0,1,1,0,5,100,100,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    rows = [f"Dialogue: 0,{stamp(c['start'], True)},{stamp(c['end'], True)},"
            f"{'Telugu' if TELUGU_SCRIPT.search(c['text']) else 'Default'},,0,0,0,,{ass_text(c['text'], wrap)}"
            for c in cues]
    if title:
        rows.insert(0, f'Dialogue: 0,0:00:00.00,{stamp(total, True)},Title,,0,0,0,,{{\\fad(250,200)}}{ass_text(title, 20 if wrap == 32 else 34)}')
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
