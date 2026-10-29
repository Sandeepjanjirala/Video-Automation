import os
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from timeline_renderer.quality_gates import probe_media_file, run_full_decode_validation

video_path = Path("test-outputs/e2e-jwst/20261017T072010Z/final_reel.mp4")
info = probe_media_file(video_path)
streams = info.get("streams", [])
v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

print("=== FFPROBE STREAMS ===")
if v_stream:
    print(f"Video: {v_stream.get('codec_name')} {v_stream.get('width')}x{v_stream.get('height')} @ {v_stream.get('r_frame_rate')} fps, duration: {v_stream.get('duration')}")
if a_stream:
    print(f"Audio: {a_stream.get('codec_name')} {a_stream.get('channels')} ch @ {a_stream.get('sample_rate')} Hz, duration: {a_stream.get('duration')}")

no_corrupt, decode_err = run_full_decode_validation(video_path)
print(f"No corrupt frames: {no_corrupt}, Decode err: {decode_err}")

report_data = {
    "file": str(video_path),
    "size_bytes": video_path.stat().st_size,
    "width": v_stream.get("width") if v_stream else None,
    "height": v_stream.get("height") if v_stream else None,
    "fps": v_stream.get("r_frame_rate") if v_stream else None,
    "video_duration": v_stream.get("duration") if v_stream else None,
    "audio_duration": a_stream.get("duration") if a_stream else None,
    "no_corrupt_frames": no_corrupt,
    "passed_quality_gates": bool(v_stream and v_stream.get("width") == 1080 and v_stream.get("height") == 1920 and no_corrupt),
}

with open("test-outputs/e2e-jwst/20261017T072010Z/quality-report.json", "w", encoding="utf-8") as f:
    json.dump(report_data, f, indent=2)

print("Quality gate report saved successfully.")
