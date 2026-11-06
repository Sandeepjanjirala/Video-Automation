import json
import math
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
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
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
        raise ValueError('Media processing timed out. Shorten the source clip or try a draft render.') from None
    except OSError:
        raise ValueError('Unable to launch FFmpeg/FFprobe. Check executable paths in Settings.') from None
    finally:
        if job_id:
            store.unregister_subprocess(job_id)


def probe(path):
    value = json.loads(run([executable('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path], timeout=45))
    duration = float(value.get('format', {}).get('duration', 0))
    if not math.isfinite(duration) or not 0 < duration <= 36000:
        raise ValueError('Media must have a valid duration between 0 and 36,000 seconds')
    v_stream = next((s for s in value.get('streams', []) if s.get('codec_type') == 'video'), None)
    if v_stream:
        w = int(v_stream.get('width', 0))
        h = int(v_stream.get('height', 0))
        value['width'] = w
        value['height'] = h
        if w > 0 and h > 0:
            if h > w:
                value['orientation'] = 'portrait'
            elif w > h:
                value['orientation'] = 'landscape'
            else:
                value['orientation'] = 'square'
            value['aspect_ratio'] = f"{w}:{h}"
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
    from . import timeline
    return timeline.align_captions(scene, audio_duration, boundaries)


def stamp(seconds, ass=False):
    from . import timeline
    return timeline.stamp(seconds, ass=ass)


def ass_text(text, width=32):
    safe = text.replace('\\', '/').replace('{', '(').replace('}', ')').replace('\r', ' ').replace('\n', ' ')
    return r'\N'.join(textwrap.wrap(safe, width=width))


def get_ass_style(caption_style: str, size: int, bottom: int) -> str:
    """Return ASS V4+ Style line for specified preset."""
    if caption_style == 'box_black':
        return f"Style: Default,Arial,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HB0000000,-1,0,0,0,100,100,0,0,3,6,0,2,90,140,{bottom},1"
    elif caption_style == 'yellow_glow':
        return f"Style: Default,Arial,{size},&H0000E6FF,&H0000E6FF,&H000E0E12,&H88000000,-1,0,0,0,100,100,0,0,1,4,2,2,90,140,{bottom},1"
    elif caption_style == 'pill_translucent':
        return f"Style: Default,Arial,{size},&H00FFFFFF,&H00FFFFFF,&H00202020,&H70101512,-1,0,0,0,100,100,0,0,3,8,0,2,90,140,{bottom},1"
    else:  # 'classic_outline'
        return f"Style: Default,Arial,{size},&H00FFFFFF,&H00FFFFFF,&H000E0E12,&H88000000,-1,0,0,0,100,100,0,0,1,4,2,2,90,140,{bottom},1"


def write_ass(path, cues, project, title=None, total=0):
    # Fixed 1080p design coordinates are scaled by libass for draft resolution.
    style_line = get_ass_style(
        project.get('caption_style', 'classic_outline'),
        project.get('caption_size', 56),
        project.get('caption_bottom', 300)
    )
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_line}
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

    from . import timeline
    tl = timeline.compute_timeline(p, prepared)
    speed = tl.speed
    offset = tl.total_duration

    draft = job['snapshot']['options'].get('quality', 'final') == 'draft'
    width, height = (540, 960) if draft else (1080, 1920)
    temp = DATA / 'tmp' / job['id']
    temp.mkdir(exist_ok=True)
    scene_files, report_scenes = [], []

    for i, (scene, voice, scene_tl) in enumerate(zip(p['scenes'], prepared, tl.scenes)):
        store.progress(job['id'], f"Rendering scene {i + 1}/{len(p['scenes'])} ({'draft' if draft else '1080p'})")
        length = scene_tl.scene_duration
        cues = scene_tl.cues

        cache_key = store.digest({
            'renderer': 3,
            'scene_id': scene['id'],
            'visual': scene['visual'],
            'video_asset': scene['video_asset'],
            'trim_start': scene['trim_start'],
            'fit': scene['fit'],
            'crop_x': scene['crop_x'],
            'graphic_title': scene['graphic_title'],
            'audio_name': Path(voice['audio_path']).name,
            'speed': round(speed, 6),
            'width': width,
            'height': height,
            'caption_size': p['caption_size'],
            'caption_bottom': p['caption_bottom'],
            'caption_style': p.get('caption_style', 'classic_outline'),
            'duration': length,
            'cues': [{'start': round(c['start'], 3), 'end': round(c['end'], 3), 'text': c['text']} for c in cues]
        })
        cached = DATA / 'cache' / (cache_key + '.scene.mp4')
        if not (cached.exists() and cached.stat().st_size > 0):
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
        report_scenes.append({'scene_id': scene['id'], 'duration': length, 'alignment': voice['alignment'],
                              'visual': store.asset(scene['video_asset']) if scene['visual'] != 'graphic' else {'provider': 'explicit graphic'},
                              'source_audio_muted': True})

    # Cache joined video so music-only changes run instantly
    joined_key = store.digest({'joined_v': 2, 'scenes': [s.name for s in scene_files]})
    cached_joined = DATA / 'cache' / (joined_key + '.joined.mp4')
    joined = temp / 'joined.mp4'
    if cached_joined.exists() and cached_joined.stat().st_size > 0:
        shutil.copyfile(cached_joined, joined)
    else:
        listing = temp / 'scenes.txt'
        for i, source in enumerate(scene_files):
            shutil.copyfile(source, temp / f'scene-{i}.mp4')
        listing.write_text('\n'.join(f"file 'scene-{i}.mp4'" for i in range(len(scene_files))), 'utf-8')
        store.progress(job['id'], 'Joining scenes into continuous video')
        run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '1',
             '-i', 'scenes.txt', '-c', 'copy', '-movflags', '+faststart', joined], cwd=temp)
        try:
            shutil.copyfile(joined, cached_joined)
        except OSError:
            pass

    store.progress(job['id'], 'Mixing background audio and voice ducking')
    final = temp / 'reel.mp4'
    if p['music_asset']:
        fade_out_st = max(0.0, offset - 0.8)
        run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-filter_complex_threads', '1',
             '-i', joined, '-stream_loop', '-1', '-i', store.asset_path(p['music_asset'], 'audio'),
             '-filter_complex', f"[0:a]asplit=2[v][side];[1:a]volume={p['music_gain']},afade=t=in:st=0:d=0.4,afade=t=out:st={fade_out_st:.2f}:d=0.8[m];[m][side]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=300[duck];[v][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]",
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

    # Export SRT and timing manifest
    (temp / 'captions.srt').write_text(timeline.to_srt(tl.all_cues), 'utf-8')
    (temp / 'timing-manifest.json').write_text(json.dumps(tl.to_manifest(), indent=2, ensure_ascii=False), 'utf-8')

    report = {'project_id': job['project_id'], 'revision': job['revision'], 'created': store.now(),
              'duration': float(info['format']['duration']), 'dimensions': [width, height], 'scenes': report_scenes,
              'caption_note': 'Automatic Roman Telugu timings need preview review. Manual cues override automatic timings.',
              'caption_style': p.get('caption_style', 'classic_outline'),
              'evidence_notes': p['evidence_notes'], 'research_sources': p['research_sources'],
              'music': store.asset(p['music_asset']) if p['music_asset'] else None,
              'speech_provider': job['snapshot']['options']['tts_provider']}
    (temp / 'source-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), 'utf-8')
    (temp / 'project.json').write_text(json.dumps(job['snapshot'], indent=2, ensure_ascii=False), 'utf-8')

    destination = DATA / 'exports' / job['id']
    destination.mkdir(exist_ok=True)
    exported_files = ['reel.mp4', 'captions.srt', 'timing-manifest.json', 'source-report.json', 'project.json']
    for name in exported_files:
        shutil.copyfile(temp / name, destination / name)
    shutil.rmtree(temp, ignore_errors=True)
    return {'export_id': job['id'], 'duration': report['duration'], 'dimensions': report['dimensions'],
            'files': exported_files}
