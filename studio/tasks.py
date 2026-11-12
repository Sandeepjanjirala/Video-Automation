"""Celery tasks for Telugu Reel Studio (Phase 3).
Splits meaningful stages: research, draft, footage acquisition, narration,
alignment, scene rendering, assembly, mixing, and validation.
Enforces execution leases, periodic heartbeats, fencing, and subprocess cancellation.
"""
import copy
import json
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional
from celery import shared_task
from .celery_app import app
from .config import DATA, executable, settings
from . import store
from django.db import connections

# Scenes rendered at the same time; each FFmpeg uses 4 threads, so 3 keep a 12+ core PC busy without
# starving the rest of the system.
RENDER_WORKERS = max(1, min(3, (os.cpu_count() or 4) // 4))


FencingError = store.FencingError


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
        self._context_token = store.execution_token.set(self.lease_token)
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()
        return self

    def _heartbeat_loop(self):
        from django.db import connections
        try:
            while not self._stop_event.wait(self.interval):
                if not store.renew_lease(self.job_id, self.lease_token, self.duration):
                    break
        finally:
            connections.close_all()

    def check_ownership(self):
        if not store.verify_lease_and_not_cancelled(self.job_id, self.lease_token):
            raise FencingError(f"Worker {self.worker_id} lost ownership or job {self.job_id} was cancelled")

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        store.execution_token.reset(self._context_token)


def get_worker_identity(task_name: str) -> str:
    return f"{task_name}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


def fail_execution(job_id, exc, lease, stage):
    if not lease.lease_token:
        return
    from .config import SECRETS
    message = str(exc) if isinstance(exc, ValueError) else f'{type(exc).__name__}: processing failed'
    cfg = settings()
    for key in SECRETS:
        if cfg.get(key):
            message = message.replace(cfg[key], '[redacted]')
    try:
        store.progress(job_id, message[:2000], 'failed', stage_name=stage,
                       lease_token=lease.lease_token)
    except FencingError:
        pass  # Cancellation or a newer attempt owns the final status.


@app.task(name='studio.tasks.task_research', bind=True, acks_late=True, max_retries=3)
def task_research(self, job_id: str):
    """Stage: Research topic using grounded sources."""
    worker_id = get_worker_identity('research')
    lease = ExecutionLeaseContext(job_id, worker_id)
    try:
        with lease:
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
        fail_execution(job_id, exc, lease, 'research')
        raise


@app.task(name='studio.tasks.task_draft', bind=True, acks_late=True, max_retries=3)
def task_draft(self, job_id: str):
    """Stage: Draft storyboard from script or topic."""
    worker_id = get_worker_identity('draft')
    lease = ExecutionLeaseContext(job_id, worker_id)
    try:
        with lease:
            job_data = store.job(job_id)
            project = job_data['snapshot']['project']
            script = job_data['snapshot']['options'].get('script', '')
            store.progress(job_id, 'Drafting storyboard scenes', stage_name='draft')

            from .providers import draft
            result = draft(project, script, fill=bool(job_data['snapshot']['options'].get('fill')))

            lease.check_ownership()
            store.progress(job_id, 'Complete', 'succeeded', result, stage_name='draft')
            return {'job_id': job_id, 'status': 'succeeded'}
    except Exception as exc:
        if isinstance(exc, FencingError):
            return {'job_id': job_id, 'status': 'fenced'}
        fail_execution(job_id, exc, lease, 'draft')
        raise


@app.task(name='studio.tasks.task_stock', bind=True, acks_late=True, max_retries=3)
def task_stock(self, job_id: str):
    """Stage: Footage acquisition for single scene."""
    worker_id = get_worker_identity('stock')
    lease = ExecutionLeaseContext(job_id, worker_id)
    try:
        with lease:
            job_data = store.job(job_id)
            opts = job_data['snapshot']['options']
            store.progress(job_id, 'Downloading footage clip', stage_name='footage')

            from .providers import download_stock
            asset_info = download_stock(opts['video_id'], opts.get('stock_provider', 'pixabay'))
            result = {'asset': asset_info, 'scene_id': opts.get('scene_id')}

            lease.check_ownership()
            store.progress(job_id, 'Complete', 'succeeded', result, stage_name='footage')
            return {'job_id': job_id, 'status': 'succeeded'}
    except Exception as exc:
        if isinstance(exc, FencingError):
            return {'job_id': job_id, 'status': 'fenced'}
        fail_execution(job_id, exc, lease, 'footage')
        raise


@app.task(name='studio.tasks.task_stock_all', bind=True, acks_late=True, max_retries=3)
def task_stock_all(self, job_id: str):
    """Stage: Sourcing footage for unassigned scenes."""
    worker_id = get_worker_identity('stock_all')
    lease = ExecutionLeaseContext(job_id, worker_id)
    try:
        with lease:
            job_data = store.job(job_id)
            p = job_data['snapshot']['project']
            from .footage import find

            def progress(text):
                lease.check_ownership()
                store.progress(job_id, text, stage_name='footage')

            scenes = [s for s in p['scenes'] if s['visual'] != 'graphic' and not s['video_asset']]
            # Footage search waits on the network most of the time, so the voice is recorded meanwhile and the
            # voice step finds it cached: always the free voice, and a paid voice (Sarvam, Azure) only when the
            # one-click draft asks for it, since that run records the voice right after footage anyway.
            voice, options = None, job_data['snapshot'].get('options', {})
            provider = options.get('tts_provider') or 'edge'
            if provider == 'edge' or options.get('prefetch_voice') is True:
                from .media import prefetch_voices
                voice = threading.Thread(target=prefetch_voices, args=(p, provider), daemon=True)
                voice.start()
            selected = find(p, scenes, progress)
            if voice:
                voice.join()
            result = {'selections': selected, 'note': 'Clips chosen from the script by AI where available. Review relevance, framing, and rights before rendering.'}

            lease.check_ownership()
            store.progress(job_id, 'Complete', 'succeeded', result, stage_name='footage')
            return {'job_id': job_id, 'status': 'succeeded'}
    except Exception as exc:
        if isinstance(exc, FencingError):
            return {'job_id': job_id, 'status': 'fenced'}
        fail_execution(job_id, exc, lease, 'footage')
        raise


@app.task(name='studio.tasks.task_voice_pipeline', bind=True, acks_late=True, max_retries=2)
def task_voice_pipeline(self, job_id: str):
    """Stages: Narration synthesis & Caption alignment."""
    worker_id = get_worker_identity('voice')
    lease = ExecutionLeaseContext(job_id, worker_id)
    try:
        with lease:
            job_data = store.job(job_id)
            p = job_data['snapshot']['project']
            if not p['scenes'] or any(not s.get('narration', '').strip() for s in p['scenes']):
                raise ValueError('Cannot prepare voice: every scene needs Telugu narration text')

            provider = job_data['snapshot'].get('options', {}).get('tts_provider') or settings()['tts_provider']
            prepared = []
            from .media import prefetch_voices
            store.progress(job_id, f"Synthesizing narration for {len(p['scenes'])} scenes", stage_name='narration')
            prefetch_voices(p, provider)

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
                    'alignment': alignment,
                    # The caption editor rebuilds automatic cues from these word timings.
                    'boundaries': boundaries
                })

            lease.check_ownership()
            store.progress(job_id, 'Complete', 'succeeded', {'voices': prepared}, stage_name='voice')
            return {'job_id': job_id, 'status': 'succeeded'}
    except Exception as exc:
        if isinstance(exc, FencingError):
            return {'job_id': job_id, 'status': 'fenced'}
        fail_execution(job_id, exc, lease, 'voice')
        raise


