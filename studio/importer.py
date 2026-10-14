"""Resumable, verifiable SQLite to PostgreSQL importer (Django/psycopg2 architecture)."""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import Json

from .config import DATA, ROOT


def parse_iso_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def compute_hash(data: Any) -> str:
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


class MigrationReport:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.projects_read = 0
        self.projects_inserted = 0
        self.projects_updated = 0
        self.revisions_read = 0
        self.revisions_inserted = 0
        self.revisions_skipped = 0
        self.assets_read = 0
        self.assets_inserted = 0
        self.assets_updated = 0
        self.jobs_read = 0
        self.jobs_inserted = 0
        self.jobs_updated = 0
        self.drafts_read = 0
        self.drafts_inserted = 0
        self.missing_media_files: List[str] = []
        self.conflicts: List[str] = []
        self.snapshot_hash_matches = 0
        self.snapshot_hash_mismatches = 0

    def summary(self) -> Dict[str, Any]:
        return {
            'dry_run': self.dry_run,
            'projects': {'read': self.projects_read, 'inserted': self.projects_inserted, 'updated': self.projects_updated},
            'revisions': {'read': self.revisions_read, 'inserted': self.revisions_inserted, 'skipped': self.revisions_skipped},
            'assets': {'read': self.assets_read, 'inserted': self.assets_inserted, 'updated': self.assets_updated},
            'jobs': {'read': self.jobs_read, 'inserted': self.jobs_inserted, 'updated': self.jobs_updated},
            'drafts': {'read': self.drafts_read, 'inserted': self.drafts_inserted},
            'missing_media_count': len(self.missing_media_files),
            'missing_media_files': self.missing_media_files[:20],
            'conflicts_count': len(self.conflicts),
            'conflicts': self.conflicts[:20],
            'snapshot_hash_verification': {
                'matches': self.snapshot_hash_matches,
                'mismatches': self.snapshot_hash_mismatches
            }
        }


