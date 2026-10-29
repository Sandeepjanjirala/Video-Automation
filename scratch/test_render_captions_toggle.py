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

from urllib.parse import urlsplit
from django.conf import settings
from django.db import connections

def _configure_explicit_test_databases():
    test_url = os.environ.get("STUDIO_TEST_DATABASE_URL")
    if not test_url:
        return
    parsed = urlsplit(test_url)
    target = parsed.path.lstrip("/")
    for alias in list(settings.DATABASES.keys()):
        settings.DATABASES[alias]["NAME"] = target
        if parsed.hostname:
            settings.DATABASES[alias]["HOST"] = parsed.hostname
        if parsed.port:
            settings.DATABASES[alias]["PORT"] = str(parsed.port)
        if parsed.username:
            settings.DATABASES[alias]["USER"] = parsed.username
        if parsed.password:
            settings.DATABASES[alias]["PASSWORD"] = parsed.password
    connections._settings = connections.configure_settings(settings.DATABASES)

_configure_explicit_test_databases()

from core.models import TimelinePlan
from studio.c5 import create_and_save_render_manifest, execute_c5_render
from timeline_renderer.quality_gates import probe_media_file, run_full_decode_validation
import subprocess
from video_understanding.sampling import get_ffmpeg_executable

plan = TimelinePlan.objects.filter(status__in=["approved", "reviewed"]).order_by("-version").first() or TimelinePlan.objects.first()
if not plan:
    print("No TimelinePlan found in DB")
    sys.exit(1)

project_id = plan.project_id
print(f"Testing with plan {plan.id} for project {project_id} (version {plan.version})")

# 1. TEST CAPTIONS ON (pro_viral active-word karaoke)
print("\n--- TEST 1: CAPTIONS ON (pro_viral) ---")
manifest_on, record_on = create_and_save_render_manifest(
    project_id=project_id,
    plan_version=plan.version,
    style_profile="pro_viral",
    target_resolution="1080x1920",
    include_captions=True,
    caption_style="pro_viral",
)
print("Manifest ON cues count:", len(manifest_on.caption_track.cues))
assert len(manifest_on.caption_track.cues) > 0, "Cues should not be empty when captions are ON"

file_on, qg_on = execute_c5_render(manifest_id=record_on.id, quality_profile="draft")
print(f"Rendered CAPTIONS ON video: {file_on} ({file_on.stat().st_size} bytes)")
print("Quality gates passed:", qg_on.passed)

# Extract a frame from the video with captions
frame_on = Path("scratch/frame_captions_ON.png")
ffmpeg = get_ffmpeg_executable()
cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.8", "-i", str(file_on), "-vframes", "1", str(frame_on)]
subprocess.run(cmd, check=True)
print("Saved frame_captions_ON.png")

# 2. TEST CAPTIONS OFF
print("\n--- TEST 2: CAPTIONS OFF ---")
manifest_off, record_off = create_and_save_render_manifest(
    project_id=project_id,
    plan_version=plan.version,
    style_profile="pro_viral",
    target_resolution="1080x1920",
    include_captions=False,
)
print("Manifest OFF cues count:", len(manifest_off.caption_track.cues))
assert len(manifest_off.caption_track.cues) == 0, "Cues must be empty when captions are OFF"

file_off, qg_off = execute_c5_render(manifest_id=record_off.id, quality_profile="draft")
print(f"Rendered CAPTIONS OFF video: {file_off} ({file_off.stat().st_size} bytes)")
print("Quality gates passed:", qg_off.passed)

# Extract a frame from the video without captions
frame_off = Path("scratch/frame_captions_OFF.png")
cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.8", "-i", str(file_off), "-vframes", "1", str(frame_off)]
subprocess.run(cmd, check=True)
print("Saved frame_captions_OFF.png")

print("\n=== ALL TOGGLE TESTS PASSED SUCCESSFULLY! ===")
