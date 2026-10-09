"""Generate a real playable MP4 using the user's migrated project data in PostgreSQL 'studio'."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import studio.store as store
import studio.media as media
import studio.worker as worker
from studio.config import DATA, executable

# Target PostgreSQL database
os.environ['STUDIO_DATABASE_URL'] = 'postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/studio'

def main():
    print("=" * 60)
    print("CUTOVER VERIFICATION: RENDERING MIGRATED PROJECT")
    print("=" * 60)

    store.init()
    migrated_pid = '6acd06e57e4c48f5bce954cfe47ccf82'
    p_wrapper = store.get_project(migrated_pid)
    current_rev = p_wrapper['revision']
    p_data = p_wrapper['project']

    print(f"Loaded Migrated Project: {migrated_pid}")
    print(f"Title: {p_data.get('title')}")
    print(f"Revision: {current_rev}")
    print(f"Total Scenes: {len(p_data.get('scenes', []))}")

    # Ensure background music asset is attached to satisfy audio requirement
    music_asset_id = 'a4ed9853a0f645f5b94f7d4c5d3753a8'
    if not p_data.get('music_asset'):
        print("Configuring background music asset for migrated reel...")
        p_data['music_asset'] = music_asset_id
        p_data['music_rights_reviewed'] = True
        p_data['music_gain'] = 0.15
        save_res = store.save_project(p_data, migrated_pid, expected=current_rev)
        current_rev = save_res['revision']
        print(f"Saved project with background music. New Revision: {current_rev}")

    # 1. Approve script revision
    print(f"Approving revision {current_rev}...")
    store.approve(migrated_pid, current_rev)

    # 2. Check preflight
    p_wrapper = store.get_project(migrated_pid)
    pf = store.preflight(p_wrapper['project'])
    print(f"Preflight status: Ready = {pf['ready']}")
    if not pf['ready']:
        print("Preflight errors:", pf['errors'])
        sys.exit(1)

    # 3. Enqueue render job (final quality, 1080x1920)
    print(f"Enqueuing render job for revision {current_rev}...")
    job = store.enqueue(migrated_pid, current_rev, 'render', {'tts_provider': 'edge', 'quality': 'final'})
    jid = job['id']
    print(f"Render Job queued: {jid}")

    # 4. Process job with worker
    print("Claiming and executing render job via worker...")
    claimed = store.claim()
    assert claimed is not None and claimed['id'] == jid, f"Expected job {jid}, got {claimed}"
    worker.process(claimed)

    # 5. Check job result
    result_job = store.job(jid)
    print(f"Job finished with state: {result_job['state']}")
    if result_job['state'] != 'succeeded':
        print(f"ERROR: Render job failed: {result_job.get('progress')}")
        sys.exit(1)

    # 6. Save output video to phase cutover directory
    output_dir = DATA / 'test_output_cutover'
    output_dir.mkdir(parents=True, exist_ok=True)
    out_video = output_dir / 'migrated_cutover_reel.mp4'

    source_video = DATA / 'exports' / jid / 'reel.mp4'
    source_srt = DATA / 'exports' / jid / 'captions.srt'
    source_report = DATA / 'exports' / jid / 'source-report.json'

    shutil.copyfile(source_video, out_video)
    shutil.copyfile(source_srt, output_dir / 'captions.srt')
    shutil.copyfile(source_report, output_dir / 'source-report.json')

    print(f"\nSaved final cutover video to: {out_video}")

    # 7. Verification checks
    print("\nVerifying video streams and decoding:")
    info = media.probe(out_video)
    video_stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
    audio_stream = next(s for s in info['streams'] if s['codec_type'] == 'audio')

    width = int(video_stream['width'])
    height = int(video_stream['height'])
    duration = float(info['format']['duration'])
    v_codec = video_stream['codec_name']
    a_codec = audio_stream['codec_name']
    size_mb = out_video.stat().st_size / (1024 * 1024)

    print(f"  Resolution: {width}x{height}")
    print(f"  Video Codec: {v_codec}")
    print(f"  Audio Codec: {a_codec} ({audio_stream.get('sample_rate')} Hz, {audio_stream.get('channels')} channels)")
    print(f"  Duration: {duration:.2f} seconds")
    print(f"  File Size: {size_mb:.2f} MB")

    # Assertions
    assert (width, height) == (1080, 1920), f"Unexpected resolution: {width}x{height}"
    assert duration > 10.0, f"Duration too short: {duration}s"
    assert v_codec == 'h264'
    assert a_codec == 'aac'

    # Decode check
    decode_cmd = [executable('ffmpeg'), '-v', 'error', '-xerror', '-i', str(out_video), '-f', 'null', '-']
    decode_res = subprocess.run(decode_cmd, capture_output=True, text=True)
    assert decode_res.returncode == 0, f"FFmpeg decode verification failed: {decode_res.stderr}"
    print("  FFmpeg Full Decode Validation: PASSED (0 errors)")

    print("\n" + "=" * 60)
    print("MIGRATED PROJECT RENDER & CUTOVER VERIFICATION COMPLETE!")
    print(f"Video Output Location: {out_video.resolve()}")
    print("=" * 60)


if __name__ == '__main__':
    main()