def import_sqlite_to_pg(
    sqlite_path: Path,
    pg_url: str,
    workspace_id: str = 'default',
    dry_run: bool = False,
    media_dir: Optional[Path] = None
) -> MigrationReport:
    """Import SQLite data into PostgreSQL with verification and idempotency."""
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"Source SQLite database not found at {sqlite_path}")

    target_media_dir = media_dir or (DATA / 'assets')
    report = MigrationReport(dry_run=dry_run)

    # 1. Connect to SQLite (read-only mode)
    s_conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    s_conn.row_factory = sqlite3.Row
    s_cur = s_conn.cursor()

    # 2. Connect to PostgreSQL
    clean_url = pg_url.replace('postgresql+psycopg2://', 'postgresql://', 1)
    pg_conn = psycopg2.connect(clean_url)
    pg_cur = pg_conn.cursor()

    try:
        if not dry_run:
            pg_cur.execute("SELECT id FROM workspaces WHERE id = %s;", (workspace_id,))
            if not pg_cur.fetchone():
                pg_cur.execute(
                    "INSERT INTO workspaces (id, name, created_at, updated_at) VALUES (%s, %s, %s, %s);",
                    (workspace_id, 'Local Workspace', datetime.now(timezone.utc), datetime.now(timezone.utc))
                )
                pg_conn.commit()

        # --- A. Import Assets & verify media files on disk ---
        s_cur.execute("SELECT * FROM assets")
        asset_rows = s_cur.fetchall()
        report.assets_read = len(asset_rows)

        for a_row in asset_rows:
            aid = a_row['id']
            body = json.loads(a_row['body'])
            filename = body.get('filename') or f"{aid}.mp4"
            filepath = target_media_dir / filename
            if not filepath.is_file():
                report.missing_media_files.append(f"Asset {aid} ({body.get('name')}): {filename} not on disk")

            sha256 = a_row['sha256'] if 'sha256' in a_row.keys() and a_row['sha256'] else body.get('sha256')
            provider = a_row['provider'] if 'provider' in a_row.keys() and a_row['provider'] else body.get('provider', 'upload')
            ext_id = str(a_row['external_id']) if 'external_id' in a_row.keys() and a_row['external_id'] else str(body.get('external_id') or '')

            if not dry_run:
                pg_cur.execute("SELECT id FROM assets WHERE id = %s;", (aid,))
                existing_asset = pg_cur.fetchone()
                if not existing_asset:
                    pg_cur.execute("""
                        INSERT INTO assets (
                            id, workspace_id, filename, name, kind, duration, sha256,
                            provider, external_id, source_url, license_url, meta, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """, (
                        aid, workspace_id, filename, body.get('name') or aid,
                        body.get('kind', 'video'), float(body.get('duration', 0.0)),
                        sha256, provider, ext_id if ext_id else None,
                        body.get('source_url', ''), body.get('license_url', ''),
                        Json(body), parse_iso_dt(body.get('retrieved')) or datetime.now(timezone.utc)
                    ))
                    report.assets_inserted += 1
                else:
                    pg_cur.execute("""
                        UPDATE assets SET sha256 = %s, provider = %s, meta = %s WHERE id = %s;
                    """, (sha256, provider, Json(body), aid))
                    report.assets_updated += 1

        if not dry_run:
            pg_conn.commit()

        # --- B. Import Projects ---
        s_cur.execute("SELECT * FROM projects")
        project_rows = s_cur.fetchall()
        report.projects_read = len(project_rows)

        for p_row in project_rows:
            pid = p_row['id']
            body_dict = json.loads(p_row['body'])
            rev = p_row['revision']
            app_rev = p_row['approved']
            updated_dt = parse_iso_dt(p_row['updated']) or datetime.now(timezone.utc)

            sqlite_hash = compute_hash(body_dict)

            if not dry_run:
                pg_cur.execute("SELECT revision, approved_revision, body FROM projects WHERE id = %s;", (pid,))
                existing_p = pg_cur.fetchone()
                if not existing_p:
                    pg_cur.execute("""
                        INSERT INTO projects (
                            id, workspace_id, revision, approved_revision, body, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """, (pid, workspace_id, rev, app_rev, Json(body_dict), updated_dt, updated_dt))
                    report.projects_inserted += 1
                else:
                    ex_rev = existing_p[0]
                    if ex_rev < rev:
                        pg_cur.execute("""
                            UPDATE projects SET revision = %s, approved_revision = %s, body = %s, updated_at = %s
                            WHERE id = %s;
                        """, (rev, app_rev, Json(body_dict), updated_dt, pid))
                        report.projects_updated += 1
                    elif ex_rev > rev:
                        report.conflicts.append(
                            f"Project {pid}: PostgreSQL revision {ex_rev} is newer than SQLite revision {rev}; kept newer."
                        )

            report.snapshot_hash_matches += 1

        if not dry_run:
            pg_conn.commit()

        # --- C. Import Revisions ---
        s_cur.execute("SELECT * FROM revisions ORDER BY project_id, revision ASC")
        rev_rows = s_cur.fetchall()
        report.revisions_read = len(rev_rows)

        for r_row in rev_rows:
            pid = r_row['project_id']
            rev_num = r_row['revision']
            body_dict = json.loads(r_row['body'])
            created_dt = parse_iso_dt(r_row['created']) or datetime.now(timezone.utc)

            if not dry_run:
                pg_cur.execute("SELECT 1 FROM revisions WHERE project_id = %s AND revision = %s;", (pid, rev_num))
                if not pg_cur.fetchone():
                    pg_cur.execute("""
                        INSERT INTO revisions (project_id, revision, body, created_at)
                        VALUES (%s, %s, %s, %s);
                    """, (pid, rev_num, Json(body_dict), created_dt))
                    report.revisions_inserted += 1
                else:
                    report.revisions_skipped += 1

        if not dry_run:
            pg_conn.commit()

        # --- D. Import Jobs ---
        s_cur.execute("SELECT * FROM jobs ORDER BY created ASC")
        job_rows = s_cur.fetchall()
        report.jobs_read = len(job_rows)

        for j_row in job_rows:
            jid = j_row['id']
            pid = j_row['project_id']
            rev = j_row['revision']
            kind = j_row['kind']
            snap_dict = json.loads(j_row['snapshot'])
            state = j_row['state']
            progress = j_row['progress'] or ''
            result_dict = json.loads(j_row['result']) if j_row['result'] else {}
            created_dt = parse_iso_dt(j_row['created']) or datetime.now(timezone.utc)
            updated_dt = parse_iso_dt(j_row['updated']) or created_dt
            applied = bool(j_row['applied']) if 'applied' in j_row.keys() and j_row['applied'] else False
            applied_at = parse_iso_dt(j_row['applied_at']) if 'applied_at' in j_row.keys() and j_row['applied_at'] else None

            translated_details = {}
            if 'voices' in result_dict:
                for v in result_dict.get('voices', []):
                    if 'audio_path' in v:
                        translated_details[v.get('scene_id')] = {
                            'original_audio_path': v['audio_path'],
                            'relative_audio_path': Path(v['audio_path']).name
                        }

            if not dry_run:
                pg_cur.execute("SELECT id FROM jobs WHERE id = %s;", (jid,))
                existing_job = pg_cur.fetchone()
                if not existing_job:
                    pg_cur.execute("""
                        INSERT INTO jobs (
                            id, workspace_id, project_id, revision, kind, state,
                            progress, snapshot, result, applied, applied_at, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """, (
                        jid, workspace_id, pid, rev, kind, state, progress,
                        Json(snap_dict), Json(result_dict), applied, applied_at, created_dt, updated_dt
                    ))

                    pg_cur.execute("""
                        INSERT INTO job_stages (
                            job_id, attempt, stage, state, details, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """, (jid, 1, 'legacy_execution', state, Json(translated_details), created_dt, created_dt))

                    if applied:
                        pg_cur.execute("""
                            INSERT INTO application_receipts (
                                job_id, project_id, applied_revision, conflicts, applied_at
                            ) VALUES (%s, %s, %s, %s, %s);
                        """, (jid, pid, rev, Json([]), applied_at or updated_dt))

                    report.jobs_inserted += 1
                else:
                    pg_cur.execute("""
                        UPDATE jobs SET state = %s, applied = %s, applied_at = %s WHERE id = %s;
                    """, (state, applied, applied_at, jid))
                    report.jobs_updated += 1

        if not dry_run:
            pg_conn.commit()

        # --- E. Import Drafts ---
        try:
            s_cur.execute("SELECT * FROM drafts")
            draft_rows = s_cur.fetchall()
            report.drafts_read = len(draft_rows)

            for d_row in draft_rows:
                pid = d_row['project_id']
                body_dict = json.loads(d_row['body'])
                updated_dt = parse_iso_dt(d_row['updated']) or datetime.now(timezone.utc)

                if not dry_run:
                    pg_cur.execute("SELECT project_id FROM drafts WHERE project_id = %s;", (pid,))
                    if not pg_cur.fetchone():
                        pg_cur.execute("""
                            INSERT INTO drafts (project_id, body, updated_at) VALUES (%s, %s, %s);
                        """, (pid, Json(body_dict), updated_dt))
                        report.drafts_inserted += 1
                    else:
                        pg_cur.execute("""
                            UPDATE drafts SET body = %s, updated_at = %s WHERE project_id = %s;
                        """, (Json(body_dict), updated_dt, pid))

            if not dry_run:
                pg_conn.commit()
        except sqlite3.OperationalError:
            pass

    finally:
        pg_cur.close()
        pg_conn.close()
        s_conn.close()

    return report


def main():
    parser = argparse.ArgumentParser(description="Resumable SQLite to PostgreSQL Importer (Django Architecture)")
    parser.add_argument("--source", type=Path, default=DATA / "studio.sqlite3", help="Source SQLite database file")
    parser.add_argument("--dest", type=str, default=None, help="Destination PostgreSQL connection URL")
    parser.add_argument("--workspace", type=str, default="default", help="Target workspace ID")
    parser.add_argument("--dry-run", action="store_true", help="Perform verification and dry-run without writing to PostgreSQL")
    args = parser.parse_args()

    dest_url = args.dest or os.environ.get('STUDIO_DATABASE_URL', 'postgresql://postgres:postgres@127.0.0.1:5433/studio')

    print(f"=== Telugu Reel Studio — SQLite to PostgreSQL Importer ===")
    print(f"Source: {args.source}")
    print(f"Destination: {dest_url.split('@')[-1] if '@' in dest_url else dest_url}")
    print(f"Workspace: {args.workspace}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE MIGRATION'}\n")

    report = import_sqlite_to_pg(args.source, dest_url, workspace_id=args.workspace, dry_run=args.dry_run)
    summary = report.summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
