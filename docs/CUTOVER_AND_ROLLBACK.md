# PostgreSQL Cutover and Rollback Runbook (Phase 2)

This document specifies the operational procedure for cutting over the Telugu Reel Studio primary persistence layer from legacy SQLite (`data/studio.sqlite3`) to PostgreSQL, as well as the safety, maintenance-mode, and rollback protocols.

---

## 1. Safety Principles & Pre-Cutover Requirements

1. **Explicit User Approval Required**: Migration and live cutover must **never** be performed automatically without explicit stakeholder/user confirmation.
2. **Maintenance Mode Window**: Writes to the application must be stopped during migration to prevent split-brain state or lost updates.
3. **Rollback Reality**: Once new writes exist in PostgreSQL, simply switching the connection string back to SQLite is **NOT a safe rollback**. New projects, edits, and completed renders made in PostgreSQL would be invisible or lost in SQLite. Rollback requires either data reconciliation back to SQLite or restoring pre-cutover backups with explicit user acknowledgment.

---

## 2. Infrastructure & Prerequisites

- **PostgreSQL Service**: PostgreSQL 18 running on port `5433` (via WSL2 Ubuntu service or Docker Compose).
  - Verify status:
    ```cmd
    scripts\launch_postgres.bat
    ```
  - Connection URL:
    ```
    STUDIO_DATABASE_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/studio
    ```
- **Alembic Migrations**:
  Schema must be migrated to `head` prior to cutover:
  ```cmd
  .venv\Scripts\python.exe -m alembic upgrade head
  ```

---

## 3. Production Cutover Procedure

### Step 1: Pre-Cutover Backup & Maintenance Mode
1. Stop any running API server or background worker (`START_WINDOWS.bat` / `run.py`).
2. Make a timestamped read-only snapshot backup of SQLite database and assets:
   ```cmd
   mkdir data\backups
   copy data\studio.sqlite3 data\backups\studio_pre_cutover_%DATE%.sqlite3
   ```

### Step 2: Migration Dry-Run
Run the resumable importer in `--dry-run` mode to inspect row counts, foreign key integrity, snapshot hashes, and media file references:
```cmd
.venv\Scripts\python.exe scripts\migrate_sqlite_to_pg.py --sqlite data\studio.sqlite3 --target-db postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/studio --dry-run
```
Verify the output:
- `missing_media_count: 0`
- `conflicts_count: 0`
- `snapshot_hash_verification: mismatches: 0`

### Step 3: Execute Resumable Importer
Execute the actual migration into the target PostgreSQL database:
```cmd
.venv\Scripts\python.exe scripts\migrate_sqlite_to_pg.py --sqlite data\studio.sqlite3 --target-db postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/studio
```

### Step 4: Verification of Migrated Data
1. Check row counts and Telugu Unicode fidelity:
   ```cmd
   .venv\Scripts\python.exe -c "from studio.db.session import get_engine; from sqlalchemy import text; eng = get_engine('postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/studio'); \
   with eng.connect() as c: \
       print('Projects:', c.execute(text('SELECT count(*) FROM projects')).scalar()); \
       print('Revisions:', c.execute(text('SELECT count(*) FROM revisions')).scalar()); \
       print('Assets:', c.execute(text('SELECT count(*) FROM assets')).scalar()); \
       print('Jobs:', c.execute(text('SELECT count(*) FROM jobs')).scalar())"
   ```
2. Verify repeatability: Re-running the command should report `0 inserted` across all tables (fully idempotent).

### Step 5: Archive SQLite & Enable PostgreSQL Default
1. Rename legacy SQLite database to prevent accidental concurrent writes:
   ```cmd
   rename data\studio.sqlite3 studio.sqlite3.migrated
   ```
2. Start the application:
   ```cmd
   START_WINDOWS.bat
   ```
3. Check `http://127.0.0.1:8787` in browser to confirm active workspace, projects, and media library are live.

---

## 4. Rollback Protocol

### Scenario A: Rollback BEFORE Any New Writes Occur in PostgreSQL
If an issue is detected immediately post-cutover before users have saved projects or rendered reels:
1. Stop the application server (`run.py` / `START_WINDOWS.bat`).
2. Restore the legacy SQLite database:
   ```cmd
   copy data\studio.sqlite3.migrated data\studio.sqlite3
   ```
3. Restart application with SQLite fallback (if configured) or investigate PostgreSQL issues while keeping data intact.

### Scenario B: Rollback AFTER New Writes Occur in PostgreSQL
If the application has been running and users have created new projects, saved edits, or generated renders in PostgreSQL:
1. **DO NOT** simply switch the connection string back to SQLite; this creates silent data divergence.
2. Put the application in maintenance mode (shut down `run.py`).
3. Export newly created/modified records from PostgreSQL:
   - Identify projects where `updated_at > cutover_timestamp`.
   - Export corresponding revisions and completed jobs.
4. If reverting to SQLite, run an inverse sync script to apply those project updates to `data/studio.sqlite3`.
5. Alternatively, resolve the underlying PostgreSQL infrastructure issue in-place (recommended, as PostgreSQL with persistent storage and connection pooling is the durable target).

---

## 5. Troubleshooting & Health Checks

- **PostgreSQL Connection Refused**:
  - Run `scripts\launch_postgres.bat` to automatically check and start PostgreSQL in WSL2 or Docker Compose.
  - Or manually start WSL Ubuntu service:
    ```cmd
    wsl -u root -d Ubuntu -- service postgresql start
    ```
- **Port Conflict**:
  - PostgreSQL is specifically configured on port `5433` (`0.0.0.0:5433`) to prevent collision with Windows native services on default 5432.
