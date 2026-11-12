"""The export's sound, built once for the whole video.

Every scene's narration is decoded, brought to the narration loudness, sped up or slowed down when the
project fits a target length, and padded to its place in the timeline as 48 kHz PCM; the parts are
joined sample-exactly, so one recording for the whole video plays exactly as recorded. The scenes'
clip sound and the music are mixed under it, a limiter keeps peaks below -1.5 dBFS, and the mix is
encoded to AAC once. (Joining separately encoded scene audio could click at every cut and pumped the
level, because each scene was normalised on its own.)
"""
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import narration, store
from .config import executable
from .timeline import FPS

RATE = narration.RATE
PER_FRAME = RATE // FPS   # 1600 samples of sound per video frame
LIMIT = 0.84              # limiter ceiling, about -1.5 dBFS
WORKERS = 4               # scene sound parts prepared at the same time
MUSIC_FADE_IN = 0.6       # seconds
MUSIC_FADE_OUT = 2.0      # seconds, at most a tenth of the video


def narration_gain(scene, project, path):
    """dB that brings a scene's narration to the export loudness. One recording for the whole video gets
    one value for all its scenes, so the speaker's own rises and falls are kept; the recording's volume
    control (already in its cut) comes on top."""
    if project.get('narration_mode') == 'track':
        found = narration.analyze(project['narration_track']['asset'])
    elif scene.get('audio_asset'):
        found = narration.analyze(scene['audio_asset'])
    else:
        found = narration.analyze_file(path)
    return narration.normal_gain(found['loudness'], found['peak'])


