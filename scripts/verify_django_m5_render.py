"""M5 Mandatory Integration Test Script.

Traces and proves the complete real production pipeline triggered by Django:
HTTP POST to Django :8788
       ↓
PostgreSQL job (created by django_job_writer)
       ↓
PostgreSQL outbox (created in same transaction)
       ↓
existing dispatcher (studio-dispatcher)
       ↓
Redis (broker DB 0)
       ↓
existing Celery worker (studio-worker-render, queue 'render')
       ↓
existing rendering pipeline (task_render_pipeline)
       ↓
FFmpeg (1080x1920 H.264 + AAC)
       ↓
REAL playable 1080x1920 MP4
"""
import os
import sys
import json
import time
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import psycopg2
import httpx

DJANGO_BASE = "http://127.0.0.1:8788"
PROJECT_ID = "93dba246d73140e396a369239b04615b"  # Home Laptop Phase 3 Reel Verification
REVISION = 2
DB_URL = "postgresql://postgres:postgres@127.0.0.1:5433/studio"

print("=" * 60)
print("STAGE M5 REAL DJANGO-TRIGGERED PIPELINE VERIFICATION")
print("=" * 60)

# 1. Fetch bootstrap token from Django
print("\n[Step 1] Fetching bootstrap session token from Django :8788...")
boot_resp = httpx.get(f"{DJANGO_BASE}/api/bootstrap", timeout=10.0)
assert boot_resp.status_code == 200, f"Django bootstrap failed: {boot_resp.status_code}"
token = boot_resp.json()['token']
print(f"Token obtained: {token[:12]}...")

# 2. Enqueue real render job through Django :8788
print(f"\n[Step 2] Sending POST /api/projects/{PROJECT_ID}/jobs to Django :8788...")
headers = {
    'x-studio-token': token,
    'origin': DJANGO_BASE,
    'Content-Type': 'application/json'
}
payload = {
    'revision': REVISION,
    'kind': 'render',
    'quality': 'final'
}

enqueue_start = time.time()
enqueue_resp = httpx.post(
    f"{DJANGO_BASE}/api/projects/{PROJECT_ID}/jobs",
    json=payload,
    headers=headers,
    timeout=15.0
)
enqueue_duration = time.time() - enqueue_start
print(f"Django HTTP Response Status: {enqueue_resp.status_code} in {enqueue_duration*1000:.1f}ms")
print(f"Django HTTP Response Body:\n{json.dumps(enqueue_resp.json(), indent=2)}")

assert enqueue_resp.status_code == 200, f"Enqueue failed: {enqueue_resp.text}"
job_data = enqueue_resp.json()
jid = job_data['id']
print(f"\nCreated Job ID: {jid}")

# 3. Verify Job and Outbox records in PostgreSQL
print("\n[Step 3] Verifying PostgreSQL records (jobs and outbox)...")
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

cur.execute("SELECT id, state, stage, progress, created_at FROM jobs WHERE id = %s;", [jid])
job_row = cur.fetchone()
print(f"PostgreSQL jobs row: id={job_row[0]}, state={job_row[1]}, stage={job_row[2]}, progress='{job_row[3]}'")

cur.execute("SELECT id, event_type, processed, payload, attempts, created_at FROM outbox WHERE payload->>'job_id' = %s;", [jid])
outbox_row = cur.fetchone()
assert outbox_row is not None, "Outbox event not found in PostgreSQL!"
outbox_id = outbox_row[0]
outbox_event_type = outbox_row[1]
outbox_processed = outbox_row[2]
outbox_payload = outbox_row[3]
print(f"PostgreSQL outbox row: id={outbox_id}, event_type={outbox_event_type}, processed={outbox_processed}")
print(f"Outbox payload keys: {list(outbox_payload.keys())}")

# 4. Monitor execution through existing Dispatcher and Celery Render Worker
print("\n[Step 4] Monitoring pipeline execution via PostgreSQL & Django polling...")
start_time = time.time()
last_progress = ""
final_state = None

while time.time() - start_time < 180:
    # Check outbox status
    cur.execute("SELECT processed, attempts, last_attempt_at, error FROM outbox WHERE id = %s;", [outbox_id])
    ob = cur.fetchone()
    
    # Check job status via Django read API
    job_poll_resp = httpx.get(f"{DJANGO_BASE}/api/projects/{PROJECT_ID}/jobs", timeout=10.0)
    if job_poll_resp.status_code == 200:
        matching_jobs = [j for j in job_poll_resp.json() if j['id'] == jid]
        if matching_jobs:
            cur_job = matching_jobs[0]
            st = cur_job.get('state')
            pr = cur_job.get('progress') or ''
            if pr != last_progress:
                elapsed = time.time() - start_time
                print(f"[{elapsed:5.1f}s] State: {st:<10} | Outbox processed: {ob[0]} | Progress: {pr}")
                last_progress = pr
            if st in ('succeeded', 'failed'):
                final_state = st
                break
    time.sleep(1)

