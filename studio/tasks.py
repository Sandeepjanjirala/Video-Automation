"""Celery tasks for Telugu Reel Studio (Phase 3).
Splits meaningful stages: research, draft, footage acquisition, narration,
alignment, scene rendering, assembly, mixing, and validation.
Enforces execution leases, periodic heartbeats, fencing, and subprocess cancellation.
"""
import copy
import json
import math
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from celery import shared_task
from .celery_app import app
from .config import DATA, executable, settings
from . import store


class FencingError(RuntimeError):
    """Raised when worker loses execution lease or fencing token mismatches."""
    pass


class CancelledError(RuntimeError):
    """Raised when job was cancelled."""
    pass


class ExecutionLeaseContext:
    """Manages execution lease, heartbeats, and fencing checks for a task."""

    def __init__(self, job_id: str, worker_id: str, interval: int = 15, duration: int = 60):
        self.job_id = job_id
        self.worker_id = worker_id
        self.interval = interval
        self.duration = duration
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.lease_token: Optional[str] = None

    def __enter__(self):
        self.lease_token = store.acquire_lease(self.job_id, self.worker_id, self.duration)
        if not self.lease_token:
            raise FencingError(f"Could not acquire execution lease for job {self.job_id}")
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()
        return self

    def _heartbeat_loop(self):
        while not self._stop_event.wait(self.interval):
            success = store.renew_lease(self.job_id, self.lease_token, self.duration)
            if not success:
                break

    def check_ownership(self):
        if not store.verify_lease_and_not_cancelled(self.job_id, self.lease_token):
            raise FencingError(f"Worker {self.worker_id} lost ownership or job {self.job_id} was cancelled")

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)


def get_worker_identity(task_name: str) -> str:
    return f"{task_name}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


@app.task(name='studio.tasks.task_research', bind=True, acks_late=True, max_retries=3)
def task_research(self, job_id: str):
    """Stage: Research topic using grounded sources."""
    worker_id = get_worker_identity('research')
    try:
        with ExecutionLeaseContext(job_id, worker_id) as lease:
            job_data = store.job(job_id)
            project = job_data['snapshot']['project']
            store.progress(job_id, 'Researching claims and facts', stage_name='research')

            from .providers import research
            result = research(project)

            lease.check_ownership()
            store.progress(job_id, 'Complete', 'succeeded', result, stage_name='research')
            return {'job_id': job_id, 'status': 'succeeded'}
    except Exception as exc:
        if isinstance(exc, FencingError):
            return {'job_id': job_id, 'status': 'fenced'}
        store.progress(job_id, f"Research failed: {str(exc)[:500]}", 'failed', stage_name='research')
        raise


@app.task(name='studio.tasks.task_draft', bind=True, acks_late=True, max_retries=3)
def task_draft(self, job_id: str):
    """Stage: Draft storyboard from script or topic."""
    worker_id = get_worker_identity('draft')
    try:
        with ExecutionLeaseContext(job_id, worker_id) as lease:
            job_data = store.job(job_id)
            project = job_data['snapshot']['project']
            script = job_data['snapshot']['options'].get('script', '')
            store.progress(job_id, 'Drafting storyboard scenes', stage_name='draft')

            from .providers import draft
            result = draft(project, script)

            lease.check_ownership()
            store.progress(job_id, 'Complete', 'succeeded', result, stage_name='draft')
            return {'job_id': job_id, 'status': 'succeeded'}
    except Exception as exc:
        if isinstance(exc, FencingError):
            return {'job_id': job_id, 'status': 'fenced'}
        store.progress(job_id, f"Drafting failed: {str(exc)[:500]}", 'failed', stage_name='draft')
        raise


