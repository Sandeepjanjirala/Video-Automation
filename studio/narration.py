"""Narration recordings: analysis, scene placement and lossless cutting.

An uploaded recording is never changed. Analysis decodes it once (cached) for a waveform, its pauses
and its loudness. Trims, volume changes and scene slices are new 48 kHz WAV files in the cache, cut
sample-accurately from the decoded original, so the scene slices of one recording joined in order are
the recording itself. Durations come from the decoded audio, the timing authority, not from container
metadata (a variable-bitrate MP3 without a header can report the wrong length).

One recording for the whole video (project narration_mode 'track'): each scene's `track_start` is where
its words begin in the recording; a scene ends where the next one starts, the last one at the
recording's end (narration_track.end, 0 = the file's end).
"""
import array
import functools
import json
import math
import re
import sys
import threading
from pathlib import Path

from . import store
from .config import DATA, executable
from .media import probe, run

RATE = 48000              # cut and render sample rate
ANALYSIS_RATE = 16000     # analysis decodes mono 16 kHz
WINDOW = 0.02             # seconds per waveform and level window
MIN_PAUSE = 0.2           # shortest silence counted as a pause
TARGET_LUFS = -16.0       # narration loudness in the export
MAX_GAIN_DB = 12.0        # automatic loudness correction is limited to +-12 dB
PEAK_CEILING_DB = -1.0    # a recording's own volume control never lifts a sample peak above this
MIN_SCENE = 0.3           # shortest scene slice of a recording, seconds
MAX_SECONDS = 3600
VERSION = 1
FORMATS = ('MP3, M4A, WAV, AAC, OGG, OPUS, FLAC or WebM')


def _cache(name):
    (DATA / 'cache').mkdir(parents=True, exist_ok=True)
    return DATA / 'cache' / name


def _file_key(path):
    stat = Path(path).stat()
    return store.digest([Path(path).name, stat.st_size, stat.st_mtime_ns])


