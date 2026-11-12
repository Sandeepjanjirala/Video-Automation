import json
import math
import os
import re
import shutil
import subprocess
import textwrap
import time
from pathlib import Path
from .config import DATA, executable, settings
from . import formats, store


def short_paths(text):
    """FFmpeg names files by their full path; messages shown in the studio keep only the file name."""
    return re.sub(r'(?:[A-Za-z]:)?(?:[\\/][^\\/\r\n:*?"<>|]+)+[\\/]([^\\/\r\n:*?"<>|]+)', r'\1', text)


def run(args, cwd=None, timeout=1800, job_id=None, binary=False, log=False):
    """Run FFmpeg/FFprobe and return its output: text, raw bytes with `binary` (decoded audio), or
    with `log` the messages FFmpeg writes to stderr (loudness and silence measurements)."""
    proc = None
    try:
        proc = subprocess.Popen(
            [str(a) for a in args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            start_new_session=os.name != 'nt',
        )
        if job_id:
            store.register_subprocess(job_id, proc.pid)
        # A job's FFmpeg runs check every half second whether the job was cancelled and stop themselves:
        # a render encodes several scenes at once, and the job records only one process to kill.
        deadline = time.monotonic() + timeout
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.5 if job_id else timeout)
                break
            except subprocess.TimeoutExpired:
                if not job_id or time.monotonic() > deadline:
                    raise
                if store.job_cancelled(job_id):
                    proc.kill()
                    proc.communicate()
                    raise ValueError('The job was cancelled') from None
        stderr = stderr.decode('utf-8', 'replace')
        if proc.returncode:
            raise ValueError('Media processing failed: ' + short_paths(stderr[-1500:]).strip())
        if log:
            return stderr
        return stdout if binary else stdout.decode('utf-8', 'replace')
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


def audio_seconds(path):
    """A narration file's length from its decoded audio (cached), the timing authority for scenes: what
    the renderer plays. Container metadata can be wrong (a variable-bitrate MP3 without a header)."""
    from . import narration
    if narration.cut_key(path):  # a cut is 48 kHz PCM: its header holds the exact sample count
        stream = probe(path)['streams'][0]
        return int(stream['duration_ts']) / int(stream['sample_rate'])
    return narration.analyze_file(path)['duration']


def recorded(scene, project):
    """True when this scene's narration comes from a recording (one for the whole video, or its own)."""
    return project.get('narration_mode') == 'track' or bool(scene.get('audio_asset'))


def voice_for(scene, project, provider):
    """(narration audio path, word timings) for a scene. Word timings come only from a voice engine
    that reports them (Edge, Azure); recordings have none."""
    from . import narration
    if project.get('narration_mode') == 'track':
        return narration.track_cut(project, scene['id']), []
    if scene.get('audio_asset'):
        # The recording stays attached when its text is edited (it is the transcript of what was said);
        # preflight warns until the text and recording are confirmed together again.
        boundaries = []
        if scene.get('take_id'):
            take = store.get_voice_take(scene['take_id'])
            if take and take.get('asset_id') == scene['audio_asset']:
                boundaries = take.get('timing_data') or []
        trim_start, trim_end = float(scene.get('audio_trim_start') or 0), float(scene.get('audio_trim_end') or 0)
        if trim_start or trim_end or scene.get('audio_gain_db'):
            result = narration.analyze(scene['audio_asset'])
            path = narration.cut(scene['audio_asset'], trim_start, trim_end or None,
                                 narration.user_gain(result, scene.get('audio_gain_db')))
            # Word timings belong to the untrimmed take; shift them and keep the ones inside the trim.
            end = trim_end or result['duration']
            boundaries = [{**b, 'start': b['start'] - trim_start, 'end': b['end'] - trim_start}
                          for b in boundaries if b['start'] >= trim_start and b['end'] <= end]
            return path, boundaries
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