@app.task(name='studio.tasks.task_stock', bind=True, acks_late=True, max_retries=3)
def task_stock(self, job_id: str):
    """Stage: Footage acquisition for single scene."""
    worker_id = get_worker_identity('stock')
    try:
        with ExecutionLeaseContext(job_id, worker_id) as lease:
            job_data = store.job(job_id)
            opts = job_data['snapshot']['options']
            store.progress(job_id, 'Downloading footage clip', stage_name='footage')

            from .providers import download_stock
            asset_info = download_stock(opts['video_id'])
            result = {'asset': asset_info, 'scene_id': opts.get('scene_id')}

            lease.check_ownership()
            store.progress(job_id, 'Complete', 'succeeded', result, stage_name='footage')
            return {'job_id': job_id, 'status': 'succeeded'}
    except Exception as exc:
        if isinstance(exc, FencingError):
            return {'job_id': job_id, 'status': 'fenced'}
        store.progress(job_id, f"Stock acquisition failed: {str(exc)[:500]}", 'failed', stage_name='footage')
        raise


@app.task(name='studio.tasks.task_stock_all', bind=True, acks_late=True, max_retries=3)
def task_stock_all(self, job_id: str):
    """Stage: Sourcing footage for unassigned scenes."""
    worker_id = get_worker_identity('stock_all')
    try:
        with ExecutionLeaseContext(job_id, worker_id) as lease:
            job_data = store.job(job_id)
            p = job_data['snapshot']['project']
            from .providers import download_stock, stock_search

            selected, used = [], set()
            scenes = [s for s in p['scenes'] if s['visual'] != 'graphic' and not s['video_asset']]

            for i, scene in enumerate(scenes):
                lease.check_ownership()
                store.progress(job_id, f"Finding footage {i + 1}/{len(scenes)}", stage_name='footage')
                if not scene['keywords'].strip():
                    selected.append({'scene_id': scene['id'], 'error': 'Add English search keywords'})
                    continue
                try:
                    candidates = stock_search(scene['keywords'])
                    candidates.sort(key=lambda h: (h['id'] in used, h['video']['height'] < h['video']['width']))
                    if not candidates:
                        raise ValueError('No suitable stock result. Upload footage or choose a graphic.')
                    hit = candidates[0]
                    selected.append({'scene_id': scene['id'], 'asset': download_stock(hit['id'])})
                    used.add(hit['id'])
                except ValueError as exc:
                    selected.append({'scene_id': scene['id'], 'error': str(exc)})

            result = {'selections': selected, 'note': 'Keyword-selected candidates. Review relevance, framing, and rights before rendering.'}

            lease.check_ownership()
            store.progress(job_id, 'Complete', 'succeeded', result, stage_name='footage')
            return {'job_id': job_id, 'status': 'succeeded'}
    except Exception as exc:
        if isinstance(exc, FencingError):
            return {'job_id': job_id, 'status': 'fenced'}
        store.progress(job_id, f"Stock sourcing failed: {str(exc)[:500]}", 'failed', stage_name='footage')
        raise


@app.task(name='studio.tasks.task_voice_pipeline', bind=True, acks_late=True, max_retries=2)
def task_voice_pipeline(self, job_id: str):
    """Stages: Narration synthesis & Caption alignment."""
    worker_id = get_worker_identity('voice')
    try:
        with ExecutionLeaseContext(job_id, worker_id) as lease:
            job_data = store.job(job_id)
            p = job_data['snapshot']['project']
            if not p['scenes'] or any(not s.get('narration', '').strip() for s in p['scenes']):
                raise ValueError('Cannot prepare voice: every scene needs Telugu narration text')

            provider = job_data['snapshot'].get('options', {}).get('tts_provider') or settings()['tts_provider']
            prepared = []

            for i, scene in enumerate(p['scenes']):
                lease.check_ownership()
                # Narration stage
                store.progress(job_id, f"Synthesizing narration {i + 1}/{len(p['scenes'])}", stage_name='narration')
                from .media import voice_for, duration, captions
                path, boundaries = voice_for(scene, p, provider)
                seconds = duration(path)

                # Alignment stage
                store.progress(job_id, f"Aligning captions {i + 1}/{len(p['scenes'])}", stage_name='alignment')
                cues, alignment = captions(scene, seconds, boundaries)
                prepared.append({
                    'scene_id': scene['id'],
                    'audio_path': str(path),
                    'duration': seconds,
                    'cues': cues,
                    'alignment': alignment
                })

            lease.check_ownership()
            store.progress(job_id, 'Complete', 'succeeded', {'voices': prepared}, stage_name='voice')
            return {'job_id': job_id, 'status': 'succeeded'}
    except Exception as exc:
        if isinstance(exc, FencingError):
            return {'job_id': job_id, 'status': 'fenced'}
        store.progress(job_id, f"Voice preparation failed: {str(exc)[:500]}", 'failed', stage_name='voice')
        raise