def decode_levels(path):
    """(seconds, [(peak 0-1, level dBFS)] per WINDOW) of the first audio stream, decoded mono 16 kHz."""
    raw = run([executable('ffmpeg'), '-v', 'error', '-i', path, '-map', '0:a:0', '-ac', '1', '-ar', str(ANALYSIS_RATE),
               '-f', 's16le', '-'], binary=True, timeout=600)
    samples = array.array('h')
    samples.frombytes(raw[:len(raw) // 2 * 2])
    if sys.byteorder == 'big':
        samples.byteswap()
    step = int(ANALYSIS_RATE * WINDOW)
    windows = []
    for i in range(0, len(samples), step):
        part = samples[i:i + step]
        peak = max(max(part), -min(part)) / 32768
        rms = math.sqrt(sum(x * x for x in part) / len(part)) / 32768
        windows.append((peak, 20 * math.log10(rms) if rms > 1e-5 else -100.0))
    return len(samples) / ANALYSIS_RATE, windows


def loudness(path):
    """(integrated loudness LUFS or None for silence, sample peak dBFS) measured by FFmpeg's EBU R128 meter."""
    log = run([executable('ffmpeg'), '-hide_banner', '-nostats', '-i', path, '-map', '0:a:0',
               '-af', 'ebur128=peak=sample', '-f', 'null', '-'], log=True, timeout=600)
    summary = log[log.rfind('Summary:'):]
    found = re.search(r'I:\s+(-?[\d.]+|-inf) LUFS', summary)
    peak = re.search(r'Peak:\s+(-?[\d.]+|-inf) dBFS', summary)
    value = float(found.group(1)) if found and found.group(1) != '-inf' else None
    return (value if value is not None and value > -70 else None,
            float(peak.group(1)) if peak and peak.group(1) != '-inf' else -100.0)


def find_pauses(windows):
    """Pauses as [start, end] seconds: runs of windows quieter than a threshold set between the
    recording's own background level and its speech level, so a noisy phone recording still works."""
    levels = sorted(level for _, level in windows)
    if not levels:
        return [], -100.0, -100.0
    noise = levels[int(len(levels) * 0.1)]
    speech = levels[int(len(levels) * 0.9)]
    threshold = noise + max(6.0, 0.35 * (speech - noise))
    pauses, begin = [], None
    for k, (_, level) in enumerate(windows + [(0, 0.0)]):  # a loud sentinel closes a final pause
        if level < threshold:
            begin = k if begin is None else begin
        elif begin is not None:
            if (k - begin) * WINDOW >= MIN_PAUSE:
                pauses.append([round(begin * WINDOW, 3), round(min(k, len(windows)) * WINDOW, 3)])
            begin = None
    return pauses, noise, speech


def speech_intervals(pauses, duration):
    """The parts between pauses."""
    out, cursor = [], 0.0
    for start, end in pauses:
        if start > cursor:
            out.append([round(cursor, 3), start])
        cursor = max(cursor, end)
    if cursor < duration:
        out.append([round(cursor, 3), round(duration, 3)])
    return out


def analyze_file(path, cache_key=None):
    """Waveform, pauses, speech and loudness of an audio file (cached by content or file identity)."""
    cached = _cache(f"{cache_key or _file_key(path)}.narration-v{VERSION}.json")
    if cached.is_file():
        try:
            return json.loads(cached.read_text('utf-8'))
        except ValueError:
            pass
    audio = next((s for s in probe(path).get('streams', []) if s.get('codec_type') == 'audio'), None)
    if not audio:
        raise ValueError(f'This file has no sound. Upload a voice recording ({FORMATS}).')
    seconds, windows = decode_levels(path)
    pauses, noise, speech = find_pauses(windows)
    integrated, peak = loudness(path)
    top = max((p for p, _ in windows), default=0) or 1
    result = {
        'version': VERSION, 'duration': round(seconds, 4), 'peaks_per_second': round(1 / WINDOW),
        'sample_rate': int(audio.get('sample_rate') or 0), 'channels': int(audio.get('channels') or 0),
        'codec': audio.get('codec_name') or '',
        'peaks': [round(100 * p / top) for p, _ in windows],
        'pauses': pauses, 'speech': speech_intervals(pauses, seconds),
        'loudness': integrated, 'peak': peak, 'noise_level': round(noise, 1), 'speech_level': round(speech, 1),
        'clipped_windows': sum(1 for p, _ in windows if p >= 0.999),
    }
    store.write_cache(cached, json.dumps(result))  # two scenes with the same narration may be measured at once
    return result


def check_recording(path):
    """Probe an uploaded narration file and return (stream info, problems that block its use)."""
    info = probe(path)
    audio = next((s for s in info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    if not audio:
        raise ValueError(f'This file has no sound. Upload a voice recording ({FORMATS}).')
    try:
        # A full decode finds files that are damaged or were only partly copied from the phone.
        run([executable('ffmpeg'), '-v', 'error', '-xerror', '-i', path, '-map', '0:a:0', '-f', 'null', '-'], timeout=600)
    except ValueError:
        raise ValueError('This recording could not be read to the end; it may be damaged or only partly uploaded. '
                         'Export or copy it again and upload the new file.') from None
    return audio


def analyze(aid):
    """Analysis of a narration asset, plus its stream details and plain-language warnings."""
    meta = store.asset(aid)
    if meta['kind'] not in ('audio', 'video'):
        raise ValueError(f'Choose a voice recording ({FORMATS}).')
    path = store.asset_path(aid, meta['kind'])
    result = dict(analyze_file(path, meta.get('sha256') or _file_key(path)))
    if result['duration'] < 0.5:
        raise ValueError('The recording is shorter than half a second.')
    if result['duration'] > MAX_SECONDS:
        raise ValueError('Recordings can be up to 60 minutes long.')
    result.update(asset_id=aid, name=meta.get('name') or '', warnings=recording_warnings(result, result['sample_rate']))
    return result


def recording_warnings(result, rate):
    warnings = []
    if 0 < rate < 16000:
        warnings.append(f'Recorded at {rate / 1000:g} kHz: voices can sound muffled. Record at 44.1 or 48 kHz if you can.')
    if result['loudness'] is None:
        warnings.append('The recording seems to be silent.')
    elif result['loudness'] < -35:
        warnings.append('The recording is very quiet; it is made louder for the video, which also raises background noise.')
    if result['clipped_windows'] >= 3 or result['peak'] >= -0.05 and result['clipped_windows']:
        warnings.append('The recording reaches full volume in places and may sound distorted. Record a little further from the microphone.')
    if result['speech_level'] - result['noise_level'] < 15:
        warnings.append('Background noise is loud compared with the voice, so pauses may not be found reliably. Check scene boundaries by ear.')
    speech = result['speech']
    if speech and speech[0][0] > 1.5:
        warnings.append(f'The recording starts with {speech[0][0]:.1f} s of silence; place the first scene where the speech begins.')
    return warnings


def normal_gain(integrated, peak, user_db=0.0, ceiling=None):
    """dB that brings a recording to TARGET_LUFS plus the user's adjustment, limited to +-MAX_GAIN_DB
    (and, with `ceiling`, so its sample peak stays at or below that many dBFS)."""
    gain = 0.0 if integrated is None else max(-MAX_GAIN_DB, min(MAX_GAIN_DB, TARGET_LUFS - integrated))
    gain += user_db
    if ceiling is not None:
        gain = min(gain, ceiling - peak)
    return round(gain, 2)


def user_gain(result, user_db):
    """The recording's own volume control as applied: a boost never lifts its peak above
    PEAK_CEILING_DB, so it cannot distort (a cut is applied as asked)."""
    user_db = float(user_db or 0)
    return round(min(user_db, max(0.0, PEAK_CEILING_DB - result['peak'])), 2) if user_db > 0 else user_db


def cut(aid, start=0.0, end=None, gain_db=0.0, clean=False):
    """Path of a cached 48 kHz stereo WAV of the recording (its cleaned copy with `clean`) from `start` to
    `end` seconds (None: its end) with the recording's volume control applied. Cuts are on exact sample
    positions of the decoded audio, so neighbouring slices meet without a gap or an overlap."""
    meta = store.asset(aid)
    path = cleaned(aid) if clean else store.asset_path(aid, meta['kind'])
    first = max(0, round(float(start) * RATE))
    last = None if end is None else round(float(end) * RATE)
    if last is not None and last - first < round(0.05 * RATE):
        raise ValueError('A narration part must be at least 0.05 seconds long')
    source = path.name if clean else meta.get('sha256') or _file_key(path)  # a cleaned copy is named by its contents
    key = store.digest(['cut', VERSION, source, first, last, round(float(gain_db), 2)])
    target = _cache(f'narr-{key[:40]}.wav')
    if target.is_file() and target.stat().st_size > 44:
        return target
    chain = f'aresample={RATE},asetpts=N/SR/TB,atrim=start_sample={first}' + (f':end_sample={last}' if last is not None else '')
    chain += ',asetpts=PTS-STARTPTS' + (f',volume={float(gain_db):.2f}dB' if gain_db else '')
    temp = store.temp_beside(target)
    try:
        run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-i', path, '-map', '0:a:0', '-af', chain,
             '-ac', '2', '-ar', str(RATE), '-c:a', 'pcm_s16le', temp], timeout=600)
        store.publish_file(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target


# Recording clean-up for the video (project recording_cleanup, on by default) --------------------------
# Phone and laptop recordings carry rumble, room hiss and uneven levels that the voice engines do not.
# A copy of the whole recording is cleaned once (cached): brought to the narration level, low rumble
# removed below 80 Hz, background noise reduced when there is some, and loud and soft words evened out
# by gentle compression. The whole file is cleaned at once, so its scene slices still join seamlessly,
# and it keeps the original's exact length and timing (the noise reduction's delay is taken out), so
# pauses, scene boundaries and caption times found in the original stay right.
CLEAN_VERSION = 1
NOISY_DB = -65  # background level above which noise is reduced (a quiet studio recording is left alone)
COMPRESS = 'acompressor=threshold=-20dB:ratio=2:attack=8:release=160:knee=6'
_cleaning, _cleaning_lock = {}, threading.Lock()


@functools.lru_cache(maxsize=1)
def denoise_delay():
    """Samples (48 kHz) by which FFmpeg's FFT noise reduction delays the sound (1200 in FFmpeg 7 to 9),
    measured once on a test signal, or None when it cannot be (the clean-up then skips noise reduction)."""
    signal = "aevalsrc=exprs='0.4*sin(2*PI*(300+900*sin(3*t))*t)*(0.6+0.4*sin(7*t))':s=48000:d=3"

    def pcm(chain):
        raw = run([executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', signal, '-af', chain, '-ac', '1',
                   '-f', 's16le', '-'], binary=True, timeout=60)
        out = array.array('h')
        out.frombytes(raw[:len(raw) // 2 * 2])
        return out
    try:
        before, after = pcm('anull'), pcm('afftdn=nr=12:nf=-50:tn=1')
    except ValueError:
        return None
    start = RATE

    def match(d):
        return sum(before[start + k] * after[start + d + k] for k in range(0, 24000, 3))
    coarse = max(range(0, 4000, 8), key=match)
    best = max(range(max(0, coarse - 8), coarse + 9), key=match)
    return best if match(best) > 0 else None


def cleanup_chain(result):
    """(FFmpeg filters, delay in samples to take out) cleaning a recording with this analysis."""
    pre = normal_gain(result['loudness'], result['peak'], ceiling=PEAK_CEILING_DB)
    chain = f'aformat=sample_fmts=fltp,volume={pre:.2f}dB,highpass=f=80:poles=2'
    delay = 0
    if result['noise_level'] > NOISY_DB and denoise_delay() is not None:
        floor = min(-20, max(-80, round(result['noise_level'] + pre)))
        chain += f',afftdn=nr=12:nf={floor}:tn=1'
        delay = denoise_delay()
    return chain + ',' + COMPRESS, delay


def cleaned(aid):
    """Path of the cached cleaned copy of a recording: 48 kHz mono 24-bit WAV, exactly as long as the
    recording decoded at 48 kHz."""
    meta = store.asset(aid)
    path = store.asset_path(aid, meta['kind'])
    chain, delay = cleanup_chain(analyze(aid))
    key = store.digest(['clean', CLEAN_VERSION, meta.get('sha256') or _file_key(path), chain, delay])
    target = _cache(f'clean-{key[:40]}.wav')
    with _cleaning_lock:
        lock = _cleaning.setdefault(key, threading.Lock())
    with lock:  # the render's parallel sound steps ask for it at once
        if target.is_file() and target.stat().st_size > 44:
            return target
        full = f'aresample={RATE},asetpts=N/SR/TB,{chain}'
        if delay:
            full += f',atrim=start_sample={delay},asetpts=N/SR/TB,apad=pad_len={delay}'
        temp = store.temp_beside(target)
        try:
            run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-i', path, '-map', '0:a:0', '-af', full,
                 '-ac', '1', '-ar', str(RATE), '-c:a', 'pcm_s24le', temp], timeout=1800)
            store.publish_file(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    return target


def cleans(project, aid):
    """True when this narration asset is cleaned for the video: a file the user uploaded, in a project that
    keeps the clean-up on (the default). A voice engine take accepted in Voice Lab is already clean."""
    return (project or {}).get('recording_cleanup', True) is not False and store.asset(aid).get('provider') in (None, '', 'upload')


def cut_key(path):
    """The cache key of a cut file, or None when `path` is not one (served by /api/narration/audio/<key>)."""
    match = re.fullmatch(r'narr-([0-9a-f]{40})\.wav', Path(path).name)
    return match.group(1) if match else None


def cut_path(key):
    if not re.fullmatch(r'[0-9a-f]{40}', key or ''):
        raise ValueError('Unknown narration audio')
    path = (DATA / 'cache' / f'narr-{key}.wav').resolve()
    if path.parent != (DATA / 'cache').resolve() or not path.is_file():
        raise ValueError('This narration audio is no longer prepared; reload the timing')
    return path


# One recording for the whole video ------------------------------------------------------------------

def track(project):
    """The project's whole-video recording settings, or None when scenes have their own narration."""
    if project.get('narration_mode') != 'track':
        return None
    return project.get('narration_track') or {}


def track_end(project, duration):
    end = float((track(project) or {}).get('end') or 0)
    return min(end, duration) if end > 0 else duration


def scene_ranges(project, duration):
    """[(start, end)] of every scene in the recording, in scene order. Raises a plain-language error
    naming the first scene that is not placed correctly."""
    scenes, last_end = project.get('scenes', []), track_end(project, duration)
    starts = [s.get('track_start') for s in scenes]
    for k, start in enumerate(starts, 1):
        if start is None:
            raise ValueError(f'Scene {k} has no place in the recording yet. Set where its words start on the Voice tab.')
        if start >= last_end - MIN_SCENE + 1e-6:
            raise ValueError(f'Scene {k} starts at {start:.2f} s, at or after the end of the recording ({last_end:.2f} s).')
        if k > 1 and start < starts[k - 2] + MIN_SCENE - 1e-6:
            raise ValueError(f'Scene {k} must start at least {MIN_SCENE} s after scene {k - 1}. Scenes follow the order of the recording.')
    return [(float(start), float(starts[k + 1]) if k + 1 < len(starts) else last_end) for k, start in enumerate(starts)]


def scene_range(project, scene_id):
    tr = track(project)
    if not tr or not tr.get('asset'):
        raise ValueError('Upload the narration recording on the Voice tab first.')
    result = analyze(tr['asset'])
    ids = [s['id'] for s in project['scenes']]
    return scene_ranges(project, result['duration'])[ids.index(scene_id)], result


def track_cut(project, scene_id):
    """This scene's slice of the whole-video recording (cleaned, unless the project turned that off), with
    the recording's volume control."""
    (start, end), result = scene_range(project, scene_id)
    tr = track(project)
    if cleans(project, tr['asset']):
        return cut(tr['asset'], start, end, user_gain(analyze_file(cleaned(tr['asset'])), tr.get('gain_db', 0)), clean=True)
    return cut(tr['asset'], start, end, user_gain(result, tr.get('gain_db', 0)))


def wall_time(speech, offset):
    """The time in the recording after `offset` seconds of speech (pauses skipped)."""
    for start, end in speech:
        if offset <= end - start:
            return start + offset
        offset -= end - start
    return speech[-1][1] if speech else 0.0


def snap(t, pauses, reach=1.0):
    """The middle of the nearest pause within `reach` seconds of `t`, or `t` itself."""
    best = min(pauses, key=lambda p: abs((p[0] + p[1]) / 2 - t), default=None)
    if best and abs((best[0] + best[1]) / 2 - t) <= reach:
        return round((best[0] + best[1]) / 2, 3)
    return round(t, 3)


def clip_speech(speech, begin, end):
    return [[max(s, begin), min(e, end)] for s, e in speech if e > begin and s < end]


def place_scenes(texts, result, begin=None, end=None):
    """Start times for scenes with these texts: the speech time is shared out by text length (pauses
    skipped) and each boundary moved to the nearest pause. An estimate to check by ear."""
    duration = result['duration']
    speech = result['speech'] or [[0.0, duration]]
    begin = speech[0][0] if begin is None else begin
    end = duration if end is None else end
    speech = clip_speech(speech, begin, end) or [[begin, end]]
    weights = [max(1, len((t or '').strip())) for t in texts]
    total_speech = sum(e - s for s, e in speech)
    starts = [round(max(0.0, begin - 0.15 if begin == speech[0][0] else begin), 3)]
    for k in range(1, len(texts)):
        ideal = wall_time(speech, total_speech * sum(weights[:k]) / sum(weights))
        starts.append(snap(ideal, [p for p in result['pauses'] if begin < p[0] and p[1] < end]))
    for k in range(1, len(starts)):  # keep every scene at least MIN_SCENE long, in order
        starts[k] = max(starts[k], round(starts[k - 1] + MIN_SCENE, 3))
    if starts and starts[-1] > end - MIN_SCENE:
        raise ValueError('The recording is too short for this many scenes.')
    return starts


def split_at_pauses(result, target=4.0, longest=8.0, begin=None, end=None):
    """Scene start times for a recording without a script: cuts at pauses, about every `target`
    seconds and never more than `longest` apart, preferring longer pauses."""
    duration = result['duration']
    speech = result['speech'] or [[0.0, duration]]
    begin = max(0.0, speech[0][0] - 0.15) if begin is None else begin
    end = duration if end is None else end
    pauses = [p for p in result['pauses'] if begin < p[0] and p[1] < end]
    starts, cursor = [round(begin, 3)], begin
    while end - cursor > longest:
        window = [p for p in pauses if cursor + 1.0 <= (p[0] + p[1]) / 2 <= cursor + longest]
        if window:
            best = max(window, key=lambda p: (p[1] - p[0]) - 0.15 * abs((p[0] + p[1]) / 2 - cursor - target))
            cursor = round((best[0] + best[1]) / 2, 3)
        else:  # no pause at all: cut at the quietest moment
            peaks, per = result['peaks'], result['peaks_per_second']
            first, last = int((cursor + target / 2) * per), int((cursor + longest) * per)
            cursor = round(min(range(first, min(last, len(peaks) - 1) + 1), key=lambda k: peaks[k]) / per, 3)
        starts.append(cursor)
    if len(starts) > 1 and end - starts[-1] < 1.0:  # a tiny last scene joins the one before it
        starts.pop()
    return starts
