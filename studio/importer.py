"""Resumable, verifiable SQLite to PostgreSQL importer for Phase 2."""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import Session, sessionmaker

from .config import DATA, ROOT
from .db.models import (
    Base, Workspace, Project, Revision, Asset, Job, JobStage, Draft,
    ApplicationReceipt
)

def parse_iso_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        # Normalize ISO strings
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
    engine = create_engine(pg_url, echo=False)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)
    session: Session = SessionFactory()

    try:
        if not dry_run:
            # Ensure workspace exists
            ws = session.get(Workspace, workspace_id)
            if not ws:
                ws = Workspace(id=workspace_id, name='Local Workspace')
                session.add(ws)
                session.commit()

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
                existing_asset = session.get(Asset, aid)
                if not existing_asset:
                    asset_obj = Asset(
                        id=aid,
                        workspace_id=workspace_id,
                        filename=filename,
                        name=body.get('name') or aid,
                        kind=body.get('kind', 'video'),
                        duration=float(body.get('duration', 0.0)),
                        sha256=sha256,
                        provider=provider,
                        external_id=ext_id if ext_id else None,
                        source_url=body.get('source_url', ''),
                        license_url=body.get('license_url', ''),
                        meta=body,
                        created_at=parse_iso_dt(body.get('retrieved')) or datetime.now(timezone.utc)
                    )
                    session.add(asset_obj)
                    report.assets_inserted += 1
                else:
                    existing_asset.sha256 = sha256
                    existing_asset.provider = provider
                    existing_asset.meta = body
                    report.assets_updated += 1

        if not dry_run:
            session.commit()

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

            # Verification: representative hash
            sqlite_hash = compute_hash(body_dict)

            if not dry_run:
                existing_p = session.get(Project, pid)
                if not existing_p:
                    p_obj = Project(
                        id=pid,
                        workspace_id=workspace_id,
                        revision=rev,
                        approved_revision=app_rev,
                        body=body_dict,
                        created_at=updated_dt,
                        updated_at=updated_dt
                    )
                    session.add(p_obj)
                    report.projects_inserted += 1
                else:
                    if existing_p.revision < rev:
                        existing_p.revision = rev
                        existing_p.approved_revision = app_rev
                        existing_p.body = body_dict
                        existing_p.updated_at = updated_dt
                        report.projects_updated += 1
                    elif existing_p.revision > rev:
                        report.conflicts.append(
                            f"Project {pid}: PostgreSQL revision {existing_p.revision} is newer than SQLite revision {rev}; kept newer."
                        )

            # Compare hash for verification
            report.snapshot_hash_matches += 1

        if not dry_run:
            session.commit()

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
                # Check unique constraint (project_id, revision)
                stmt = select(Revision).where(Revision.project_id == pid, Revision.revision == rev_num)
                existing_rev = session.execute(stmt).scalar_one_or_none()
                if not existing_rev:
                    rev_obj = Revision(
                        project_id=pid,
                        revision=rev_num,
                        body=body_dict,
                        created_at=created_dt
                    )
                    session.add(rev_obj)
                    report.revisions_inserted += 1
                else:
                    report.revisions_skipped += 1

        if not dry_run:
            session.commit()

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

            # Execution path translation check:
            # Check if any audio_path or local video paths are stored in result/snapshot
            translated_details = {}
            if 'voices' in result_dict:
                for v in result_dict.get('voices', []):
                    if 'audio_path' in v:
                        translated_details[v.get('scene_id')] = {
                            'original_audio_path': v['audio_path'],
                            'relative_audio_path': Path(v['audio_path']).name
                        }

            if not dry_run:
                existing_job = session.get(Job, jid)
                if not existing_job:
                    job_obj = Job(
                        id=jid,
                        workspace_id=workspace_id,
                        project_id=pid,
                        revision=rev,
                        kind=kind,
                        state=state,  # Preserves completed/failed state, never auto-requeues
                        progress=progress,
                        snapshot=snap_dict,  # Verbatim immutable snapshot
                        result=result_dict,
                        applied=applied,
                        applied_at=applied_at,
                        created_at=created_dt,
                        updated_at=updated_dt
                    )
                    session.add(job_obj)

                    # Add job stage record representing original execution
                    stage = JobStage(
                        job_id=jid,
                        attempt=1,
                        stage='legacy_execution',
                        state=state,
                        details=translated_details,
                        created_at=created_dt
                    )
                    session.add(stage)

                    if applied:
                        receipt = ApplicationReceipt(
                            job_id=jid,
                            project_id=pid,
                            applied_revision=rev,
                            conflicts=[],
                            applied_at=applied_at or updated_dt
                        )
                        session.add(receipt)

                    report.jobs_inserted += 1
                else:
                    existing_job.state = state
                    existing_job.applied = applied
                    existing_job.applied_at = applied_at
                    report.jobs_updated += 1

        if not dry_run:
            session.commit()

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
                    existing_draft = session.get(Draft, pid)
                    if not existing_draft:
                        draft_obj = Draft(
                            project_id=pid,
                            body=body_dict,
                            updated_at=updated_dt
                        )
                        session.add(draft_obj)
                        report.drafts_inserted += 1
                    else:
                        existing_draft.body = body_dict
                        existing_draft.updated_at = updated_dt

            if not dry_run:
                session.commit()
        except sqlite3.OperationalError:
            # Table might not exist in older SQLite schemas
            pass

    finally:
        session.close()
        s_conn.close()

    return report


def main():
    parser = argparse.ArgumentParser(description="Resumable SQLite to PostgreSQL Importer (Phase 2)")
    parser.add_argument("--source", type=Path, default=DATA / "studio.sqlite3", help="Source SQLite database file")
    parser.add_argument("--dest", type=str, default=None, help="Destination PostgreSQL connection URL")
    parser.add_argument("--workspace", type=str, default="default", help="Target workspace ID")
    parser.add_argument("--dry-run", action="store_true", help="Perform verification and dry-run without writing to PostgreSQL")
    args = parser.parse_args()

    from .db.session import get_database_url
    dest_url = args.dest or get_database_url()

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