@app.task(name='studio.tasks.task_render_pipeline', bind=True, acks_late=True, max_retries=1)
def task_render_pipeline(self, job_id: str):
    """Full render pipeline with fine-grained stages and ownership fencing.
    Stages: narration -> alignment -> scene rendering -> assembly -> mixing -> validation.
    """
    worker_id = get_worker_identity('render')
    lease = ExecutionLeaseContext(job_id, worker_id, interval=15, duration=90)
    temp = None
    try:
        with lease:
            job_data = store.job(job_id)
            p = job_data['snapshot']['project']

            # 1. Preflight validation
            pf = store.preflight(p)
            if not pf['ready']:
                raise ValueError(f"Preflight validation failed: {pf['errors'][0]['message']}")

            # 2. Narration & Alignment stages
            provider = job_data['snapshot'].get('options', {}).get('tts_provider') or settings()['tts_provider']
            prepared = []
            from .media import prefetch_voices
            prefetch_voices(p, provider)  # usually cached already by the voice job
            for i, scene in enumerate(p['scenes']):
                lease.check_ownership()
                store.progress(job_id, f"Preparing narration {i + 1}/{len(p['scenes'])}", stage_name='narration')
                from .media import CAPTION_FILTER, voice_for, duration, captions, write_ass, probe, run
                from .framing import COLOUR_TAGS, scene_shots, source_info, visual_inputs
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

            from .timeline import compute_timeline, to_srt
            timeline = compute_timeline(p, prepared)
            speed = timeline.speed
            offset = timeline.total_duration

            draft = job_data['snapshot']['options'].get('quality', 'final') == 'draft'
            from .formats import of as format_of, size as format_size
            width, height = format_size(p, 'draft' if draft else 'final')

            # Unique temporary working directory per render slot
            temp = DATA / 'tmp' / f"{job_id}_{uuid.uuid4().hex[:8]}"
            temp.mkdir(parents=True, exist_ok=True)

            scene_files, report_scenes = [], []

            # 3. Scene rendering stage: several scenes at once (each FFmpeg uses 4 threads), in scene order.
            rendered, progress_lock = [0], threading.Lock()

            def render_scene(i, scene, voice, timing):
              try:
                lease.check_ownership()
                length = timing.scene_duration
                cues = timing.cues if p.get('captions_enabled', True) else []
                shots = scene_shots(scene, length, format_of(p)['shot_seconds'])
                cache_key = store.digest({
                    'renderer': 6,
                    'scene': scene,
                    'audio': Path(voice['audio_path']).name,
                    'audio_mtime_ns': Path(voice['audio_path']).stat().st_mtime_ns,
                    'audio_size': Path(voice['audio_path']).stat().st_size,
                    'cues': cues,
                    'speed': speed,
                    'width': width,
                    'caption_size': p['caption_size'],
                    'caption_bottom': p['caption_bottom'],
                    'caption_style': p.get('caption_style', 'classic'),
                    # Only projects that highlight key words add to the key, so older renders stay cached.
                    **({'caption_emphasis': p['caption_emphasis']} if p.get('caption_emphasis', 'none') != 'none' else {}),
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
                    from .media import emphasis_words
                    write_ass(work / 'captions.ass', cues, p,
                              scene['graphic_title'] if scene['visual'] == 'graphic' else None, length,
                              emphasis_words(scene, p))
                    args = [executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-filter_threads', '1']
                    if scene['visual'] == 'graphic':
                        args += ['-f', 'lavfi', '-i', f'color=c=0x12251F:s={width}x{height}:r=30:d={length}']
                        vf = f"drawbox=x=0:y={int(height * .24)}:w=iw:h=4:color=0xB9F8CF:t=fill"
                    elif len(shots) == 1:
                        # Same geometry as the editor's framing preview (studio/framing.py).
                        source_path = store.asset_path(scene['video_asset'], 'image' if scene['visual'] == 'image' else 'video')
                        inputs, vf = visual_inputs(scene, source_path, source_info(source_path), length,
                                                   width, height, work, run, job_id)
                        args += inputs
                    else:
                        # Several clips share the scene: each is framed and cut to its part, then joined.
                        part = length / len(shots)
                        for k, shot in enumerate(shots):
                            shot_work = work / f'shot{k}'
                            shot_work.mkdir(exist_ok=True)
                            shot_path = store.asset_path(shot['video_asset'], 'video')
                            seconds = part + (0.1 if k == len(shots) - 1 else 0)
                            shot_inputs, shot_vf = visual_inputs(shot, shot_path, source_info(shot_path), seconds,
                                                                 width, height, shot_work, run, job_id)
                            run([executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-filter_threads', '1',
                                 *shot_inputs, '-map', '0:v:0', '-an', '-vf', shot_vf + ',setsar=1,fps=30',
                                 '-t', f'{seconds:.3f}', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '16',
                                 '-pix_fmt', 'yuv420p', '-threads', '4', shot_work / 'shot.mp4'], cwd=shot_work, job_id=job_id)
                        (work / 'shots.txt').write_text('\n'.join(f"file 'shot{k}/shot.mp4'" for k in range(len(shots))), 'utf-8')
                        args += ['-f', 'concat', '-safe', '0', '-i', 'shots.txt']
                        vf = 'null'
                    output = work / 'scene.mp4'
                    args += ['-i', voice['audio_path'], '-map', '0:v:0', '-map', '1:a:0',
                             '-vf', vf + f",setsar=1,fps=30,{CAPTION_FILTER},{COLOUR_TAGS}",
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

                report = {
                    'scene_id': scene['id'],
                    'duration': length,
                    'alignment': voice['alignment'],
                    'visual': store.asset(scene['video_asset']) if scene['visual'] != 'graphic' else {'provider': 'explicit graphic'},
                    'extra_shots': [store.asset(shot['video_asset']) for shot in shots[1:]],
                    'source_audio_muted': True
                }
                with progress_lock:
                    rendered[0] += 1
                    store.progress(job_id, f"Rendering scenes {rendered[0]}/{len(p['scenes'])} ({'draft' if draft else f'{width}x{height}'})",
                                   stage_name='scene_rendering')
                return cached, report
              finally:
                if threading.current_thread() is not threading.main_thread():
                    connections.close_all()  # each worker thread opened its own database connection

            lease.check_ownership()
            store.progress(job_id, f"Rendering scenes 0/{len(p['scenes'])} ({'draft' if draft else f'{width}x{height}'})", stage_name='scene_rendering')
            jobs = list(enumerate(zip(p['scenes'], prepared, timeline.scenes)))
            with ThreadPoolExecutor(max_workers=min(RENDER_WORKERS, len(jobs))) as pool:
                results = list(pool.map(lambda item: render_scene(item[0], *item[1]), jobs))
            scene_files = [cached for cached, _ in results]
            report_scenes = [report for _, report in results]

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
            # A full decode; long YouTube videos need more than the reel's ten minutes.
            run([executable('ffmpeg'), '-v', 'error', '-xerror', '-i', final, '-f', 'null', '-'],
                timeout=max(600, int(offset * 3)), job_id=job_id)

            # 7. Atomic publication
            (temp / 'captions.srt').write_text(to_srt(timeline.all_cues), 'utf-8')
            manifest = timeline.to_manifest(
                project_id=job_data['project_id'], revision=job_data['revision'], job_id=job_id,
                dimensions=(width, height), measured_duration=float(info['format']['duration']))
            (temp / 'timing-manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), 'utf-8')
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

            result = {
                'export_id': job_id,
                'duration': report['duration'],
                'dimensions': report['dimensions'],
                'files': ['reel.mp4', 'captions.srt', 'source-report.json', 'project.json', 'timing-manifest.json']
            }

            # Fencing check before committing final succeeded state
            lease.check_ownership()
            store.publish_export(job_id, temp, result)
            return {'job_id': job_id, 'status': 'succeeded'}
    except Exception as exc:
        if isinstance(exc, FencingError):
            return {'job_id': job_id, 'status': 'fenced'}
        fail_execution(job_id, exc, lease, 'render')
        raise
    finally:
        if temp is not None:
            shutil.rmtree(temp, ignore_errors=True)