elapsed_total = time.time() - start_time
print(f"\nFinal Job State: {final_state} in {elapsed_total:.2f}s")
assert final_state == 'succeeded', f"Render did not succeed! Final state: {final_state}"

# 5. Fetch job stages from PostgreSQL
cur.execute("SELECT attempt, stage, state, created_at FROM job_stages WHERE job_id = %s ORDER BY id ASC;", [jid])
stages = cur.fetchall()
print("\nJob Stages recorded in PostgreSQL:")
for s in stages:
    print(f"  - Attempt {s[0]}: stage={s[1]}, state={s[2]}, at={s[3]}")

cur.close()
conn.close()

# 6. Verify export artifact on disk
export_dir = Path("data/exports") / jid
export_mp4 = export_dir / "reel.mp4"
print(f"\n[Step 5] Checking export artifact on disk: {export_mp4}")
assert export_mp4.exists(), f"Export file {export_mp4} not found!"
file_size = export_mp4.stat().st_size
print(f"Export file size: {file_size} bytes ({file_size / (1024*1024):.2f} MB)")

# 7. Save to test-outputs/django-m5/<timestamp>/
timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
output_dir = Path("test-outputs") / "django-m5" / timestamp_str
output_dir.mkdir(parents=True, exist_ok=True)
dest_mp4 = output_dir / "django_m5_reel.mp4"
shutil.copy2(export_mp4, dest_mp4)

# Copy other artifacts if present
for fname in ("captions.srt", "source-report.json", "project.json"):
    src_f = export_dir / fname
    if src_f.exists():
        shutil.copy2(src_f, output_dir / fname)

print(f"Copied artifacts to: {dest_mp4}")

# 8. Probe with FFprobe
print("\n[Step 6] Running FFprobe validation...")
ffprobe_cmd = [
    "ffprobe", "-v", "error",
    "-show_entries", "format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,r_frame_rate",
    "-of", "json",
    str(dest_mp4)
]
probe_run = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
probe_data = json.loads(probe_run.stdout)
print("FFprobe summary:")
duration = float(probe_data['format']['duration'])
print(f"  - Duration: {duration:.2f} seconds")
print(f"  - Container format size: {probe_data['format']['size']} bytes")

video_stream = next((s for s in probe_data['streams'] if s['codec_type'] == 'video'), None)
audio_stream = next((s for s in probe_data['streams'] if s['codec_type'] == 'audio'), None)

assert video_stream is not None, "No video stream found in rendered reel!"
assert audio_stream is not None, "No audio stream found in rendered reel!"

width = int(video_stream['width'])
height = int(video_stream['height'])
v_codec = video_stream['codec_name']
a_codec = audio_stream['codec_name']

print(f"  - Video stream: {v_codec}, {width}x{height}, fps={video_stream.get('r_frame_rate')}")
print(f"  - Audio stream: {a_codec}")

assert width == 1080 and height == 1920, f"Expected 1080x1920, got {width}x{height}"
assert duration > 0, "Video duration must be > 0"

# 9. Full FFmpeg decode test
print("\n[Step 7] Testing full video decoding with FFmpeg...")
decode_cmd = ["ffmpeg", "-v", "error", "-xerror", "-i", str(dest_mp4), "-f", "null", "-"]
decode_res = subprocess.run(decode_cmd, capture_output=True, text=True)
if decode_res.returncode == 0:
    print("[OK] FFmpeg full decode succeeded with 0 errors!")
else:
    print(f"[FAIL] FFmpeg decode errors: {decode_res.stderr}")
    sys.exit(1)

# 10. Verify FastAPI read API can see this Django-created job
print("\n[Step 8] Verifying FastAPI :8787 read API sees the Django-created job...")
fa_resp = httpx.get(f"http://127.0.0.1:8787/api/projects/{PROJECT_ID}/jobs", timeout=10.0)
assert fa_resp.status_code == 200
matching_fa = [j for j in fa_resp.json() if j['id'] == jid]
assert len(matching_fa) == 1, "FastAPI could not see the Django-created job!"
print(f"[OK] FastAPI verified seeing job {jid} with state: {matching_fa[0]['state']}")

# 11. Verify Django export serving endpoint (GET /api/exports/<jid>/reel.mp4)
print("\n[Step 9] Verifying Django HTTP export streaming endpoint...")
export_http_resp = httpx.get(f"{DJANGO_BASE}/api/exports/{jid}/reel.mp4", headers={'Range': 'bytes=0-1023'}, timeout=10.0)
print(f"Export HTTP status: {export_http_resp.status_code}")
assert export_http_resp.status_code == 206
assert export_http_resp.headers['Content-Range'].startswith('bytes 0-1023/')
assert len(export_http_resp.content) == 1024
print("[OK] Django export endpoint streamed range successfully!")

print("\n" + "=" * 60)
print("REAL DJANGO-TRIGGERED INTEGRATION TEST PASSED!")
print(f"Job ID: {jid}")
print(f"Export Path: {dest_mp4}")
print(f"Dimensions: {width}x{height}")
print(f"Duration: {duration:.2f}s")
print(f"Size: {file_size} bytes")
print("=" * 60)