def ffmpeg(*args):
    return [executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', *args]


def narration_part(source, gain_db, speed, samples, output, run, job_id=None):
    """`samples` of 48 kHz float sound: the narration at `gain_db`, `speed`, padded with silence."""
    chain = f'aresample={RATE},asetpts=N/SR/TB,volume={gain_db:.2f}dB'
    if abs(speed - 1) > 1e-6:
        chain += f',atempo={speed:.8f}'
    chain += f',asetpts=N/SR/TB,apad=whole_len={samples},atrim=end_sample={samples}'
    run(ffmpeg('-i', source, '-map', '0:a:0', '-af', chain, '-ac', '2', '-c:a', 'pcm_f32le', output), job_id=job_id)


def clip_part(shot, path, source, seconds, samples, volume, output, run, job_id=None):
    """`samples` of a video clip's own sound as its picture plays in the scene (same trim, loop or freeze),
    at `volume`; silence for images and clips without sound."""
    from .framing import segment
    has_sound = shot.get('visual') != 'image' and source.get('audio')
    if not has_sound or volume <= 0:
        run(ffmpeg('-f', 'lavfi', '-i', f'anullsrc=r={RATE}:cl=stereo', '-af', f'atrim=end_sample={samples}',
                   '-c:a', 'pcm_f32le', output), job_id=job_id)
        return
    start, end = segment(shot, source['duration'])
    span = end - start
    chain = f'atrim=end={span:.6f},asetpts=PTS-STARTPTS,aresample={RATE},asetpts=N/SR/TB'
    if span < seconds and shot.get('short_clip_mode') != 'freeze':  # the picture loops, so does its sound
        chain += f',aloop=loop=-1:size={max(1, round(span * RATE))}'
    chain += f',volume={volume:.3f},apad=whole_len={samples},atrim=end_sample={samples}'
    run(ffmpeg('-ss', f'{start:.3f}', '-i', path, '-map', '0:a:0', '-af', chain, '-ac', '2', '-c:a', 'pcm_f32le', output),
        job_id=job_id)


def has_sound(path):
    from .media import probe
    return any(s.get('codec_type') == 'audio' for s in probe(path).get('streams', []))


def join(parts, output, run, job_id=None):
    listing = Path(output).with_suffix('.txt')
    listing.write_text('\n'.join(f"file '{Path(p).name}'" for p in parts), 'utf-8')
    run(ffmpeg('-f', 'concat', '-safe', '0', '-i', listing.name, '-c:a', 'pcm_f32le', Path(output).name),
        cwd=Path(output).parent, job_id=job_id)


def narration_bed(project, prepared, timeline, work, run, job_id=None):
    """The whole narration as one WAV, exactly as long as the video."""
    total = timeline.total_frames * PER_FRAME
    starts = [round(s.audio_offset * RATE) for s in timeline.scenes] + [total]
    jobs = []
    for k, (scene, voice, timing) in enumerate(zip(project['scenes'], prepared, timeline.scenes)):
        gain = narration_gain(scene, project, voice['audio_path'])
        jobs.append((voice['audio_path'], gain, timeline.speed, starts[k + 1] - starts[k], Path(work) / f'narration-{k}.wav'))

    def one(item):
        narration_part(*item, run, job_id)
        return item[4]
    with ThreadPoolExecutor(min(WORKERS, len(jobs))) as pool:
        parts = list(pool.map(one, jobs))
    output = Path(work) / 'narration.wav'
    join(parts, output, run, job_id)
    return output, [round(j[1], 2) for j in jobs]


def clip_bed(project, timeline, work, run, job_id=None):
    """The scenes' own clip sound (each at its clip volume) as one WAV as long as the video, or None
    when every scene's clip sound is muted."""
    from .framing import scene_shots, shot_lengths, source_info
    from .formats import of
    if not any(s.get('clip_volume', 0) > 0 and s.get('visual') in ('upload', 'stock') for s in project['scenes']):
        return None
    parts = []
    for k, (scene, timing) in enumerate(zip(project['scenes'], timeline.scenes)):
        samples = timing.frame_count * PER_FRAME
        volume = float(scene.get('clip_volume') or 0) if scene.get('visual') in ('upload', 'stock') else 0.0
        if volume <= 0:
            out = Path(work) / f'clip-{k}.wav'
            clip_part({'visual': 'image'}, None, {}, 0, samples, 0, out, run, job_id)
            parts.append(out)
            continue
        shots = scene_shots(scene, timing.scene_duration, of(project)['shot_seconds'])
        lengths = shot_lengths(scene, timing.scene_duration, len(shots))
        bounds = [0]
        for seconds in lengths:
            bounds.append(min(samples, bounds[-1] + round(seconds * RATE)))
        bounds[-1] = samples
        for n, (shot, seconds) in enumerate(zip(shots, lengths)):
            path = store.asset_path(shot['video_asset'], 'video')
            out = Path(work) / f'clip-{k}-{n}.wav'
            source = {**source_info(path), 'audio': has_sound(path)}
            clip_part(shot, path, source, seconds, bounds[n + 1] - bounds[n], volume, out, run, job_id)
            parts.append(out)
    output = Path(work) / 'clipsound.wav'
    join(parts, output, run, job_id)
    return output


def mix_graph(project, inputs, seconds):
    """FFmpeg filter graph mixing input 1 (narration), optional clip sound and music into [a]."""
    nar = '[1:a]'
    graph, mixed = [], [nar]
    index = 2
    if inputs.get('clip'):
        mixed.append(f'[{index}:a]')
        index += 1
    if inputs.get('music'):
        chain = f'[{index}:a]aresample={RATE},volume={float(project["music_gain"]):.4f}'
        if project.get('music_fade', True):
            fade_out = min(MUSIC_FADE_OUT, seconds / 10)
            chain += f',afade=t=in:d={MUSIC_FADE_IN},afade=t=out:st={max(0.0, seconds - fade_out):.3f}:d={fade_out:.3f}'
        chain += f',atrim=end={seconds:.6f}'
        if project.get('music_ducking', True):
            graph.append(f'{nar}asplit=2[nar][side]')
            graph.append(chain + '[music]')
            graph.append('[music][side]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[ducked]')
            mixed = ['[nar]'] + mixed[1:] + ['[ducked]']
        else:
            graph.append(chain + '[music]')
            mixed.append('[music]')
    if len(mixed) > 1:
        graph.append(''.join(mixed) + f'amix=inputs={len(mixed)}:duration=first:normalize=0[mix]')
        last = '[mix]'
    else:
        last = mixed[0]
    graph.append(f'{last}alimiter=limit={LIMIT}:attack=5:release=50:level=disabled:latency=1[a]')
    return ';'.join(graph)


def mux(video, narration_wav, clip_wav, music, project, seconds, output, run, job_id=None):
    """The final MP4: the joined picture (copied) and the mixed sound, encoded to AAC once, fast start."""
    args = ['-i', video, '-i', narration_wav]
    if clip_wav:
        args += ['-i', clip_wav]
    if music:
        args += ['-stream_loop', '-1', '-i', music]
    graph = mix_graph(project, {'clip': bool(clip_wav), 'music': bool(music)}, seconds)
    run(ffmpeg('-filter_complex_threads', '1', *args, '-filter_complex', graph, '-map', '0:v:0', '-map', '[a]',
               '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', str(RATE), '-ac', '2', '-t', f'{seconds:.6f}',
               '-movflags', '+faststart', output), job_id=job_id)


def moov_first(path):
    """True when the MP4's index (moov) comes before its media data, so playback starts before download ends."""
    with open(path, 'rb') as handle:
        while True:
            header = handle.read(8)
            if len(header) < 8:
                return False
            size, kind = int.from_bytes(header[:4], 'big'), header[4:8]
            if kind == b'moov':
                return True
            if kind == b'mdat':
                return False
            if size == 1:
                size = int.from_bytes(handle.read(8), 'big') - 8
            elif size < 8:
                return False
            handle.seek(size - 8, 1)


def inspect(path, run, timeout, job_id=None):
    """Decode the whole export (any decoding error fails it) and measure black picture, long silences
    and loudness. Returns plain-language findings for the export report."""
    log = run([executable('ffmpeg'), '-hide_banner', '-nostats', '-xerror', '-i', path,
               '-vf', 'blackdetect=d=0.3:pix_th=0.08', '-af', 'silencedetect=n=-45dB:d=2.5,ebur128=peak=true',
               '-f', 'null', '-'], log=True, timeout=timeout, job_id=job_id)
    black = [[float(a), float(b)] for a, b in re.findall(r'black_start:([\d.]+) black_end:([\d.]+)', log)]
    starts = [float(x) for x in re.findall(r'silence_start: (-?[\d.]+)', log)]
    ends = [float(x) for x in re.findall(r'silence_end: ([\d.]+)', log)]
    silences = [[round(max(0.0, s), 2), round(e, 2)] for s, e in zip(starts, ends + [None] * len(starts)) if e is not None]
    summary = log[log.rfind('Summary:'):]
    integrated = re.search(r'I:\s+(-?[\d.]+) LUFS', summary)
    peak = re.search(r'Peak:\s+(-?[\d.]+) dBFS', summary)
    found = {'black': black, 'silences': silences,
             'loudness': float(integrated.group(1)) if integrated else None,
             'true_peak': float(peak.group(1)) if peak else None}
    notes = []
    for a, b in black:
        notes.append(f'Black picture from {a:.1f} s to {b:.1f} s.')
    for a, b in silences:
        notes.append(f'No sound from {a:.1f} s to {b:.1f} s.')
    if found['true_peak'] is not None and found['true_peak'] > -0.5:
        notes.append(f"Sound peaks reach {found['true_peak']:.1f} dBTP and may distort on some phones.")
    if found['loudness'] is not None and not -20 <= found['loudness'] <= -12:
        notes.append(f"Overall loudness is {found['loudness']:.1f} LUFS; about -16 is usual for Reels and Shorts.")
    found['notes'] = notes
    return found
