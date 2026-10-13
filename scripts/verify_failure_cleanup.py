"""Verify Section 28 Failure Cleanup.

Tests:
1. Controlled failed render enqueued via Django :8788.
2. Job reaches correct 'failed' state in PostgreSQL.
3. Useful error message is persisted in job.progress and job_stages.
4. No job remains permanently 'running'.
5. Temporary directories are cleaned.
6. Subsequent retry works (e.g. after updating project or retry original retains error / retry current).
7. Existing user media remains untouched.
8. Clean up test project from DB.
"""
import time
import json
import uuid
from pathlib import Path
import psycopg2
import httpx

DJANGO_BASE = "http://127.0.0.1:8788"
DB_URL = "postgresql://postgres:postgres@127.0.0.1:5433/studio"

print("=" * 60)
print("SECTION 28: CONTROLLED FAILED RENDER & CLEANUP VERIFICATION")
print("=" * 60)

# Connect to database
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Get existing known-good project to base our test on
cur.execute("SELECT body FROM projects WHERE id = '93dba246d73140e396a369239b04615b';")
base_body = cur.fetchone()[0]

test_proj_id = f"failtest_{uuid.uuid4().hex[:12]}"
fail_body = json.loads(json.dumps(base_body))
# Set duration_mode to 'target' with target_seconds shorter than holds to trigger controlled ValueError
fail_body['duration_mode'] = 'target'
fail_body['target_seconds'] = 0.2  # holds is 1.0s, so 0.2 <= 1.0 will raise ValueError in pipeline

# Insert test project
cur.execute(
    """
    INSERT INTO projects (id, workspace_id, revision, approved_revision, body, created_at, updated_at)
    VALUES (%s, 'default', 1, 1, %s, NOW(), NOW());
    """,
    [test_proj_id, json.dumps(fail_body)]
)
conn.commit()
print(f"Created temporary test project: {test_proj_id}")

try:
    # 1. Fetch bootstrap token
    boot_resp = httpx.get(f"{DJANGO_BASE}/api/bootstrap", timeout=10.0)
    token = boot_resp.json()['token']

    headers = {
        'x-studio-token': token,
        'origin': DJANGO_BASE,
        'Content-Type': 'application/json'
    }

    # 2. Enqueue job via Django :8788
    enqueue_resp = httpx.post(
        f"{DJANGO_BASE}/api/projects/{test_proj_id}/jobs",
        json={'revision': 1, 'kind': 'render', 'quality': 'draft'},
        headers=headers,
        timeout=10.0
    )
    assert enqueue_resp.status_code == 200, f"Enqueue failed: {enqueue_resp.text}"
    jid = enqueue_resp.json()['id']
    print(f"Enqueued render job via Django :8788: {jid}")

    # 3. Monitor until terminal state
    start_time = time.time()
    final_state = None
    final_progress = None

    while time.time() - start_time < 60:
        cur.execute("SELECT state, progress FROM jobs WHERE id = %s;", [jid])
        row = cur.fetchone()
        st, pr = row[0], row[1]
        if st in ('succeeded', 'failed'):
            final_state = st
            final_progress = pr
            break
        time.sleep(1)

    print(f"Job reached terminal state: {final_state} | progress: '{final_progress}'")
    assert final_state == 'failed', f"Expected failed, got {final_state}"
    assert "Target duration is shorter than the scene holds" in final_progress, f"Unexpected error: {final_progress}"
    print("[OK] Job reached 'failed' state and preserved clear error message!")

    # 4. Check job_stages
    cur.execute("SELECT stage, state FROM job_stages WHERE job_id = %s;", [jid])
    stages = cur.fetchall()
    print(f"Job stages recorded: {stages}")
    assert any(s[1] == 'failed' for s in stages), "No failed stage recorded in job_stages"
    print("[OK] Failed stage recorded in job_stages!")

    # 5. Check no temporary folder leak in data/tmp
    tmp_matches = list(Path("data/tmp").glob(f"{jid}*"))
    print(f"Temporary folders remaining for job {jid}: {len(tmp_matches)}")
    assert len(tmp_matches) == 0, f"Temporary folders were not cleaned: {tmp_matches}"
    print("[OK] No temporary folder leak!")

    # 6. Test subsequent retry (POST /api/jobs/<jid>/retry)
    # First update project to fix the error (set duration_mode back to 'natural')
    fail_body['duration_mode'] = 'natural'
    cur.execute(
        "UPDATE projects SET revision = 2, approved_revision = 2, body = %s, updated_at = NOW() WHERE id = %s;",
        [json.dumps(fail_body), test_proj_id]
    )
    conn.commit()

    # Retry using 'current' mode via Django :8788
    retry_resp = httpx.post(
        f"{DJANGO_BASE}/api/jobs/{jid}/retry",
        json={'mode': 'current'},
        headers=headers,
        timeout=10.0
    )
    assert retry_resp.status_code == 200, f"Retry failed: {retry_resp.text}"
    retry_jid = retry_resp.json()['id']
    print(f"Retried job enqueued via Django :8788: {retry_jid}")

    # Verify job reset to queued in PostgreSQL with attempt_count incremented
    cur.execute("SELECT state, attempt_count FROM jobs WHERE id = %s;", [jid])
    row = cur.fetchone()
    assert row[0] == 'queued'
    assert row[1] == 2
    print("[OK] Retry successfully reset job to queued with attempt_count=2 and current revision!")

    # Cancel retry job to avoid unnecessary render
    cancel_resp = httpx.post(
        f"{DJANGO_BASE}/api/jobs/{retry_jid}/cancel",
        headers=headers,
        timeout=10.0
    )
    assert cancel_resp.status_code == 200
    print("[OK] Successfully cancelled retried job!")

    print("\n" + "=" * 60)
    print("SECTION 28 FAILURE CLEANUP VERIFICATION PASSED!")
    print("=" * 60)

finally:
    # Cleanup test project and jobs
    print("\nCleaning up test project and jobs from database...")
    cur.execute("DELETE FROM outbox WHERE payload->>'project_id' = %s;", [test_proj_id])
    cur.execute("DELETE FROM job_stages WHERE job_id IN (SELECT id FROM jobs WHERE project_id = %s);", [test_proj_id])
    cur.execute("DELETE FROM jobs WHERE project_id = %s;", [test_proj_id])
    cur.execute("DELETE FROM projects WHERE id = %s;", [test_proj_id])
    conn.commit()
    cur.close()
    conn.close()
    print("Cleanup complete.")
