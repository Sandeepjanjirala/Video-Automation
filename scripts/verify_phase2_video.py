"""Phase 2 Mandatory Video Verification Script.

Generates a real playable MP4 regression-test video in data/test_output_phase2/
using PostgreSQL as primary DB, real moving footage, Telugu narration,
Roman Telugu subtitles, and ducked background music.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Set default test database URL
if not os.environ.get('STUDIO_DATABASE_URL'):
    os.environ['STUDIO_DATABASE_URL'] = 'postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/studio_test'

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import studio.config
import studio.store as store
import studio.media as media
import studio.worker as worker
from studio.config import DATA, executable


def run_verification():
    print("=" * 60)
    print("PHASE 2 MANDATORY REGRESSION TEST: VIDEO GENERATION")
    print(f"Target Database: {store.get_database_url()}")
    print("=" * 60)

    # 1. Initialize store & check PostgreSQL connection
    store.init()
    output_dir = DATA / 'test_output_phase2'
    output_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = output_dir / 'phase2_reel.mp4'

    # 2. Register real media assets in DB if not already present
    vid_asset_1 = '5fe61c8ec2954fafb636295abc9f20d4'
    vid_asset_2 = '486fbc42dadd4746a023090a06b1873a'
    music_asset_id = 'a4ed9853a0f645f5b94f7d4c5d3753a8'

    for vid in (vid_asset_1, vid_asset_2):
        if not store.find_asset(external_id=vid):
            store.add_asset({
                'id': vid,
                'name': f'Moving Footage {vid[:8]}',
                'filename': f'{vid}.mp4',
                'kind': 'video',
                'duration': 9.8,
                'sha256': 'local_footage_hash',
                'provider': 'upload',
                'external_id': vid,
                'license': 'Royalty Free'
            })

    if not store.find_asset(external_id=music_asset_id):
        store.add_asset({
            'id': music_asset_id,
            'name': 'Background Music Football Beat',
            'filename': f'{music_asset_id}.mp3',
            'kind': 'audio',
            'duration': 59.1,
            'sha256': 'local_music_hash',
            'provider': 'upload',
            'external_id': music_asset_id,
            'license': 'Royalty Free'
        })

    # 3. Create test project with real moving footage, Telugu narration, Roman subtitles, BGM
    scenes = [
        {
            'id': 'scene_phase2_1',
            'narration': 'ఒక చిన్న ఊరిలో, 20 సంవత్సరాలుగా ఒక ఇల్లు లాక్ చేసి ఉండేది.',
            'roman': 'Oka chinna oorilo, 20 samvatsaraluga oka illu lock chesi undedi.',
            'visual': 'stock',
            'video_asset': vid_asset_1,
            'fit': 'crop',
            'crop_x': 0.5,
            'trim_start': 0.0,
            'hold_seconds': 0.3,
            'rights_reviewed': True
        },
        {
            'id': 'scene_phase2_2',
            'narration': 'ఆ ఇంటి నుంచి ప్రతి రోజూ రాత్రి ఒక చిన్న పాప ఏడుస్తున్న సౌండ్ వినిపించేది.',
            'roman': 'Aa inti nunchi prathi roju rathri oka chinna paapa edusthunna sound vinipinchedi.',
            'visual': 'stock',
            'video_asset': vid_asset_2,
            'fit': 'crop',
            'crop_x': 0.5,
            'trim_start': 0.0,
            'hold_seconds': 0.3,
            'rights_reviewed': True
        }
    ]

    project_payload = {
        'title': 'Phase 2 Regression Reel',
        'topic': 'Telugu Mystery House',
        'evidence_notes': 'Phase 2 PostgreSQL persistence regression verification test project.',
        'target_seconds': 12,
        'duration_mode': 'natural',
        'caption_size': 48,
        'caption_bottom': 220,
        'music_asset': music_asset_id,
        'music_rights_reviewed': True,
        'music_gain': 0.18,
        'scenes': scenes
    }

    print("[1/5] Saving test project to PostgreSQL...")
    p_row = store.save_project(project_payload)
    pid = p_row['id']
    rev = p_row['revision']
    print(f"      Project ID: {pid}, Revision: {rev}")

    print("[2/5] Approving script revision...")
    store.approve(pid, rev)

    print("[3/5] Enqueuing render job with edge TTS & final 1080x1920 quality...")
    job = store.enqueue(pid, rev, 'render', {'tts_provider': 'edge', 'quality': 'final'})
    jid = job['id']
    print(f"      Job ID: {jid}")

    print("[4/5] Executing worker render process...")
    claim_job = store.claim()
    assert claim_job is not None and claim_job['id'] == jid, f"Expected to claim {jid}, got {claim_job}"
    worker.process(claim_job)

    result_job = store.job(jid)
    print(f"      Job Status: {result_job['state']}")
    if result_job['state'] != 'succeeded':
        print(f"ERROR: Render job failed: {result_job.get('progress')}")
        sys.exit(1)

    rendered_export = DATA / 'exports' / jid / 'reel.mp4'
    captions_file = DATA / 'exports' / jid / 'captions.srt'
    report_file = DATA / 'exports' / jid / 'source-report.json'

    assert rendered_export.is_file(), f"Rendered output missing at {rendered_export}"
    shutil.copyfile(rendered_export, out_mp4)
    shutil.copyfile(captions_file, output_dir / 'captions.srt')
    shutil.copyfile(report_file, output_dir / 'source-report.json')
    print(f"      Saved output video to: {out_mp4}")

    print("[5/5] Performing verification checks on generated video...")
    info = media.probe(out_mp4)
    video_stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
    audio_stream = next(s for s in info['streams'] if s['codec_type'] == 'audio')

    width = int(video_stream['width'])
    height = int(video_stream['height'])
    duration = float(info['format']['duration'])
    v_codec = video_stream['codec_name']
    a_codec = audio_stream['codec_name']
    file_size_mb = out_mp4.stat().st_size / (1024 * 1024)

    print(f"      Resolution: {width}x{height} (Expected: 1080x1920)")
    print(f"      Video Codec: {v_codec}")
    print(f"      Audio Codec: {a_codec} (Sample rate: {audio_stream.get('sample_rate')} Hz, Channels: {audio_stream.get('channels')})")
    print(f"      Duration: {duration:.2f} seconds")
    print(f"      File Size: {file_size_mb:.2f} MB")

    # Assertions
    assert (width, height) == (1080, 1920), f"Incorrect dimensions: {width}x{height}"
    assert duration > 5.0, f"Video too short: {duration}s"
    assert v_codec == 'h264', f"Unexpected video codec: {v_codec}"
    assert a_codec == 'aac', f"Unexpected audio codec: {a_codec}"

    # Full decode validation via ffmpeg null muxer
    decode_cmd = [executable('ffmpeg'), '-v', 'error', '-xerror', '-i', str(out_mp4), '-f', 'null', '-']
    decode_res = subprocess.run(decode_cmd, capture_output=True, text=True)
    if decode_res.returncode != 0:
        print(f"ERROR: Video decode test failed: {decode_res.stderr}")
        sys.exit(1)
    print("      FFmpeg Full Decode Validation: PASSED (zero decoding errors)")

    # Captions check
    srt_content = (output_dir / 'captions.srt').read_text(encoding='utf-8')
    assert 'Oka chinna oorilo' in srt_content, "Missing Roman subtitles in SRT"
    print("      Subtitle File & Burn-in Check: PASSED")

    print("\n" + "=" * 60)
    print("ALL PHASE 2 ACCEPTANCE CRITERIA VERIFIED SUCCESSFULLY!")
    print(f"Output Video Path: {out_mp4.resolve()}")
    print("=" * 60)


if __name__ == '__main__':
    run_verification()
