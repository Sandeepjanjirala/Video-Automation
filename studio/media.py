import json
import math
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from .config import DATA, executable, settings
from . import store


def run(args, cwd=None, timeout=1800):
    try:
        result = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise ValueError('Media processing timed out. Shorten the source clip or try a draft render.') from None
    except OSError:
        raise ValueError('Unable to launch FFmpeg/FFprobe. Check executable paths in Settings.') from None
    if result.returncode:
        raise ValueError('Media processing failed: ' + result.stderr[-1500:])
    return result.stdout


def probe(path):
    value = json.loads(run([executable('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path], timeout=45))
    duration = float(value.get('format', {}).get('duration', 0))
    if not math.isfinite(duration) or not 0 < duration <= 36000:
        raise ValueError('Media must have a valid duration between 0 and 36,000 seconds')
    return value


def duration(path):
    return float(probe(path)['format']['duration'])


def voice_for(scene, project, provider):
    if scene['audio_asset']:
        if scene['audio_text_hash'] != store.digest(scene['narration']):
            raise ValueError('An uploaded narration no longer matches the script revision. Re-attach or reconfirm it.')
        return store.asset_path(scene['audio_asset'], 'audio'), []
    from .providers import synthesize
    return synthesize(scene['narration'], project['voice'], project['speech_rate'], provider)


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
    p = job['snapshot']['project']
    pf = store.preflight(p)
    if not pf['ready']:
        raise ValueError(f"Preflight validation failed: {pf['errors'][0]['message']}")
    prepared = prepare_voices(job)
    voice_total = sum(v['duration'] for v in prepared)
    holds = sum(s['hold_seconds'] for s in p['scenes'])
    speed = 1.0
    if p['duration_mode'] == 'target':
        if p['target_seconds'] <= holds:
            raise ValueError('Target duration is shorter than the scene holds')
        speed = voice_total / (p['target_seconds'] - holds)
        if not 0.8 <= speed <= 1.25:
            raise ValueError(f"Narration plus holds is {voice_total + holds:.1f}s. Target needs excessive speed change. Edit the script, change the target, or use natural duration.")
    natural = voice_total / speed + holds
    if natural > 180.5:
        raise ValueError('This release supports up to 180 seconds. Shorten the script or increase speech rate.')
    draft = job['snapshot']['options'].get('quality', 'final') == 'draft'
    width, height = (540, 960) if draft else (1080, 1920)
    temp = DATA / 'tmp' / job['id']
    temp.mkdir(exist_ok=True)
    scene_files, all_cues, report_scenes = [], [], []
    offset = 0.0
    for i, (scene, voice) in enumerate(zip(p['scenes'], prepared)):
        store.progress(job['id'], f"Rendering scene {i + 1}/{len(p['scenes'])} ({'draft' if draft else '1080p'})")
        # Frame quantization makes independently cached scenes concatenate predictably.
        length = math.ceil((voice['duration'] / speed + scene['hold_seconds']) * 30) / 30
        cues = [{**c, 'start': c['start'] / speed, 'end': c['end'] / speed} for c in voice['cues']]
        cache_key = store.digest({'renderer': 2, 'scene': scene, 'audio': Path(voice['audio_path']).name,
                                  'speed': speed, 'width': width, 'caption_size': p['caption_size'],
                                  'caption_bottom': p['caption_bottom']})
        cached = DATA / 'cache' / (cache_key + '.scene.mp4')
        if not cached.exists():
            work = temp / str(i)
            work.mkdir(exist_ok=True)
            write_ass(work / 'captions.ass', cues, p,
                      scene['graphic_title'] if scene['visual'] == 'graphic' else None, length)
            args = [executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-filter_threads', '1']
            if scene['visual'] == 'graphic':
                args += ['-f', 'lavfi', '-i', f'color=c=0x12251F:s={width}x{height}:r=30:d={length}']
                vf = f"drawbox=x=0:y={int(height * .24)}:w=iw:h=4:color=0xB9F8CF:t=fill"
            else:
                args += ['-stream_loop', '-1', '-ss', str(scene['trim_start']), '-i', store.asset_path(scene['video_asset'], 'video')]
                if scene['fit'] == 'crop':
                    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}:(iw-ow)*{scene['crop_x']}:(ih-oh)/2"
                else:
                    vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x12251F"
            output = work / 'scene.mp4'
            args += ['-i', voice['audio_path'], '-map', '0:v:0', '-map', '1:a:0',
                     '-vf', vf + ",setsar=1,fps=30,subtitles=captions.ass",
                     '-af', f'atempo={speed:.8f},loudnorm=I=-16:TP=-1.5:LRA=11,apad',
                     '-t', str(length), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '24' if draft else '20',
                     '-pix_fmt', 'yuv420p', '-threads', '4', '-c:a', 'aac', '-b:a', '192k',
                     '-ar', '48000', '-ac', '2', '-movflags', '+faststart', output]
            run(args, cwd=work)
            probe(output)
            try:
                output.replace(cached)
            except OSError:
                if not cached.exists():
                    shutil.copyfile(output, cached)
                output.unlink(missing_ok=True)
        scene_files.append(cached)
        all_cues.extend([{**c, 'start': c['start'] + offset, 'end': c['end'] + offset} for c in cues])
        report_scenes.append({'scene_id': scene['id'], 'duration': length, 'alignment': voice['alignment'],
                              'visual': store.asset(scene['video_asset']) if scene['visual'] != 'graphic' else {'provider': 'explicit graphic'},
                              'source_audio_muted': True})
        offset += length
    listing = temp / 'scenes.txt'
    # Generated cache names only: no user-controlled concat directives or path quoting.
    for i, source in enumerate(scene_files):
        shutil.copyfile(source, temp / f'scene-{i}.mp4')
    listing.write_text('\n'.join(f"file 'scene-{i}.mp4'" for i in range(len(scene_files))), 'utf-8')
    store.progress(job['id'], 'Joining scenes and mixing audio')
    joined = temp / 'joined.mp4'
    run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '1',
         '-i', 'scenes.txt', '-c', 'copy', '-movflags', '+faststart', joined], cwd=temp)
    final = temp / 'reel.mp4'
    if p['music_asset']:
        run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-filter_complex_threads', '1',
             '-i', joined, '-stream_loop', '-1', '-i', store.asset_path(p['music_asset'], 'audio'),
             '-filter_complex', f"[0:a]asplit=2[v][side];[1:a]volume={p['music_gain']}[m];[m][side]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[duck];[v][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]",
             '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', str(offset), '-movflags', '+faststart', final])
    else:
        shutil.copyfile(joined, final)
    store.progress(job['id'], 'Validating output video and audio')
    info = probe(final)
    video = next((s for s in info['streams'] if s['codec_type'] == 'video'), {})
    audio = next((s for s in info['streams'] if s['codec_type'] == 'audio'), {})
    if (video.get('width'), video.get('height')) != (width, height) or not audio or abs(float(info['format']['duration']) - offset) > 0.5:
        raise ValueError('Export validation failed: dimensions, audio, or duration did not match')
    run([executable('ffmpeg'), '-v', 'error', '-xerror', '-i', final, '-f', 'null', '-'], timeout=600)
    (temp / 'captions.srt').write_text('\n\n'.join(f"{i+1}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i, c in enumerate(all_cues)) + '\n', 'utf-8')
    report = {'project_id': job['project_id'], 'revision': job['revision'], 'created': store.now(),
              'duration': float(info['format']['duration']), 'dimensions': [width, height], 'scenes': report_scenes,
              'caption_note': 'Automatic Roman Telugu timings need preview review. Manual cues override automatic timings.',
              'evidence_notes': p['evidence_notes'], 'research_sources': p['research_sources'],
              'music': store.asset(p['music_asset']) if p['music_asset'] else None,
              'speech_provider': job['snapshot']['options']['tts_provider']}
    (temp / 'source-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), 'utf-8')
    (temp / 'project.json').write_text(json.dumps(job['snapshot'], indent=2, ensure_ascii=False), 'utf-8')
    destination = DATA / 'exports' / job['id']
    destination.mkdir(exist_ok=True)
    for name in ('reel.mp4', 'captions.srt', 'source-report.json', 'project.json'):
        shutil.copyfile(temp / name, destination / name)
    shutil.rmtree(temp)
    return {'export_id': job['id'], 'duration': report['duration'], 'dimensions': report['dimensions'],
            'files': ['reel.mp4', 'captions.srt', 'source-report.json', 'project.json']}
