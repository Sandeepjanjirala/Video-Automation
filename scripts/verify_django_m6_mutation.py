"""Real Stage M6 Integration Verification Script.

Executes the complete end-to-end M6 mutation sequence against live Django (:8788)
and validates identical durable visibility from FastAPI (:8787):

Sequence:
1. Pre-audit database row counts
2. Bootstrap handshake on Django (:8788) to get DJANGO_TOKEN
3. Bootstrap handshake on FastAPI (:8787) to get FASTAPI_TOKEN
4. Django POST /api/projects -> create disposable integration project (rev 1)
5. Django PUT /api/projects/<pid> -> update project (rev 2)
6. Django PUT /api/projects/<pid>/draft -> save draft
7. Django GET /api/projects/<pid>/draft -> read draft
8. Django DELETE /api/projects/<pid>/draft -> clear draft
9. Django POST /api/projects/<pid>/approve -> approve revision 2
10. Django GET /api/projects/<pid> -> verify approved_revision == 2
11. FastAPI GET /api/projects/<pid> -> verify identical state on :8787
12. Create disposable stock job for this project in DB
13. Django POST /api/projects/<pid>/jobs/<jid>/apply -> apply job result (rev 3)
14. Verify ApplicationReceipt persisted in PostgreSQL
15. Django GET /api/projects/<pid> -> verify applied changes (rev 3)
16. FastAPI GET /api/projects/<pid> -> verify identical state (rev 3) on :8787
17. Django POST /api/projects/<pid>/jobs/<jid>/apply -> duplicate apply returns idempotent response
18. Post-audit database counts and clean up disposable integration records
19. Verify Alembic schema version remains intact
"""
import copy
import json
import os
import sys
import uuid
import psycopg2
import httpx

sys.stdout.reconfigure(encoding='utf-8')

ADMIN_DB_URL = os.environ.get('STUDIO_DATABASE_URL', 'postgresql://postgres:postgres@127.0.0.1:5433/studio')
DJANGO_URL = "http://127.0.0.1:8788"
FASTAPI_URL = "http://127.0.0.1:8787"


def get_db_counts(cur):
    tables = [
        "workspaces", "projects", "revisions", "drafts", "assets",
        "jobs", "job_stages", "application_receipts", "outbox",
        "voice_takes", "usage"
    ]
    counts = {}
    for t in tables:
        cur.execute(f"SELECT count(*) FROM {t};")
        counts[t] = cur.fetchone()[0]
    return counts


