# M9 Schema Diff Report: Alembic vs Django Baseline

**Source A (Alembic Baseline / Restored Live DB)**: `studio_m9_disposable`
**Source B (Django Migrations Baseline / Fresh DB)**: `studio_m9_fresh`

## 1. Table Inventory Comparison

| Table | In Alembic DB (A) | In Django Fresh DB (B) | Status |
| :--- | :--- | :--- | :--- |
| `alembic_version` | YES | NO | RETIRED (Alembic Authority Table) |
| `application_receipts` | YES | YES | MATCH |
| `assets` | YES | YES | MATCH |
| `django_migrations` | NO | YES | ACTIVE (Django Authority Table) |
| `drafts` | YES | YES | MATCH |
| `job_stages` | YES | YES | MATCH |
| `jobs` | YES | YES | MATCH |
| `outbox` | YES | YES | MATCH |
| `projects` | YES | YES | MATCH |
| `revisions` | YES | YES | MATCH |
| `usage` | YES | YES | MATCH |
| `voice_takes` | YES | YES | MATCH |
| `workspaces` | YES | YES | MATCH |

## 2. Application Tables Detailed Analysis

### Table `workspaces`
- **Columns (4)**: 100% Match (created_at, id, name, updated_at)
- **Types & Nullability**: 100% Match across all columns
- **Primary Key**: Match `['id']`
- **Foreign Keys (0)**: Match []
- **Indexes**: 1 matching index names
  - In Django only: ['workspaces_id_90693ab7_like']

### Table `projects`
- **Columns (7)**: 100% Match (approved_revision, body, created_at, id, revision, updated_at, workspace_id)
- **Types & Nullability**: 100% Match across all columns
- **Primary Key**: Match `['id']`
- **Foreign Keys (1)**: Match [('workspace_id', 'workspaces', 'id')]
- **Indexes**: 4 matching index names
  - In Django only: ['projects_workspace_id_82651d7a_like', 'projects_id_a0f92e8b_like', 'projects_workspace_id_82651d7a']

### Table `revisions`
- **Columns (5)**: 100% Match (body, created_at, id, project_id, revision)
- **Types & Nullability**: 100% Match across all columns
- **Primary Key**: Match `['id']`
- **Foreign Keys (1)**: Match [('project_id', 'projects', 'id')]
- **Indexes**: 3 matching index names
  - In Alembic only: ['uq_project_revision']
  - In Django only: ['revisions_project_id_a6535696_like', 'revisions_project_id_a6535696', 'revisions_project_id_revision_0a33af9c_uniq']

### Table `drafts`
- **Columns (3)**: 100% Match (body, project_id, updated_at)
- **Types & Nullability**: 100% Match across all columns
- **Primary Key**: Match `['project_id']`
- **Foreign Keys (1)**: Match [('project_id', 'projects', 'id')]
- **Indexes**: 1 matching index names
  - In Django only: ['drafts_project_id_bc58e5b7_like']

### Table `assets`
- **Columns (13)**: 100% Match (created_at, duration, external_id, filename, id, kind, license_url, meta, name, provider, sha256, source_url, workspace_id)
- **Types & Nullability**: 100% Match across all columns
- **Primary Key**: Match `['id']`
- **Foreign Keys (1)**: Match [('workspace_id', 'workspaces', 'id')]
- **Indexes**: 6 matching index names
  - In Alembic only: ['ix_assets_created_at']
  - In Django only: ['assets_workspace_id_fa2ba290_like', 'assets_id_ccff16bb_like', 'assets_workspace_id_fa2ba290']

### Table `jobs`
- **Columns (23)**: 100% Match (applied, applied_at, attempt_count, cancelled, cancelled_at, created_at, error_message, heartbeat_at, id, kind, lease_expires_at, lease_token, operation_key, progress, project_id, result, revision, snapshot, stage, state, subprocess_pid, updated_at, workspace_id)
- **Types & Nullability**: 100% Match across all columns
- **Primary Key**: Match `['id']`
- **Foreign Keys (2)**: Match [('project_id', 'projects', 'id'), ('workspace_id', 'workspaces', 'id')]
- **Indexes**: 13 matching index names
  - In Alembic only: ['ix_jobs_operation_key']
  - In Django only: ['jobs_workspace_id_40f940da', 'jobs_project_id_cba441e6', 'jobs_operation_key_key', 'jobs_workspace_id_40f940da_like', 'jobs_id_5fa7abdb_like', 'jobs_operation_key_30279995_like', 'jobs_project_id_cba441e6_like']

### Table `job_stages`
- **Columns (10)**: 100% Match (ambiguous_outcome, attempt, billable_units, created_at, details, id, job_id, stage, state, updated_at)
- **Types & Nullability**: 100% Match across all columns
- **Primary Key**: Match `['id']`
- **Foreign Keys (1)**: Match [('job_id', 'jobs', 'id')]
- **Indexes**: 2 matching index names
  - In Django only: ['job_stages_job_id_7099d065', 'job_stages_job_id_7099d065_like']

### Table `application_receipts`
- **Columns (6)**: 100% Match (applied_at, applied_revision, conflicts, id, job_id, project_id)
- **Types & Nullability**: 100% Match across all columns
- **Primary Key**: Match `['id']`
- **Foreign Keys (2)**: Match [('job_id', 'jobs', 'id'), ('project_id', 'projects', 'id')]
- **Indexes**: 3 matching index names
  - In Django only: ['application_receipts_project_id_bf44d2fd', 'application_receipts_job_id_855601f8_like', 'application_receipts_project_id_bf44d2fd_like', 'application_receipts_job_id_855601f8']

### Table `outbox`
- **Columns (9)**: 100% Match (attempts, created_at, error, event_type, id, last_attempt_at, locked_until, payload, processed)
- **Types & Nullability**: 100% Match across all columns
- **Primary Key**: Match `['id']`
- **Foreign Keys (0)**: Match []
- **Indexes**: 4 matching index names

### Table `voice_takes`
- **Columns (16)**: 100% Match (accepted, asset_id, contract_key, created_at, duration, id, meta, model, preset, project_id, provider, roman_text, scene_id, spoken_text, timing_data, voice_id)
- **Types & Nullability**: 100% Match across all columns
- **Primary Key**: Match `['id']`
- **Foreign Keys (2)**: Match [('asset_id', 'assets', 'id'), ('project_id', 'projects', 'id')]
- **Indexes**: 7 matching index names
  - In Django only: ['voice_takes_asset_id_b47cb383_like', 'voice_takes_asset_id_b47cb383', 'voice_takes_project_id_43d5791f_like', 'voice_takes_project_id_43d5791f', 'voice_takes_id_e86fcfb8_like']

### Table `usage`
- **Columns (7)**: 100% Match (actual_billable_units, calls, day, estimated_billable_units, last_call_at, provider, uncertain_calls)
- **Types & Nullability**: 100% Match across all columns
- **Primary Key**: Match `['day', 'provider']`
- **Foreign Keys (0)**: Match []
- **Indexes**: 1 matching index names

## 3. Schema Equivalence Verdict

**VERDICT: PASS — MATERIALLY EQUIVALENT**
The fresh Django-created schema exactly replicates all 11 application tables, column types, nullabilities, primary keys, and foreign keys created historically by Alembic.