@app.task(name='studio.tasks.task_render_pipeline', bind=True, acks_late=True, max_retries=1)
def task_render_pipeline(self, job_id: str):
    """Full render pipeline with fine-grained stages and ownership fencing.
    Stages: narration -> alignment -> scene rendering -> assembly -> mixing -> validation.
    """
    worker_id = get_worker_identity('render')
    try:
        with ExecutionLeaseContext(job_id, worker_id, interval=15, duration=90) as lease:
            job_data = store.job(job_id)
            p = job_data['snapshot']['project']

            # 1. Preflight validation
            pf = store.preflight(p)
            if not pf['ready']:
                raise ValueError(f"Preflight validation failed: {pf['errors'][0]['message']}")

            # 2. Narration & Alignment stages
            provider = job_data['snapshot'].get('options', {}).get('tts_provider') or settings()['tts_provider']
            prepared = []
            for i, scene in enumerate(p['scenes']):
                lease.check_ownership()
                store.progress(job_id, f"Preparing narration {i + 1}/{len(p['scenes'])}", stage_name='narration')
                from .media import voice_for, duration, captions, write_ass, probe, run, stamp
                vpath, boundaries = voice_for(scene, p, provider)
                sec = duration(vpath)

                store.progress(job_id, f"Aligning captions {i + 1}/{len(p['scenes'])}", stage_name='alignment')
                cues, alignment = captions(scene, sec, boundaries)
                prepared.append({
                    'scene_id': scene['id'],
                    'audio_path': str(vpath),
                    'duration': sec,
                    'cues': cues,
                    'alignment': alignment
                })

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

            draft = job_data['snapshot']['options'].get('quality', 'final') == 'draft'
            width, height = (540, 960) if draft else (1080, 1920)

            # Unique temporary working directory per render slot
            temp = DATA / 'tmp' / f"{job_id}_{uuid.uuid4().hex[:8]}"
            temp.mkdir(parents=True, exist_ok=True)

            scene_files, all_cues, report_scenes = [], [], []
            offset = 0.0

            # 3. Scene rendering stage
            for i, (scene, voice) in enumerate(zip(p['scenes'], prepared)):
                lease.check_ownership()
                store.progress(job_id, f"Rendering scene {i + 1}/{len(p['scenes'])} ({'draft' if draft else '1080p'})", stage_name='scene_rendering')
                length = math.ceil((voice['duration'] / speed + scene['hold_seconds']) * 30) / 30
                cues = [{**c, 'start': c['start'] / speed, 'end': c['end'] / speed} for c in voice['cues']]
                cache_key = store.digest({
                    'renderer': 2,
                    'scene': scene,
                    'audio': Path(voice['audio_path']).name,
                    'speed': speed,
                    'width': width,
                    'caption_size': p['caption_size'],
                    'caption_bottom': p['caption_bottom']
                })
                cached = DATA / 'cache' / (cache_key + '.scene.mp4')

                # Check if cached scene is present and valid
                is_cached_valid = cached.exists() and cached.stat().st_size > 0
                if is_cached_valid:
                    try:
                        probe(cached)
                    except Exception:
                        is_cached_valid = False

                if not is_cached_valid:
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
                    run(args, cwd=work, job_id=job_id)
                    probe(output)

                    # Atomic publication to cache
                    temp_cache = cached.with_suffix(f'.{uuid.uuid4().hex[:8]}.tmp')
                    shutil.copyfile(output, temp_cache)
                    try:
                        temp_cache.replace(cached)
                    except OSError:
                        if not cached.exists():
                            shutil.copyfile(temp_cache, cached)
                        temp_cache.unlink(missing_ok=True)

                scene_files.append(cached)
                all_cues.extend([{**c, 'start': c['start'] + offset, 'end': c['end'] + offset} for c in cues])
                report_scenes.append({
                    'scene_id': scene['id'],
                    'duration': length,
                    'alignment': voice['alignment'],
                    'visual': store.asset(scene['video_asset']) if scene['visual'] != 'graphic' else {'provider': 'explicit graphic'},
                    'source_audio_muted': True
                })
                offset += length

            # 4. Assembly stage
            lease.check_ownership()
            store.progress(job_id, 'Joining scenes into continuous video', stage_name='assembly')
            listing = temp / 'scenes.txt'
            for i, source in enumerate(scene_files):
                shutil.copyfile(source, temp / f'scene-{i}.mp4')
            listing.write_text('\n'.join(f"file 'scene-{i}.mp4'" for i in range(len(scene_files))), 'utf-8')
            joined = temp / 'joined.mp4'
            run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '1',
                 '-i', 'scenes.txt', '-c', 'copy', '-movflags', '+faststart', joined], cwd=temp, job_id=job_id)

            # 5. Mixing stage
            lease.check_ownership()
            store.progress(job_id, 'Mixing background audio and voice ducking', stage_name='mixing')
            final = temp / 'reel.mp4'
            if p['music_asset']:
                run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-filter_complex_threads', '1',
                     '-i', joined, '-stream_loop', '-1', '-i', store.asset_path(p['music_asset'], 'audio'),
                     '-filter_complex', f"[0:a]asplit=2[v][side];[1:a]volume={p['music_gain']}[m];[m][side]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[duck];[v][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]",
                     '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', str(offset), '-movflags', '+faststart', final],
                    job_id=job_id)
            else:
                shutil.copyfile(joined, final)

            # 6. Validation stage
            lease.check_ownership()
            store.progress(job_id, 'Validating output video streams and duration', stage_name='validation')
            info = probe(final)
            video = next((s for s in info['streams'] if s['codec_type'] == 'video'), {})
            audio = next((s for s in info['streams'] if s['codec_type'] == 'audio'), {})
            if (video.get('width'), video.get('height')) != (width, height) or not audio or abs(float(info['format']['duration']) - offset) > 0.5:
                raise ValueError('Export validation failed: dimensions, audio, or duration did not match')
            run([executable('ffmpeg'), '-v', 'error', '-xerror', '-i', final, '-f', 'null', '-'], timeout=600, job_id=job_id)

            # 7. Atomic publication
            (temp / 'captions.srt').write_text('\n\n'.join(f"{i+1}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i, c in enumerate(all_cues)) + '\n', 'utf-8')
            report = {
                'project_id': job_data['project_id'],
                'revision': job_data['revision'],
                'created': store.now(),
                'duration': float(info['format']['duration']),
                'dimensions': [width, height],
                'scenes': report_scenes,
                'caption_note': 'Automatic Roman Telugu timings need preview review. Manual cues override automatic timings.',
                'evidence_notes': p['evidence_notes'],
                'research_sources': p['research_sources'],
                'music': store.asset(p['music_asset']) if p['music_asset'] else None,
                'speech_provider': job_data['snapshot']['options']['tts_provider']
            }
            (temp / 'source-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), 'utf-8')
            (temp / 'project.json').write_text(json.dumps(job_data['snapshot'], indent=2, ensure_ascii=False), 'utf-8')

            destination = DATA / 'exports' / job_id
            destination.mkdir(parents=True, exist_ok=True)
            for name in ('reel.mp4', 'captions.srt', 'source-report.json', 'project.json'):
                shutil.copyfile(temp / name, destination / name)
            shutil.rmtree(temp, ignore_errors=True)

            result = {
                'export_id': job_id,
                'duration': report['duration'],
                'dimensions': report['dimensions'],
                'files': ['reel.mp4', 'captions.srt', 'source-report.json', 'project.json']
            }

            # Fencing check before committing final succeeded state
            lease.check_ownership()
            store.progress(job_id, 'Complete', 'succeeded', result, stage_name='validation')
            return {'job_id': job_id, 'status': 'succeeded'}
    except Exception as exc:
        if isinstance(exc, FencingError):
            return {'job_id': job_id, 'status': 'fenced'}
        store.progress(job_id, f"Render failed: {str(exc)[:500]}", 'failed', stage_name='render')
        raise