VOICE_WORKERS = 4  # narration requests at the same time (Edge, Sarvam and Azure all accept a few)


def prefetch_voices(project, provider, workers=VOICE_WORKERS):
    """Synthesize every scene's narration in parallel so the scene-by-scene loop that follows finds it
    in the speech cache (synthesize caches by text and voice settings). Errors are left for that loop
    to report with its own message."""
    from concurrent.futures import ThreadPoolExecutor
    from django.db import connections
    scenes = [s for s in project.get('scenes', []) if (s.get('narration') or '').strip() and not recorded(s, project)]
    if len(scenes) < 2:
        return

    def one(scene):
        try:
            voice_for(scene, project, provider)
        except Exception:
            pass
        finally:
            connections.close_all()  # this worker thread's own connection
    with ThreadPoolExecutor(min(workers, len(scenes))) as pool:
        list(pool.map(one, scenes))


def captions(scene, audio_duration, boundaries, speech=None):
    if scene['cues']:
        if scene['cues'][-1]['end'] > audio_duration + 0.1:
            raise ValueError('A manual caption cue extends beyond its narration. Correct its timestamp.')
        return scene['cues'], 'manual'
    return auto_captions(scene['roman'], audio_duration, boundaries, speech)


def speech_for(scene, project, path, audio_duration, boundaries=()):
    """The spoken parts of a scene's narration (pauses removed) in its own time, for timing captions
    when there are no word timings. Recordings use the whole recording's analysis, whose background
    level is known, clipped to the scene's part; generated audio is analysed on its own."""
    if boundaries:
        return None
    from . import narration
    try:
        if project.get('narration_mode') == 'track':
            (begin, end), found = narration.scene_range(project, scene['id'])
        elif scene.get('audio_asset'):
            found = narration.analyze(scene['audio_asset'])
            begin = float(scene.get('audio_trim_start') or 0)
            end = float(scene.get('audio_trim_end') or 0) or found['duration']
        else:
            found, begin, end = narration.analyze_file(path), 0.0, audio_duration
    except (ValueError, OSError):
        return None
    return [[round(s - begin, 3), round(e - begin, 3)] for s, e in narration.clip_speech(found['speech'], begin, end)] or None


# Alignment labels: only the first comes from measured word times, and it is still mapped by word order.
WORD_TIMING = 'voice engine word timings, matched in word order; review required'
PAUSE_TIMING = 'estimated: spread over the speech between pauses; review required'
LENGTH_TIMING = 'estimated from text length; review required'


def auto_captions(text, audio_duration, boundaries, speech=None):
    """Automatic caption cues of four words. With word timings from the voice engine whose word count
    matches, each cue takes its words' times. Otherwise the timing is an estimate: the speech time
    (pauses skipped, when `speech` intervals are known) shared out by word length."""
    words = text.split()
    if not words:
        return [], 'empty'
    mapped = len(boundaries) == len(words) and all(b['end'] <= audio_duration + 0.2 for b in boundaries)
    weights = [max(len(w), 1) for w in words]
    total = sum(weights)
    speech = [s for s in speech or [] if s[1] > s[0]]
    talk = sum(e - s for s, e in speech)

    def at(fraction):  # time in the narration when `fraction` of the speech has been said
        if talk < 0.3:
            return audio_duration * fraction
        rest = talk * fraction
        for s, e in speech:
            if rest <= e - s + 1e-9:
                return s + rest
            rest -= e - s
        return speech[-1][1]
    cues, offset = [], 0
    for start in range(0, len(words), 4):
        stop = min(start + 4, len(words))
        if mapped:
            begin = boundaries[start]['start']
            end = boundaries[stop - 1]['end']
        else:
            # A cue shows from just before its first word (or the start of its sentence after a pause).
            begin = at(sum(weights[:start]) / total)
            end = at(sum(weights[:stop]) / total)
            if talk >= 0.3:
                begin = next((s for s, e in speech if s <= begin + 1e-9 < e and begin - s < 0.35), begin)
        cues.append({'start': max(offset, begin), 'end': min(audio_duration, end), 'text': ' '.join(words[start:stop])})
        offset = end
    return cues, WORD_TIMING if mapped else PAUSE_TIMING if talk >= 0.3 else LENGTH_TIMING


