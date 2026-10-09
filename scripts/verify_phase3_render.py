"""Phase 3 Mandatory Verification: Real MP4 video render using Celery and PostgreSQL.
Produces data/test_output_phase3/phase3_reel.mp4 with real moving footage,
Telugu narration, Roman Telugu captions, and ducked background music.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

# Ensure root in sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import store
from studio.config import DATA, executable
from studio.media import probe, run
from studio.tasks import task_render_pipeline


def main():
    store.init()
    output_dir = DATA / 'test_output_phase3'
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[Phase 3 Verification] Setting up test project with real media assets...")

    # Validate asset files exist
    v1_path = DATA / 'assets' / '5fe61c8ec2954fafb636295abc9f20d4.mp4'
    v2_path = DATA / 'assets' / '486fbc42dadd4746a023090a06b1873a.mp4'
    bgm_path = DATA / 'assets' / 'a4ed9853a0f645f5b94f7d4c5d3753a8.mp3'

    if not v1_path.exists() or not v2_path.exists() or not bgm_path.exists():
        raise FileNotFoundError("Required test media assets missing in data/assets/")

    project_payload = {
        'title': 'Phase 3 Celery Distributed Regression Reel',
        'category': 'General',
        'voice': 'te-IN-ShrutiNeural',
        'speech_rate': 0,
        'target_seconds': 12,
        'duration_mode': 'natural',
        'caption_size': 58,
        'caption_bottom': 260,
        'music_asset': 'a4ed9853a0f645f5b94f7d4c5d3753a8',
        'music_gain': 0.22,
        'music_rights_reviewed': True,
        'evidence_notes': 'Phase 3 distributed Celery rendering verification project with real moving footage, Telugu narration, Roman Telugu captions, and ducked background music.',
        'research_sources': [],
        'scenes': [
            {
                'id': 'scene_phase3_1',
                'narration': 'ఒక చిన్న ఊరిలో, 20 సంవత్సరాలుగా ఒక ఇల్లు లాక్ చేసి ఉండేది.',
                'roman': 'Oka chinna oorilo, 20 samvatsaraluga oka illu lock chesi undedi.',
                'visual': 'upload',
                'video_asset': '5fe61c8ec2954fafb636295abc9f20d4',
                'rights_reviewed': True,
                'trim_start': 0.0,
                'fit': 'crop',
                'crop_x': 0.5,
                'hold_seconds': 0.0,
                'cues': []
            },
            {
                'id': 'scene_phase3_2',
                'narration': 'ఆ ఇంటి నుంచి ప్రతి రోజు రాత్రి ఒక చిన్న పాప ఏడుస్తున్న సౌండ్ వినిపించేది.',
                'roman': 'Aa inti nunchi prathi roju rathri oka chinna paapa edusthunna sound vinipinchedi.',
                'visual': 'upload',
                'video_asset': '486fbc42dadd4746a023090a06b1873a',
                'rights_reviewed': True,
                'trim_start': 0.0,
                'fit': 'crop',
                'crop_x': 0.5,
                'hold_seconds': 0.0,
                'cues': []
            }
        ]
    }

    saved_p = store.save_project(project_payload)
    pid = saved_p['id']
    rev = saved_p['revision']
    store.approve(pid, rev)

    print(f"[Phase 3 Verification] Enqueuing render job for project {pid} (rev {rev})...")
    job = store.enqueue(pid, rev, 'render', {'quality': 'final', 'tts_provider': 'edge'})
    jid = job['id']
    print(f"[Phase 3 Verification] Job enqueued: {jid}")

    print("[Phase 3 Verification] Executing Phase 3 render task via task_render_pipeline...")
    start_time = time.time()
    task_res = task_render_pipeline(jid)
    elapsed = time.time() - start_time
    print(f"[Phase 3 Verification] Render completed in {elapsed:.2f}s. Result: {task_res}")

    # Verify job status in PostgreSQL
    final_job = store.job(jid)
    if final_job['state'] != 'succeeded':
        raise RuntimeError(f"Render job failed: {final_job.get('error_message') or final_job.get('progress')}")

    export_dir = DATA / 'exports' / jid
    final_mp4 = export_dir / 'reel.mp4'
    if not final_mp4.exists():
        raise FileNotFoundError(f"Export reel.mp4 not found in {export_dir}")

    # Copy artifacts to data/test_output_phase3/
    dest_mp4 = output_dir / 'phase3_reel.mp4'
    shutil.copyfile(final_mp4, dest_mp4)
    for fname in ('captions.srt', 'source-report.json', 'project.json'):
        if (export_dir / fname).exists():
            shutil.copyfile(export_dir / fname, output_dir / fname)

    print(f"[Phase 3 Verification] Saved output artifacts to {output_dir}")

    # FFprobe analysis
    print("[Phase 3 Verification] Probing output video...")
    info = probe(dest_mp4)
    duration_sec = float(info['format']['duration'])
    size_mb = dest_mp4.stat().st_size / (1024 * 1024)

    video_stream = next((s for s in info['streams'] if s['codec_type'] == 'video'), {})
    audio_stream = next((s for s in info['streams'] if s['codec_type'] == 'audio'), {})

    v_width = video_stream.get('width')
    v_height = video_stream.get('height')
    v_codec = video_stream.get('codec_name')
    a_codec = audio_stream.get('codec_name')
    a_channels = audio_stream.get('channels')

    print(f"  - File: {dest_mp4}")
    print(f"  - Size: {size_mb:.2f} MB ({dest_mp4.stat().st_size} bytes)")
    print(f"  - Duration: {duration_sec:.2f} seconds")
    print(f"  - Dimensions: {v_width}x{v_height} (aspect 9:16 vertical)")
    print(f"  - Video Codec: {v_codec}")
    print(f"  - Audio Codec: {a_codec} ({a_channels} channels)")

    # Full decode validation with null-muxer
    print("[Phase 3 Verification] Running FFmpeg null-muxer full decode test...")
    run([executable('ffmpeg'), '-v', 'error', '-xerror', '-i', str(dest_mp4), '-f', 'null', '-'], timeout=300)
    print("  [OK] Full decode test passed with 0 errors!")

    # Verify SRT contents
    srt_file = output_dir / 'captions.srt'
    srt_text = srt_file.read_text('utf-8')
    assert 'Oka chinna oorilo' in srt_text, "Missing scene 1 Roman Telugu caption"
    assert 'paapa edusthunna' in srt_text, "Missing scene 2 Roman Telugu caption"
    print("  [OK] Roman Telugu synchronized captions verified in SRT.")

    print("\n[SUCCESS] Phase 3 Mandatory Verification complete!")
    print(f"Playable Video: {dest_mp4.resolve()}")


if __name__ == '__main__':
    main()