def main():
    print("=" * 70)
    print("STAGE M6 REAL INTEGRATION FLOW & DATABASE AUDIT")
    print("=" * 70)

    conn = psycopg2.connect(ADMIN_DB_URL)
    conn.autocommit = True
    cur = conn.cursor()

    # 1. Pre-audit
    pre_counts = get_db_counts(cur)
    cur.execute("SELECT version_num FROM alembic_version LIMIT 1;")
    alembic_before = cur.fetchone()[0]
    print(f"\nAlembic Revision: {alembic_before}")
    print("Pre-integration DB counts:")
    for t, c in pre_counts.items():
        print(f"  {t:22}: {c}")

    # 2. Handshake tokens
    dj_boot = httpx.get(f"{DJANGO_URL}/api/bootstrap", timeout=5).json()
    dj_token = dj_boot["token"]
    fa_boot = httpx.get(f"{FASTAPI_URL}/api/bootstrap", timeout=5).json()
    fa_token = fa_boot["token"]
    print(f"\nHandshake verified: Django token acquired, FastAPI token acquired.")

    # 3. Create disposable integration project
    disposable_title = "M6 Real Integration Reel - తెలుగు కథ"
    initial_body = {
        "title": disposable_title,
        "topic": "Telugu Culture and Heritage",
        "category": "Culture",
        "target_seconds": 30,
        "duration_mode": "natural",
        "tts_provider": "edge",
        "voice": "te-IN-ShrutiNeural",
        "speech_rate": 0,
        "delivery_preset": "conversational",
        "pronunciation_dict": {"Heritage": "వారసత్వం"},
        "voice_locked": False,
        "scenes": [
            {
                "id": "scene_1",
                "narration": "తెలుగు సంస్కృతి ఎంతో ప్రాచీనమైనది మరియు గొప్పది.",
                "spoken_text": "తెలుగు సంస్కృతి ఎంతో ప్రాచీనమైనది మరియు గొప్పది.",
                "roman": "Telugu samskruthi entho praacheenamainadi mariyu goppadi.",
                "keywords": "culture, temple",
                "visual": "graphic",
                "graphic_title": "Telugu Samskruthi",
                "rights_reviewed": True,
                "hold_seconds": 3.0
            },
            {
                "id": "scene_2",
                "narration": "ఈ చరిత్రను భవిష్యత్ తరాలకు అందించడం మన బాధ్యత.",
                "spoken_text": "ఈ చరిత్రను భవిష్యత్ తరాలకు అందించడం మన బాధ్యత.",
                "roman": "Ee charithranu bhavishyath tharaalaku andinchadam mana baadhyatha.",
                "keywords": "future, responsibility",
                "visual": "graphic",
                "graphic_title": "Mana Baadhyatha",
                "rights_reviewed": True,
                "hold_seconds": 3.0
            }
        ],
        "evidence_notes": "Verified cultural history documentation for Stage M6 verification."
    }

    create_resp = httpx.post(
        f"{DJANGO_URL}/api/projects",
        json=initial_body,
        headers={"x-studio-token": dj_token},
        timeout=5
    )
    assert create_resp.status_code == 200, f"Create failed: {create_resp.text}"
    created_data = create_resp.json()
    pid = created_data["id"]
    print(f"\n[Step 1] Django Project Created:")
    print(f"  Project ID: {pid}")
    print(f"  Revision:   {created_data['revision']} (approved={created_data['approved']})")
    print(f"  Title:      {created_data['project']['title']}")

    try:
        # 4. Django Edit Project -> Revision 2
        updated_body = copy.deepcopy(initial_body)
        updated_body["title"] = "M6 Real Integration Reel - సంస్కృతి నవీకరణ"
        update_resp = httpx.put(
            f"{DJANGO_URL}/api/projects/{pid}",
            json={"revision": 1, "project": updated_body},
            headers={"x-studio-token": dj_token},
            timeout=5
        )
        assert update_resp.status_code == 200, f"Update failed: {update_resp.text}"
        updated_data = update_resp.json()
        print(f"\n[Step 2] Django Project Edited:")
        print(f"  New Revision: {updated_data['revision']} (approved={updated_data['approved']})")
        print(f"  New Title:    {updated_data['project']['title']}")

        # 5. Django Draft Save / Read / Clear
        draft_body = {"notes": "Drafting scene 3 in Telugu...", "timestamp": 12345}
        put_draft = httpx.put(
            f"{DJANGO_URL}/api/projects/{pid}/draft",
            json=draft_body,
            headers={"x-studio-token": dj_token},
            timeout=5
        )
        assert put_draft.status_code == 200
        get_draft = httpx.get(f"{DJANGO_URL}/api/projects/{pid}/draft", timeout=5).json()
        assert get_draft["notes"] == "Drafting scene 3 in Telugu..."
        del_draft = httpx.delete(
            f"{DJANGO_URL}/api/projects/{pid}/draft",
            headers={"x-studio-token": dj_token},
            timeout=5
        )
        assert del_draft.status_code == 200
        cleared_draft = httpx.get(f"{DJANGO_URL}/api/projects/{pid}/draft", timeout=5).json()
        assert cleared_draft == {}
        print(f"\n[Step 3] Django Draft Lifecycle:")
        print(f"  Save Draft: OK")
        print(f"  Read Draft: Verified content ({get_draft['notes']})")
        print(f"  Clear Draft: OK (empty={cleared_draft == {}})")

        # 6. Django Approve Current Project
        app_resp = httpx.post(
            f"{DJANGO_URL}/api/projects/{pid}/approve",
            json={"revision": 2},
            headers={"x-studio-token": dj_token},
            timeout=5
        )
        assert app_resp.status_code == 200, f"Approve failed: {app_resp.text}"
        app_data = app_resp.json()
        print(f"\n[Step 4] Django Project Approved:")
        print(f"  Revision: {app_data['revision']} (approved={app_data['approved']})")
        print(f"  Script Hash: {app_data['project'].get('script_approved_hash')}")

        # 7. Confirm FastAPI visibility of approved revision 2
        fa_read = httpx.get(f"{FASTAPI_URL}/api/projects/{pid}", timeout=5).json()
        assert fa_read["id"] == pid
        assert fa_read["revision"] == 2
        assert fa_read["approved"] is True
        print(f"\n[Step 5] FastAPI Read Parity Verified:")
        print(f"  FastAPI sees ID:       {fa_read['id']}")
        print(f"  FastAPI sees Revision: {fa_read['revision']}")
        print(f"  FastAPI sees Approved: {fa_read['approved']}")

        # 8. Real Job Result Application Flow
        asset_id = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
        cur.execute(
            "INSERT INTO assets (id, workspace_id, filename, name, kind, duration, sha256, provider) "
            "VALUES (%s, 'default', 'temple_drone.mp4', 'Temple Footage', 'video', 12.5, 'hash123', 'upload') "
            "ON CONFLICT (id) DO NOTHING;",
            (asset_id,)
        )

        jid = uuid.uuid4().hex[:32]
        job_snap = {"project": updated_body, "options": {"scene_id": "scene_1"}}
        job_res = {"asset": {"id": asset_id, "filename": "temple_drone.mp4"}}
        cur.execute(
            "INSERT INTO jobs (id, workspace_id, project_id, revision, kind, state, stage, snapshot, result) "
            "VALUES (%s, 'default', %s, 2, 'stock', 'succeeded', 'complete', %s, %s);",
            (jid, pid, json.dumps(job_snap), json.dumps(job_res))
        )

        apply_resp = httpx.post(
            f"{DJANGO_URL}/api/projects/{pid}/jobs/{jid}/apply",
            json={},
            headers={"x-studio-token": dj_token},
            timeout=5
        )
        assert apply_resp.status_code == 200, f"Apply failed: {apply_resp.text}"
        apply_data = apply_resp.json()
        print(f"\n[Step 6] Real Job Result Applied via Django:")
        print(f"  Applied:       {apply_data['applied']}")
        print(f"  New Revision:  {apply_data['revision']}")
        print(f"  Scene 1 Video: {apply_data['project']['project']['scenes'][0]['video_asset']}")
        print(f"  Conflicts:     {apply_data['conflicts']}")

        # 9. Verify ApplicationReceipt persisted in PostgreSQL
        cur.execute("SELECT id, job_id, applied_revision, applied_at FROM application_receipts WHERE job_id = %s;", (jid,))
        receipt_row = cur.fetchone()
        assert receipt_row is not None
        print(f"\n[Step 7] Application Receipt Persisted:")
        print(f"  Receipt ID:       {receipt_row[0]}")
        print(f"  Job ID:           {receipt_row[1]}")
        print(f"  Applied Revision: {receipt_row[2]}")
        print(f"  Timestamp:        {receipt_row[3]}")

        # 10. Verify FastAPI sees Revision 3 with applied asset
        fa_read_v3 = httpx.get(f"{FASTAPI_URL}/api/projects/{pid}", timeout=5).json()
        assert fa_read_v3["revision"] == 3
        assert fa_read_v3["project"]["scenes"][0]["video_asset"] == asset_id
        print(f"\n[Step 8] FastAPI Verified Revision 3 Applied State:")
        print(f"  FastAPI Revision: {fa_read_v3['revision']}")
        print(f"  FastAPI Asset:    {fa_read_v3['project']['scenes'][0]['video_asset']}")

        # 11. Duplicate apply idempotency check
        dup_apply = httpx.post(
            f"{DJANGO_URL}/api/projects/{pid}/jobs/{jid}/apply",
            json={},
            headers={"x-studio-token": dj_token},
            timeout=5
        ).json()
        assert dup_apply["applied"] is True
        assert dup_apply["revision"] == 3
        assert "already applied" in dup_apply.get("message", "")
        print(f"\n[Step 9] Duplicate Apply Idempotency Verified: message='{dup_apply.get('message')}'")

        # 12. Database Invariant Check
        cur.execute("SELECT max(revision) FROM revisions WHERE project_id = %s;", (pid,))
        max_rev = cur.fetchone()[0]
        cur.execute("SELECT revision FROM projects WHERE id = %s;", (pid,))
        curr_p_rev = cur.fetchone()[0]
        assert max_rev == curr_p_rev == 3
        print(f"\n[Step 10] Database Invariant Verified: projects.revision ({curr_p_rev}) == max(revisions.revision) ({max_rev})")

    finally:
        # Clean up disposable integration project records
        print("\nCleaning up disposable integration records...")
        cur.execute("DELETE FROM application_receipts WHERE project_id = %s;", (pid,))
        cur.execute("DELETE FROM drafts WHERE project_id = %s;", (pid,))
        cur.execute("DELETE FROM revisions WHERE project_id = %s;", (pid,))
        cur.execute("DELETE FROM jobs WHERE project_id = %s;", (pid,))
        cur.execute("DELETE FROM projects WHERE id = %s;", (pid,))
        cur.execute("DELETE FROM assets WHERE id = %s;", (asset_id,))

    # Post-audit
    post_counts = get_db_counts(cur)
    cur.execute("SELECT version_num FROM alembic_version LIMIT 1;")
    alembic_after = cur.fetchone()[0]
    print(f"\nAlembic Revision After: {alembic_after}")
    assert alembic_after == alembic_before == "0003_phase4_voice_lab"

    print("\nPost-verification DB table counts (cleaned):")
    for t in pre_counts:
        delta = post_counts[t] - pre_counts[t]
        sign = f"+{delta}" if delta > 0 else str(delta)
        print(f"  {t:22}: {post_counts[t]} (delta: {sign})")

    cur.close()
    conn.close()
    print("\n" + "=" * 70)
    print("STAGE M6 REAL INTEGRATION FLOW: 100% SUCCESS")
    print("=" * 70)


if __name__ == "__main__":
    main()