# Compatibility import for callers; ASS and SRT share timestamp formatting.
from .timeline import stamp


def word_key(word):
    """A word compared without case or punctuation (Telugu letters and digits kept)."""
    return re.sub(r'[^0-9a-z\u0c00-\u0c7f]', '', (word or '').lower())


def emphasis_words(scene, project):
    """Words to highlight in this scene's captions: the scene's key words, or else the subject words of
    its stock search; numbers are always highlighted. Empty when the project's highlighting is off."""
    if (project or {}).get('caption_emphasis') != 'keywords':
        return None
    chosen = {word_key(w) for w in scene.get('emphasis') or []} - {''}
    if not chosen:
        chosen = {k for k in map(word_key, (scene.get('keywords') or '').split()) if len(k) >= 4}
    return chosen


# Yellow (&HBBGGRR) on white styles; white on the all-yellow style.
EMPHASIS_COLOUR = {'yellow_accent': '&HFFFFFF&'}


def ass_text(text, width=32, emphasis=None, colour='&H00D7FF&'):
    safe = text.replace('\\', '/').replace('{', '(').replace('}', ')').replace('\r', ' ').replace('\n', ' ')
    # Lines break only between words: a long Telugu word stays whole rather than being cut inside a
    # conjunct (textwrap counts vowel signs and viramas as characters).
    lines = textwrap.wrap(safe, width=width, break_long_words=False, break_on_hyphens=False)
    if emphasis is not None:
        # Tags go in after wrapping, so they never count towards the line width.
        def mark(word):
            key = word_key(word)
            return f'{{\\1c{colour}}}{word}{{\\r}}' if key and (key in emphasis or any(ch.isdigit() for ch in key)) else word
        lines = [' '.join(mark(word) for word in line.split(' ')) for line in lines]
    return r'\N'.join(lines)


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


def write_ass(path, cues, project, title=None, total=0, emphasis=None):
    """`emphasis`: the scene's words to highlight (emphasis_words), or None for plain captions."""
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
Style: TitleTelugu,{TELUGU_FONT},78,&H00B9F8CF,&H00FFFFFF,&H00120E0B,&H00000000,-1,0,0,0,100,100,0,0,1,1,0,5,100,100,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    rows = [f"Dialogue: 0,{stamp(c['start'], True)},{stamp(c['end'], True)},"
            f"{'Telugu' if TELUGU_SCRIPT.search(c['text']) else 'Default'},,0,0,0,,"
            f"{ass_text(c['text'], wrap, emphasis, EMPHASIS_COLOUR.get(project.get('caption_style'), '&H00D7FF&'))}"
            for c in cues]
    if title:
        style = 'TitleTelugu' if TELUGU_SCRIPT.search(title) else 'Title'  # Arial has no Telugu letters
        rows.insert(0, f'Dialogue: 0,0:00:00.00,{stamp(total, True)},{style},,0,0,0,,{{\\fad(250,200)}}{ass_text(title, 20 if wrap == 32 else 34)}')
    path.write_text(header + '\n'.join(rows), 'utf-8')


def prepare_voices(job):
    p = job['snapshot']['project']
    provider = job['snapshot'].get('options', {}).get('tts_provider') or settings()['tts_provider']
    prepared = []
    for i, scene in enumerate(p['scenes']):
        store.progress(job['id'], f"Preparing narration {i + 1}/{len(p['scenes'])}")
        path, boundaries = voice_for(scene, p, provider)
        seconds = audio_seconds(path)
        cues, alignment = captions(scene, seconds, boundaries, speech_for(scene, p, path, seconds, boundaries))
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
