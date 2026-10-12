# Telugu Reel Studio — Django Migration Architecture & Execution Plan

**Document Version**: 1.0.0 (Phase A — Audit, Architecture Design & Migration Plan)  
**Status**: Planning / Not Implemented — Awaiting User Approval  
**Last Verified Baseline Commit**: `dbb89c3` ("save FastAPI project") on branch `main`  
**Current Date**: 2026-10-12  

---

## Executive Summary & Guiding Mandate

This document defines the comprehensive, zero-downtime, zero-data-loss migration architecture for transitioning the **Telugu Reel Studio / Video Automation** backend from **FastAPI** to **Django 5.x + Django REST Framework (DRF)**.

Per strict operating rules:
1. **This phase is AUDIT AND DESIGN ONLY.** No FastAPI code is removed, no database schema is altered, and no destructive migrations are executed.
2. **Every existing capability is preserved**:
   - Both production workflows (**Workflow A**: User media to 1080x1920 reel; **Workflow B**: AI topic/storyboard to 1080x1920 reel).
   - All 6 existing projects, 65 revisions, 25 media assets, and 46 historical jobs stored in PostgreSQL.
   - All uploaded video/audio files in `data/assets/`.
   - The complete Phase 4 naturalness pipeline: Spoken Telugu normalizer, restrained delivery presets, narration blocks, contract cache, and the interactive Telugu Voice Lab.
   - Celery 5.6.3 distributed processing (Redis broker, 1-slot render worker, 2-slot network worker, transactional outbox dispatcher).
   - Docker container infrastructure and volume persistence.
3. **Frontend Strategy**:
   - The current React 19 / TypeScript frontend (`frontend/app.tsx`) will be preserved and run against the Django DRF API first to verify 100% backend parity.
   - The frontend transition to **Django Templates + HTML/CSS + jQuery** is designed in detail in Section 19 as a **separate future phase** that will commence only after the Django backend passes all verification checks.

---

## 1. Current Architecture

```
+-----------------------------------------------------------------------------------------+
|                                    CLIENT TIER                                          |
|  Single-Page Application (React 19, TypeScript, esbuild bundle -> studio/static/app.js)  |
|  - Tab 01: Script & Storyboard                                                          |
|  - Tab 02: Telugu Voice Lab & Visuals                                                   |
|  - Tab 03: Continuous Timeline & Roman Captions                                         |
|  - Tab 04: Render Queue, Job Logs & Export Downloads                                    |
+-----------------------------------------------------------------------------------------+
                                           | HTTP JSON / Multipart (Port 8787)
                                           v
+-----------------------------------------------------------------------------------------+
|                                 WEB API TIER (FastAPI)                                  |
|  - Security Middleware: TrustedHost, local_guard (x-studio-token, Origin, Size Limits)   |
|  - Routers: Bootstrap, Health, Projects, Drafts, Assets, Stock, Jobs, Voice Lab         |
|  - Static Mount: StaticFiles serving /index.html, /app.js, /app.css                     |
+-----------------------------------------------------------------------------------------+
                               |                                          |
        Database Operations    |                                          | Outbox Enqueue
       (SQLAlchemy 2.0 ORM)    v                                          v
+-------------------------------------------+      +--------------------------------------+
|             POSTGRESQL 18                 |      |           TRANSACTIONAL OUTBOX       |
|  Container: studio-postgres (Port 5433)   |      |  Table: outbox (PostgreSQL)          |
|  Volume: video-automation_postgres_data   |      |  Status: processed=false             |
|  Tables: workspaces, projects, revisions, |      +--------------------------------------+
|          assets, jobs, job_stages, drafts,|                         |
|          application_receipts, usage,     |                         | Poll: SKIP LOCKED
|          voice_takes                      |                         v
+-------------------------------------------+      +--------------------------------------+
                                                   |       OUTBOX DISPATCHER PROCESS      |
                                                   |  Module: studio.dispatcher           |
                                                   |  Applies: task.apply_async(...)      |
                                                   +--------------------------------------+
                                                                      |
                                                                      | Enqueue Job IDs
                                                                      v
                                                   +--------------------------------------+
                                                   |            REDIS 7 BROKER            |
                                                   |  Container: studio-redis (Port 6379) |
                                                   |  Volume: video-automation_redis_data |
                                                   |  - DB 0: Celery Queue (render/net)   |
                                                   |  - DB 1: Celery Result Backend       |
                                                   |  - DB 2: Sliding-Window Rate Limiter |
                                                   +--------------------------------------+
                                                                      |
                                              +-----------------------+----------------------+
                                              |                                              |
                                              v                                              v
                      +----------------------------------+          +----------------------------------+
                      |       CELERY NETWORK WORKER      |          |       CELERY RENDER WORKER       |
                      |  Queue: network (Concurrency: 2) |          |  Queue: render (Concurrency: 1)  |
                      |  Tasks:                          |          |  Tasks:                          |
                      |  - task_research (Gemini API)    |          |  - task_render_scene (FFmpeg)    |
                      |  - task_draft (Gemini AI)        |          |  - task_assemble (FFmpeg concat) |
                      |  - task_stock (Pixabay search)   |          |  - task_mix (FFmpeg ducking)     |
                      |  - task_stock_all (Download)     |          |  - task_validate (FFprobe check) |
                      |  - task_voice_pipeline (TTS)     |          +----------------------------------+
                      +----------------------------------+                            |
                                      |                                               |
                                      +-----------------------+-----------------------+
                                                              |
                                                              v
+----------------------------------------------------------------------------------------------------+
|                                    SHARED MEDIA STORAGE (data/)                                     |
|  data/assets/   : Uploaded user MP4s, MP3s, and downloaded Pixabay clips (Deduplicated via SHA-256)|
|  data/cache/    : Deterministic TTS audio, word timing JSONs, and video thumbnails                 |
|  data/exports/  : Finished 1080x1920 reels (reel.mp4), SRT captions, reports, project snapshots    |
|  data/tmp/      : Atomic multi-process temp render fragments (*.part.mp4, *.part.mp3)              |
+----------------------------------------------------------------------------------------------------+
```

### Component Responsibility & Adaptation Inventory

| Component | Technology | Responsibility | Data Owned | Unchanged / Adapted | Migration Risk |
|---|---|---|---|---|---|
| **Web Server / Routing** | FastAPI / Starlette | HTTP endpoints, file streaming, token validation | None (Stateless) | **Replace** with Django + DRF | Medium |
| **Data Layer** | SQLAlchemy 2.0 ORM + Alembic | DB schema, transactions, optimistic locking | PostgreSQL tables | **Adapt** to Django ORM (Unmanaged first) | High (Data integrity critical) |
| **Media Engine** | FFmpeg 9.0.2 / FFprobe (`studio/media.py`) | Video crop, ASS burn-in, audio ducking, muxing | `data/tmp/`, `data/exports/` | **Unchanged** (Reusable Python module) | Low |
| **Voice Pipeline** | Edge, Sarvam, Azure (`studio/voice/`) | Telugu normalizer, delivery presets, Voice Lab | `data/cache/`, `voice_takes` | **Unchanged** (Reusable Python package) | Low |
| **External Providers** | Gemini, Pixabay (`studio/providers.py`) | Grounded AI research, stock video search | API Keys in `settings.json` | **Unchanged** (Reusable Python module) | Low |
| **Distributed Broker** | Redis 7 | Celery task queue & rate limiting | Transient task messages | **Unchanged** (Docker container preserved) | Low |
| **Worker Engine** | Celery 5.6.3 (`studio/tasks.py`) | Distributed video rendering & network downloads | Worker execution leases | **Adapted** (Load Django settings) | Low |
| **Outbox Dispatcher** | Polling loop (`studio/dispatcher.py`) | Bridges DB outbox events to Celery | `outbox` table | **Adapted** (Use Django ORM or raw SQL) | Low |
| **Frontend UI** | React 19 (`frontend/app.tsx`) | User interface and timeline controls | Local browser state | **Unchanged initially**, jQuery later | Medium (Future phase) |

---

## 2. Actual Implementation Status of Phases 1 to 4

A strict audit of the active repository confirms:

| Phase | Core Objective | Actual Status | Evidence in Active Repository |
|---|---|---|---|
| **Phase 1** | Reliability, media/scene assignment, independent rendering | **Complete & Verified** | `studio/media.py`, `studio/models.py`, `studio/store.py` (preflight, asset deduplication, draft recovery). 20 passing unit tests in `tests/test_workflow.py`. |
| **Phase 2** | PostgreSQL primary store, Alembic migrations | **Complete & Verified** | Alembic migration `0001_phase2_initial.py`. Live PostgreSQL database on port 5433 with 8 relational tables. Unicode Telugu fidelity and optimistic locking tested. |
| **Phase 3** | Redis, Celery workers, Transactional Outbox, Leases | **Complete & Verified** | Alembic migration `0002_phase3_leases_and_outbox.py`. Outbox dispatcher, Redis queues (`render`, `network`), Celery tasks (`studio/tasks.py`), execution leases, rate limiter. Real 17.42s 1080x1920 video verified. |
| **Phase 4** | Telugu Voice Lab, naturalness, normalizer, continuity | **Complete & Verified** | Alembic migration `0003_phase4_voice_lab.py`. Complete `studio/voice/` architecture (registry, normalizer, presets, edge/sarvam/azure adapters, blocks, cache, lab). 10 baseline samples in `test-outputs/voice-lab-eval/`. Voice Lab UI in tab 02. |

---

## 3. Complete API Inventory & Route Mapping

The following 35 endpoints are currently implemented in `studio/api.py`. The Django REST Framework implementation will map 1-to-1 to ensure zero client disruption:

| Method | URL | Purpose | Input | Output | DB Interaction | Celery Task | Difficulty |
|---|---|---|---|---|---|---|---|
| `GET` | `/api/bootstrap` | Initial client handshake & project list | None | `{token, settings, projects}` | `SELECT projects` | None | Low |
| `GET` | `/api/health` | Diagnostic check for ffmpeg/ffprobe | None | `{ffmpeg, ffprobe}` versions | None | None | Low |
| `POST` | `/api/settings` | Save user API keys and defaults | JSON dict | Saved settings dict | None (File `settings.json`) | None | Low |
| `POST` | `/api/projects` | Create a new project | Project JSON | Unpacked project dict | `INSERT projects`, `INSERT revisions` | None | Low |
| `PUT` | `/api/projects/{pid}` | Save project with optimistic lock | `{revision, project}` | Unpacked project dict | `UPDATE projects`, `INSERT revisions` | None | Medium |
| `GET` | `/api/projects/{pid}` | Retrieve project details | None | Unpacked project dict | `SELECT projects` | None | Low |
| `POST` | `/api/projects/{pid}/approve` | Approve project revision | `{revision}` | Unpacked project dict | `UPDATE projects.approved_revision` | None | Low |
| `GET` | `/api/projects/{pid}/history` | Retrieve revision history (max 50) | None | `[{revision, created, project}]` | `SELECT revisions` | None | Low |
| `POST` | `/api/projects/{pid}/jobs` | Enqueue background processing job | `JobBody` JSON | Job dict with `id` | `INSERT jobs`, `INSERT outbox` | Dispatched to Celery | Medium |
| `GET` | `/api/projects/{pid}/jobs` | List all jobs for a project | None | `[Job dict]` | `SELECT jobs`, `SELECT job_stages` | None | Low |
| `POST` | `/api/jobs/{jid}/retry` | Retry a failed job | `{mode: 'original'\|'current'}`| Job dict | `UPDATE jobs`, `INSERT outbox` | Dispatched to Celery | Medium |
| `POST` | `/api/jobs/{jid}/cancel` | Cancel running job & kill ffmpeg | None | `{cancelled: true}` | `UPDATE jobs.cancelled = true` | Subprocess kill | Medium |
| `POST` | `/api/projects/{pid}/jobs/{jid}/apply` | Apply completed job result to project | None (or `?force=true`) | Applied project dict | `UPDATE projects`, `INSERT application_receipts` | None | Medium |
| `GET` | `/api/projects/{pid}/preflight` | Check project readiness for render | None | `{ready, warnings, blockers}` | `SELECT projects`, `SELECT assets` | None | Low |
| `PUT` | `/api/projects/{pid}/draft` | Save client autosave draft | Draft body JSON | Saved draft dict | `UPSERT drafts` | None | Low |
| `GET` | `/api/projects/{pid}/draft` | Get client autosave draft | None | Draft body JSON | `SELECT drafts` | None | Low |
| `DELETE`| `/api/projects/{pid}/draft` | Discard client autosave draft | None | `{ok: true}` | `DELETE drafts` | None | Low |
| `POST` | `/api/projects/{pid}/assign-media` | Assign video/audio to scenes | `{asset_id, scene_ids}` | Saved project dict | `UPDATE projects`, `INSERT revisions` | None | Medium |
| `GET` | `/api/stock` | Query Pixabay stock video search | Query `?q=...` | `[Stock video items]` | None | None | Low |
| `GET` | `/api/assets` | List uploaded/downloaded assets | Query `?kind=video\|audio` | `[Asset dict]` | `SELECT assets` | None | Low |
| `POST` | `/api/assets` | Upload media file (max 200MB) | Multipart file | Asset dict | `INSERT assets` | None | Medium |
| `GET` | `/api/assets/{aid}` | Get asset metadata | None | Asset dict | `SELECT assets` | None | Low |
| `GET` | `/api/assets/{aid}/media` | Stream asset audio/video file | None | Binary stream (FileResponse)| None | None | Low |
| `GET` | `/api/assets/{aid}/thumbnail` | Get/generate video thumbnail JPEG | None | JPEG image (FileResponse) | `SELECT assets` | Subprocess ffmpeg | Low |
| `GET` | `/api/jobs/{jid}/audio/{scene_id}`| Stream synthesized scene audio | None | MP3/WAV (FileResponse) | `SELECT jobs` | None | Low |
| `GET` | `/api/exports/{jid}/{name}` | Download export artifacts | Name (reel.mp4, etc.) | Binary/Text FileResponse | `SELECT jobs` | None | Low |
| `GET` | `/api/voice/capabilities` | List verified TTS capabilities | None | `[Capability dict]` | None | None | Low |
| `POST` | `/api/voice/normalize` | Normalize text into spoken Telugu | `{text, dictionary}` | `{source, spoken, roman}` | None | None | Low |
| `POST` | `/api/voice/preview` | Generate Voice Lab preview take | `PreviewBody` JSON | Take dict | `INSERT voice_takes` | Direct / Async | Medium |
| `GET` | `/api/projects/{pid}/takes` | List versioned voice takes | Query `?scene_id=...` | `[Take dict]` | `SELECT voice_takes` | None | Low |
| `POST` | `/api/projects/{pid}/takes/{take_id}/accept`| Accept candidate voice take | None | `{take, project}` | `UPDATE voice_takes`, `UPDATE projects` | None | Medium |
| `POST` | `/api/voice/ab_compare` | Compare Baseline vs Candidate takes | `{take_a, take_b}` | Comparison metrics JSON | None | None | Low |
| `POST` | `/api/voice/rewrite_preview` | Preview colloquial Telugu rewrite | `{text}` | `{changed, proposed, diff}` | None | None | Low |
| `POST` | `/api/projects/{pid}/voice_lock` | Toggle project voice profile lock | `{locked: boolean}` | Saved project dict | `UPDATE projects` | None | Low |
| `POST` | `/api/projects/{pid}/pronunciation_dict` | Update custom dictionary overrides| `{dictionary: {...}}` | Saved project dict | `UPDATE projects` | None | Low |

---

## 4. Database Ownership & Schema Analysis

### Current Schema in PostgreSQL (`studio` DB)

```sql
-- Workspaces
CREATE TABLE workspaces (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) DEFAULT 'Local Workspace',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Projects (body holds the full project document JSONB)
CREATE TABLE projects (
    id VARCHAR(64) PRIMARY KEY,
    workspace_id VARCHAR(64) REFERENCES workspaces(id),
    revision INTEGER DEFAULT 1 NOT NULL,
    approved_revision INTEGER,
    body JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Immutable Revision Snapshots
CREATE TABLE revisions (
    id SERIAL PRIMARY KEY,
    project_id VARCHAR(64) REFERENCES projects(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    body JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_project_revision UNIQUE (project_id, revision)
);

-- Media Assets
CREATE TABLE assets (
    id VARCHAR(64) PRIMARY KEY,
    workspace_id VARCHAR(64) REFERENCES workspaces(id),
    filename VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    kind VARCHAR(32) NOT NULL, -- 'video' or 'audio'
    duration DOUBLE PRECISION DEFAULT 0.0,
    sha256 VARCHAR(64),
    provider VARCHAR(64) DEFAULT 'upload',
    external_id VARCHAR(128),
    source_url TEXT DEFAULT '',
    license_url TEXT DEFAULT '',
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Distributed Jobs
CREATE TABLE jobs (
    id VARCHAR(64) PRIMARY KEY,
    workspace_id VARCHAR(64) REFERENCES workspaces(id),
    project_id VARCHAR(64) REFERENCES projects(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    kind VARCHAR(32) NOT NULL,
    state VARCHAR(32) DEFAULT 'queued' NOT NULL,
    stage VARCHAR(64) DEFAULT 'queued' NOT NULL,
    progress TEXT DEFAULT '',
    snapshot JSONB NOT NULL,
    result JSONB DEFAULT '{}'::jsonb,
    applied BOOLEAN DEFAULT FALSE,
    applied_at TIMESTAMPTZ,
    error_message TEXT,
    attempt_count INTEGER DEFAULT 0,
    operation_key VARCHAR(128) UNIQUE,
    lease_token VARCHAR(64),
    lease_expires_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    cancelled BOOLEAN DEFAULT FALSE,
    cancelled_at TIMESTAMPTZ,
    subprocess_pid INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Granular Job Stages
CREATE TABLE job_stages (
    id SERIAL PRIMARY KEY,
    job_id VARCHAR(64) REFERENCES jobs(id) ON DELETE CASCADE,
    attempt INTEGER DEFAULT 1,
    stage VARCHAR(64) NOT NULL,
    state VARCHAR(32) NOT NULL,
    details JSONB DEFAULT '{}'::jsonb,
    billable_units INTEGER DEFAULT 0,
    ambiguous_outcome BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Transactional Outbox
CREATE TABLE outbox (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    processed BOOLEAN DEFAULT FALSE,
    attempts INTEGER DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    error TEXT,
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Application Receipts (Optimistic Merge History)
CREATE TABLE application_receipts (
    id SERIAL PRIMARY KEY,
    job_id VARCHAR(64) REFERENCES jobs(id) ON DELETE CASCADE,
    project_id VARCHAR(64) REFERENCES projects(id) ON DELETE CASCADE,
    applied_revision INTEGER NOT NULL,
    conflicts JSONB DEFAULT '[]'::jsonb,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);

-- Usage & Rate Records
CREATE TABLE usage (
    day VARCHAR(32) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    calls INTEGER DEFAULT 0,
    estimated_billable_units INTEGER DEFAULT 0,
    actual_billable_units INTEGER DEFAULT 0,
    uncertain_calls INTEGER DEFAULT 0,
    last_call_at TIMESTAMPTZ,
    PRIMARY KEY (day, provider)
);

-- Voice Lab Versioned Takes (Phase 4)
CREATE TABLE voice_takes (
    id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(64) REFERENCES projects(id) ON DELETE CASCADE,
    scene_id VARCHAR(64),
    provider VARCHAR(32) NOT NULL,
    voice_id VARCHAR(64) NOT NULL,
    model VARCHAR(64),
    preset VARCHAR(32) DEFAULT 'conversational',
    spoken_text TEXT NOT NULL,
    roman_text TEXT DEFAULT '',
    asset_id VARCHAR(64) REFERENCES assets(id) ON DELETE SET NULL,
    duration DOUBLE PRECISION DEFAULT 0.0,
    timing_data JSONB DEFAULT '[]'::jsonb,
    accepted BOOLEAN DEFAULT FALSE,
    contract_key VARCHAR(64),
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Transition Strategy: Safe Ownership Handover (Option C)

To guarantee that zero production data is altered or lost, the database migration follows a **Two-Stage Transition**:

1. **Stage 1 — Unmanaged Coexistence (Stages M1 to M8)**:
   - Define Django models with `Meta: managed = False` and explicit `db_table = '...'`.
   - Django queries the exact existing PostgreSQL tables without running `manage.py migrate` on them.
   - Alembic remains the sole authority over the live database schema.
   - Both FastAPI and Django can safely connect to the database in parallel.

2. **Stage 2 — Verified Schema-Ownership Transfer Protocol (Stage M9 Cutover)**:
   > [!IMPORTANT]
   > Do NOT blindly change all unmanaged models to `managed = True` and run `python manage.py migrate --fake-initial` against the live existing database.
   > The eventual schema-ownership transfer must strictly adhere to the following 8-step protocol:
   > 1. **Exact Django Model Mapping Verification**: Audit all field types, nullability, defaults, primary keys, foreign key constraints, and JSONB handling against active PostgreSQL catalog.
   > 2. **Schema Comparison against PostgreSQL**: Run schema diffing against the active database to ensure zero drift.
   > 3. **Freeze Alembic Schema Ownership**: Freeze Alembic migrations in source control for historical/reference purposes (never delete Alembic migration history).
   > 4. **Django Baseline Migration**: Author a deterministic Django baseline migration representing the verified existing schema.
   > 5. **Disposable Dual-Testing**: Test that baseline migration against:
   >    - a clean disposable PostgreSQL database, and
   >    - a restored copy of the existing database.
   > 6. **SQL Review**: Inspect the raw SQL generated by `python manage.py sqlmigrate` to confirm equivalence with Alembic DDL.
   > 7. **Explicit Verified Marking**: Only after equivalence is demonstrated, explicitly mark the verified baseline migration as applied on the existing database.
   > 8. **Sole Authority Checkpoint**: From that checkpoint onward, Django migrations become the sole schema authority.

---

## 5. Target Django Architecture

### Directory & App Structure

```
Video-Automation/
|-- manage.py
|-- config/                          # Django Core Project Settings
|   |-- __init__.py
|   |-- settings.py                  # Database, Redis, Celery, DRF, Security
|   |-- urls.py                      # Root URL routing (/api/..., /admin/..., /)
|   |-- wsgi.py
|   `-- asgi.py
|-- studio/                          # Existing Studio Core (Preserved)
|   |-- media.py                     # Unchanged FFmpeg media processing
|   |-- providers.py                 # Unchanged Gemini & Pixabay integrations
|   |-- ratelimit.py                 # Unchanged Redis sliding-window limiter
|   |-- voice/                       # Unchanged Phase 4 Voice Lab package
|   |-- config.py                    # Path constants (DATA, ROOT, executable)
|   |-- tasks.py                     # Unchanged Celery worker tasks
|   `-- dispatcher.py                # Transactional outbox polling loop
|-- apps/
|   |-- core/                        # Django Core (Health, bootstrap, settings)
|   |   |-- views.py
|   |   `-- urls.py
|   |-- projects/                    # Projects, Revisions, Drafts
|   |   |-- models.py                # Django models for projects, revisions, drafts
|   |   |-- serializers.py           # DRF serializers
|   |   |-- views.py                 # DRF ViewSets / APIViews
|   |   `-- admin.py                 # Django Admin customization
|   |-- media_assets/                # Assets, uploads, deduplication
|   |   |-- models.py                # Asset model
|   |   |-- serializers.py
|   |   `-- views.py
|   |-- jobs/                        # Jobs, JobStages, Outbox, Leases
|   |   |-- models.py                # Job, JobStage, Outbox, Receipt models
|   |   |-- serializers.py
|   |   `-- views.py
|   `-- voice_lab/                   # Phase 4 Voice Lab integration
|       |-- models.py                # VoiceTake model
|       |-- serializers.py
|       `-- views.py
|-- data/                            # Preserved persistent storage
`-- frontend/                        # Preserved React frontend
```

### Separation of Concerns
- **Views**: Handle HTTP serialization, parameter validation, and status codes.
- **Service Layer**: Business logic lives in reusable modules (`studio/store.py`, `studio/media.py`, `studio/voice/`). Views never execute FFmpeg or synthesize voice directly.
- **Django Admin**: Provides operational dashboards for inspecting `Projects`, `Revisions`, `Jobs`, `JobStages`, `Assets`, and `VoiceTakes` with search and JSON tree inspection.

---

## 6. Celery + Django Integration Strategy

### Task & Queue Preservation
- The Celery application will be configured in `config/celery.py` adhering to Django conventions:
  ```python
  import os
  from celery import Celery

  os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  app = Celery('studio')
  app.config_from_object('django.conf:settings', namespace='CELERY')
  app.autodiscover_tasks()
  ```
- **Task Signatures**: Tasks in `studio/tasks.py` (`task_render_pipeline`, `task_voice_pipeline`, etc.) accept a single argument: `job_id: str`. They do not serialize complex models or ORM objects across Redis.
- **Worker Queues**:
  - `render` queue: Concurrency 1 slot (`-c 1`) for FFmpeg video encoding.
  - `network` queue: Concurrency 2 slots (`-c 2`) for TTS, AI, and Pixabay HTTP calls.
  - `default` queue: Auxiliary maintenance.
- **Transactional Outbox Dispatcher**:
  - `studio/dispatcher.py` continues to poll the `outbox` table using `SELECT ... FOR UPDATE SKIP LOCKED`.
  - Enqueueing in Django is performed atomically inside `transaction.atomic()` ensuring jobs are only dispatched if database commits succeed.

---

## 7. Docker Architecture & Side-by-Side Coexistence

### Target Compose Topology (`docker-compose.yml`)

```yaml
services:
  postgres:
    image: postgres:18-alpine
    container_name: studio-postgres
    restart: unless-stopped
    ports:
      - "127.0.0.1:5433:5432"
    volumes:
      - video-automation_postgres_data:/var/lib/postgresql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d studio"]

  redis:
    image: redis:7-alpine
    container_name: studio-redis
    restart: unless-stopped
    ports:
      - "127.0.0.1:6379:6379"
    volumes:
      - video-automation_redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  django-web:
    build: .
    container_name: studio-django
    command: ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8787", "--workers", "3"]
    environment:
      - DJANGO_SETTINGS_MODULE=config.settings
      - STUDIO_DATABASE_URL=postgresql://postgres:postgres@postgres:5432/studio
      - CELERY_BROKER_URL=redis://redis:6379/0
      - CELERY_RESULT_BACKEND=redis://redis:6379/1
      - STUDIO_CACHE_REDIS_URL=redis://redis:6379/2
    volumes:
      - ./data:/app/data
    ports:
      - "127.0.0.1:8787:8787"
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }

  dispatcher:
    build: .
    container_name: studio-dispatcher
    command: ["python", "-m", "studio.dispatcher"]
    volumes:
      - ./data:/app/data

  worker_network:
    build: .
    container_name: studio-worker-network
    command: ["celery", "-A", "config.celery", "worker", "-Q", "network", "-c", "2"]
    volumes:
      - ./data:/app/data

  worker_render:
    build: .
    container_name: studio-worker-render
    command: ["celery", "-A", "config.celery", "worker", "-Q", "render", "-c", "1"]
    volumes:
      - ./data:/app/data

volumes:
  video-automation_postgres_data:
    external: true
  video-automation_redis_data:
    external: true
```

### Side-by-Side Coexistence During Testing
During development:
- FastAPI runs on `127.0.0.1:8787` (Primary).
- Django runs on `127.0.0.1:8788` (Test port).
- Both connect to `studio-postgres` (Port 5433) and `studio-redis` (Port 6379).
- Once Django verification passes 100%, swap the binding: Django takes port `8787`.

---

## 8. Security & Authentication Strategy

1. **Local Access Guard**:
   - FastAPI used a custom middleware checking `x-studio-token` and `Origin`.
   - Django DRF will implement custom middleware / authentication classes enforcing the identical security contract:
     - Header check: `request.headers.get('x-studio-token') == TOKEN`.
     - Origin check: `request.META.get('HTTP_ORIGIN') == BASE_URL`.
     - Request size limits: 220 MB for `/api/assets`, 1 MB for all other endpoints.
     - Security headers: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cache-Control: no-store` on `/api/` paths.
2. **CSRF & Token Compatibility**:
   - `/api/bootstrap` will continue to return `token` and `settings`, allowing the existing React frontend (and future jQuery frontend) to authenticate without modifying the client handshake protocol.

---

## 9. Data & Media Protection Protocol

1. **Atomic Pre-Migration Checkpoint**:
   - Git tag: `pre-django-migration`.
   - Full PostgreSQL logical backup via `pg_dump`:
     ```powershell
     docker exec -t studio-postgres pg_dump -U postgres studio > data/studio_pre_django.sql
     ```
2. **Read-Only Media Isolation**:
   - `data/assets/`, `data/cache/`, `data/exports/` are treated as immutable storage during migration.
   - Any test synthesis or test renders write to isolated directories (`test-outputs/` or temporary pytest directories).

---

## 10. Detailed Staged Migration Roadmap

```
+----+       +----+       +----+       +----+       +----+       +----+       +----+       +----+       +----+
| M1 | ----> | M2 | ----> | M3 | ----> | M4 | ----> | M5 | ----> | M6 | ----> | M7 | ----> | M8 | ----> | M9 |
+----+       +----+       +----+       +----+       +----+       +----+       +----+       +----+       +----+
Skeleton   DB Unmanaged  DRF Core   Assets/Media    Jobs &       Voice Lab    Frontend      Full Reel   Cutover &
& Health    Models       & Projects  Endpoints     Celery Task    Endpoints    Parity       Render      Decommission
(Port 8788) (Read-Only)  APIs        & Uploads     Pipeline      (Phase 4)    Verification Benchmark   FastAPI
```

### Stage M1: Django Skeleton & Health Endpoints
- **Scope**: Initialize `config/` project skeleton, install `django` and `djangorestframework`, implement `/api/health` and `/api/bootstrap` on port 8788.
- **Rollback Point**: Remove `config/` directory; FastAPI on 8787 is untouched.

### Stage M2: Connect Django to PostgreSQL with Unmanaged Models
- **Scope**: Configure `DATABASES` in `config/settings.py` targeting `studio` database on port 5433. Create `managed = False` models. Verify ORM read operations across existing 6 projects, 65 revisions, and 25 assets.
- **Rollback Point**: No schema altered. Remove model definitions.

### Stage M3: Project, Revision & Draft DRF Endpoints
- **Scope**: Port `/api/projects`, `/api/projects/{pid}`, `/api/projects/{pid}/approve`, `/api/projects/{pid}/history`, and draft endpoints. Maintain optimistic locking (`revision`).
- **Tests**: Pytest contract tests comparing FastAPI vs Django responses on identical project payloads.

### Stage M4: Asset, Media Streaming & Upload Endpoints
- **Scope**: Port `/api/assets`, `/api/assets/{aid}`, `/api/assets/{aid}/media`, `/api/assets/{aid}/thumbnail`. Implement chunked upload and SHA-256 deduplication.
- **Tests**: Upload test video, verify deduplication against existing asset hashes.

### Stage M5: Job Enqueuing, Outbox & Celery Pipeline
- **Scope**: Port `/api/projects/{pid}/jobs`, `/api/jobs/{jid}/retry`, `/api/jobs/{jid}/cancel`, `/api/projects/{pid}/jobs/{jid}/apply`. Integrate with Celery app and verify outbox record insertion.
- **Tests**: Enqueue research, stock, and render test jobs; verify execution lease acquisition and terminal state transition.

### Stage M6: Voice Lab & Phase 4 Naturalness Endpoints
- **Scope**: Port `/api/voice/capabilities`, `/api/voice/normalize`, `/api/voice/preview`, `/api/projects/{pid}/takes`, `/api/projects/{pid}/takes/{take_id}/accept`, `/api/voice/ab_compare`, `/api/voice/rewrite_preview`.
- **Tests**: Run Phase 4 test suite (`tests/test_phase4.py`) against Django endpoints.

### Stage M7: React Frontend Parity Verification
- **Scope**: Point React frontend to Django API port. Verify all 4 tabs, settings modal, Voice Lab A/B playback, script editor, and media upload.
- **Tests**: Run `node tests/ui_smoke.cjs` against Django backend.

### Stage M8: End-to-End Telugu Reel Render Benchmark
- **Scope**: Render a complete 1080x1920 video reel through Django + Celery stack.
- **Verification**: FFprobe stream validation, duration check, ASS subtitle burn-in, BGM ducking check, 0 FFmpeg decoding errors.

### Stage M9: Production Cutover & Alembic Retirement
- **Scope**: Execute `python manage.py migrate --fake-initial`. Switch Django to port 8787. Disengage FastAPI.
- **Rollback Point**: If issues arise, switch port 8787 back to FastAPI container.

---

## 11. Test & Quality Verification Plan

1. **Pytest Parity Suite**: Port tests from `tests/test_workflow.py`, `tests/test_phase3.py`, and `tests/test_phase4.py` using Django's `APIClient`.
   - Target: **50 of 50 tests passing** with 0 regressions.
2. **UI Smoke Test**:
   - Target: `node tests/ui_smoke.cjs` passing with 0 runtime errors.
3. **FFmpeg Video Verification**:
   - Render a vertical 1080x1920 MP4 through Django Celery worker and verify with null-muxer:
     `ffmpeg -v error -xerror -i reel.mp4 -f null -` (0 errors).

---

## 12. Future Frontend Architecture Plan: Django Templates + jQuery

> [!IMPORTANT]
> This frontend transition will be implemented as a **separate future phase** only after the Django backend is 100% verified.

### Screen-to-Template Mapping

| React Screen / Modal | Django Template Target | Dynamic Client Behavior (jQuery) |
|---|---|---|
| **Header & Project Bar** | `templates/partials/_header.html` | Project selector dropdown, New Project modal toggle, Save status indicator |
| **Tab 01: Script & Storyboard** | `templates/tabs/tab_script.html` | Scene list drag-and-drop ordering, scene text input binding, AI storyboard polling |
| **Tab 02: Voice Lab & Visuals** | `templates/tabs/tab_voice.html` | Live provider badges, rate/pitch sliders, live normalization test, A/B audio player toggle, take history table |
| **Tab 03: Timeline & Captions** | `templates/tabs/tab_timeline.html`| Interactive audio waveform / timeline scrubber, caption cue editor, BGM ducking preview |
| **Tab 04: Render & Export** | `templates/tabs/tab_export.html` | Render quality radio toggle, progress bar polling via AJAX (`/api/projects/{pid}/jobs`), download triggers |
| **Settings Modal** | `templates/modals/_settings.html` | Password input masks, AJAX save (`POST /api/settings`) |

### jQuery Architectural Principles
- **No Full Page Reloads on Studio Actions**: Studio workflows rely on rapid iterative editing. Modals, scene updates, voice takes, and render status will use clean jQuery AJAX (`$.ajax` / `$.getJSON`) calling the DRF REST APIs.
- **Server-Rendered Shell**: The main HTML structure, navigation, and modal skeletons will be rendered by Django templates, eliminating the complex React bundle, node_modules, and client hydration lag.
- **Style Continuity**: Preserve `frontend/style.css` glassmorphic tokens, CSS variables, and layout classes.

---

## 13. Audit Conclusion & Recommendations

### Recommended Migration Approach
**Option C: Staged Coexistence with Unmanaged Models**.
- Keep existing PostgreSQL schema intact under Alembic during initial development.
- Build Django DRF endpoints on port 8788.
- Verify 100% test parity and real video rendering.
- Fake-initial Django migrations and cut over port 8787.

### Subsystem Complexity Estimates

| Subsystem | Estimated Complexity | Primary Technical Challenge |
|---|---|---|
| **Database & ORM Models** | **Medium** | Ensuring Django unmanaged models map 1-to-1 to existing PostgreSQL column types and JSONB structures. |
| **Project & Revision APIs** | **Medium** | Replicating optimistic locking (`revision`) and revision snapshot immutability. |
| **Asset & Media Streaming** | **Low** | Streaming MP4/MP3 bytes and generating 270x480 thumbnails via FFmpeg. |
| **Celery & Outbox Dispatcher** | **Low** | Configuring Django Celery app while reusing existing `tasks.py` and `dispatcher.py`. |
| **Phase 4 Voice Lab Integration** | **Low** | Reusing `studio/voice/` directly as an internal library. |
| **Security & Local Guard** | **Low** | Implementing custom DRF middleware for `x-studio-token` and Origin checks. |
| **Future jQuery Frontend** | **Medium** | Porting the timeline scrubber and A/B audio player from React components to jQuery DOM manipulation. |

### Exact First Implementation Step (When Approved)
**Stage M1**: Create `config/` Django project skeleton with `settings.py` and `urls.py`, configure DRF, and implement the `/api/health` and `/api/bootstrap` endpoints on test port `8788`.

### Actions Requiring Manual User Approval
1. Confirmation of the migration architecture described in this plan.
2. Formal approval to create the pre-migration git checkpoint (`pre-django-migration`) and start **Stage M1**.

---

## 14. DJANGO MIGRATION — M1 EXECUTION REPORT

**Execution Timestamp**: 2026-10-12  
**Status**: Stage M1 Complete — Verified Coexistence & Read-Only Health Endpoint  

### 1. Git Baseline Checkpoint & Tag
- Baseline Commit: `7f2d37c50ff9429f0970aacf8f4aa068013f5101`
- Git Tag: `pre-django-migration`
- Branch: `main`

### 2. PostgreSQL Logical Backup & Integrity Verification
- **Source Database**: `studio` on port `5433` (PostgreSQL 18.6)
- **Backup File**: `backups/studio_pre_django_20261012T145428Z.dump` (custom `-Fc` format)
- **Backup Size**: 93,408 bytes
- **SHA-256 Checksum**: `f835bc5d1144dbb7b6e6984b39f2b15477fdb4f55496eaf02f1c9a97c6673a8e`
- **Restore Verification**:
  - Restored into temporary disposable database `studio_verify_backup`.
  - All 11 table row counts and Alembic version matched 100%:
    - `workspaces`: Source=1, Restored=1 (MATCH)
    - `projects`: Source=6, Restored=6 (MATCH)
    - `revisions`: Source=65, Restored=65 (MATCH)
    - `assets`: Source=25, Restored=25 (MATCH)
    - `jobs`: Source=46, Restored=46 (MATCH)
    - `job_stages`: Source=122, Restored=122 (MATCH)
    - `drafts`: Source=0, Restored=0 (MATCH)
    - `application_receipts`: Source=27, Restored=27 (MATCH)
    - `outbox`: Source=46, Restored=46 (MATCH)
    - `usage`: Source=5, Restored=5 (MATCH)
    - `voice_takes`: Source=0, Restored=0 (MATCH)
    - `alembic_version`: Source=0003_phase4_voice_lab, Restored=0003_phase4_voice_lab (MATCH)
  - Disposable database `studio_verify_backup` was safely dropped after validation.

### 3. Installed Packages
- `django==5.1.15`
- `djangorestframework==3.15.2`
- `asgiref==3.12.1`
- `sqlparse==0.6.0`

### 4. Running Servers & Ports
- **FastAPI**: `http://127.0.0.1:8787` (Active primary backend, status 200)
- **Django**: `http://127.0.0.1:8788` (Independent side-by-side test backend, status 200)

### 5. Health API Verification (`GET http://127.0.0.1:8788/api/health`)
- Response:
  ```json
  {
    "status": "ok",
    "service": "django",
    "version": "5.1.15",
    "database": {
      "engine": "postgresql",
      "connected": true,
      "current_alembic_revision": "0003_phase4_voice_lab",
      "tables_visible": true,
      "table_count": 12,
      "read_query_verified": true
    },
    "redis": {
      "connected": true
    },
    "media_tools": {
      "ffmpeg": "ffmpeg version 9.0.2-full_build...",
      "ffprobe": "ffprobe version 9.0.2-full_build..."
    }
  }
  ```

### 6. Schema Ownership & Mutation Protection
- Alembic revision before M1: `0003_phase4_voice_lab`
- Alembic revision after M1: `0003_phase4_voice_lab` (Identical)
- `django_migrations` table present in PostgreSQL: **No** (Zero internal migration tables created)
- Real database state mutated: **No** (Health check is strictly read-only)

### 7. Test Results
- Full automated test suite: **55 passed, 2 skipped, 0 failed** in 38.37s.
  - Existing workflow tests (20): PASSED
  - Existing Phase 3 Celery/Redis tests (9): PASSED
  - Existing Phase 4 Voice Lab tests (21 passed, 2 skipped): PASSED
  - Focused Django M1 tests (5): PASSED
- Frontend UI smoke test: **0 runtime errors** (`{"reactRuntimeErrors":[],"checks":[...]}`).
- Baseline 17.42s 1080x1920 video (`test-outputs/phase-3-home/phase3_home_reel.mp4`): Untouched and intact.

### 8. Stage M2 Execution Report & Model Coverage Table

**Status**: **PASSED**  
**Execution Date**: 2026-10-12  
**Database Role**: `django_readonly` (Dedicated role with `SELECT` grant only; negative tests prove `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `ALTER`, `DROP`, `CREATE` are rejected by PostgreSQL).

#### Model Mapping Matrix

| Existing PostgreSQL Table | Django Model (`core.models`) | Primary Key (PK) | Important Relationships | JSONB Fields | `managed` Status | Mapping Verified? | Known Limitations / Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `workspaces` | `Workspace` | `id` (VARCHAR 64) | None | None | `False` | **Yes** | Root workspace boundary. |
| `projects` | `Project` | `id` (VARCHAR 64) | `workspace -> Workspace` (DO_NOTHING) | `body` | `False` | **Yes** | `body` holds full project JSON document with scenes. |
| `revisions` | `Revision` | `id` (SERIAL / AutoField) | `project -> Project` (DO_NOTHING) | `body` | `False` | **Yes** | Unique together `(project, revision)`. Ascending order verified. |
| `assets` | `Asset` | `id` (VARCHAR 64) | `workspace -> Workspace` (DO_NOTHING) | `meta` | `False` | **Yes** | Media asset metadata with duration, hash, source. |
| `jobs` | `Job` | `id` (VARCHAR 64) | `workspace -> Workspace`, `project -> Project` (DO_NOTHING) | `snapshot`, `result` | `False` | **Yes** | Execution lease, heartbeat, and state tracking preserved. |
| `job_stages` | `JobStage` | `id` (SERIAL / AutoField) | `job -> Job` (DO_NOTHING) | `details` | `False` | **Yes** | Granular stage progression records. |
| `drafts` | `Draft` | `project_id` (VARCHAR 64) | `project -> Project` (OneToOne, DO_NOTHING) | `body` | `False` | **Yes** | **No synthetic `id`**: PK explicitly mapped to `project_id`. |
| `application_receipts` | `ApplicationReceipt` | `id` (SERIAL / AutoField) | `job -> Job`, `project -> Project` (DO_NOTHING) | `conflicts` | `False` | **Yes** | Optimistic merge receipt history. |
| `outbox` | `OutboxEvent` | `id` (SERIAL / AutoField) | None | `payload` | `False` | **Yes** | Transactional outbox event records. |
| `voice_takes` | `VoiceTake` | `id` (VARCHAR 64) | `project -> Project`, `asset -> Asset` (DO_NOTHING) | `timing_data`, `meta` | `False` | **Yes** | Phase 4 Voice Lab versioned speech synthesis takes. |

#### Tables Intentionally Not Mapped as Django ORM Models

1. **`usage`**:
   - **PostgreSQL Schema**: `PRIMARY KEY (day, provider)` — Composite primary key.
   - **Reason**: Django 5.1 does not natively support composite primary keys in `models.Model`. Inventing a fake surrogate key would risk corrupted ORM caching.
   - **Strategy**: Represented through read-only functions in `core/repository.py` (`list_usage()`, `get_usage(day, provider)`) using safe direct connection queries.
2. **`alembic_version`**:
   - **PostgreSQL Schema**: `PRIMARY KEY (version_num)`.
   - **Reason**: Alembic internal migration catalog table. Owned exclusively by Alembic. Not a domain entity.

#### Data Integrity & Unicode Verification
- **Telugu Unicode Fidelity**: Verified exact character-for-character match (SHA-256: `5b934e4d9b523c7f648b0b12c0f87a595d21391740966bef01988bcb71ff7d64`) between PostgreSQL direct query and Django ORM for Telugu script and Roman transliteration in `Project.body`.
- **JSONB Round-Trip**: All snapshots, results, and bodies deserialize as Python dicts/lists without modification.
- **Repository Layer**: Read-only repository functions (`list_projects`, `get_project`, `get_revision_history`, `list_assets`, `get_job`, `get_job_stages`, `list_usage`) verified operational.
- **Diagnostic Endpoint**: `GET /api/migration/schema-status` verified returning HTTP 200 with model counts and Alembic version without exposing credentials or private payloads.

#### Test Results Summary
- **Pytest Suite**: **68 passed, 2 skipped, 0 failed** in 60.75s.
  - Workflow regression tests (20): PASSED
  - Phase 3 Celery/Redis tests (9): PASSED
  - Phase 4 Voice Lab tests (21 passed, 2 skipped): PASSED
  - Django M1 tests (5): PASSED
  - Django M2 tests (13): PASSED
- **UI Smoke Test**: PASSED with **0 runtime errors**.
- **Alembic Revision**: `0003_phase4_voice_lab` (Unchanged).
- **Django Internal Tables**: Exactly **0** created.
- **Baseline Video**: `test-outputs/phase-3-home/phase3_home_reel.mp4` (7,481,435 bytes) untouched.

### 9. Stage M3 Execution Report & API Contract Parity Matrix

**Status**: **PASSED**  
**Execution Date**: 2026-10-12  
**Django Server**: `http://127.0.0.1:8788`  
**FastAPI Server**: `http://127.0.0.1:8787` (Active production backend)  
**Database Identity**: `django_readonly` (Strict SELECT only; 0 writes)  

#### Endpoint Parity Matrix

| FastAPI Route | Django DRF Route | HTTP Method | Parity Status | Known Differences | Tests Covering Endpoint | Deferred Behavior / Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `/api/bootstrap` | `/api/bootstrap` | `GET` | **100% PARITY** | Token is unique session secret | `test_bootstrap_endpoint` | None. Returns token, public settings, projects. |
| (Embedded in bootstrap) | `/api/projects` | `GET` | **100% PARITY** | DRF standalone collection | `test_django_project_list_endpoint` | Workspace-scoped project collection list. |
| `/api/projects/{pid}` | `/api/projects/{pid}` | `GET` | **100% PARITY** | None (100% JSON equality) | `test_django_project_detail_endpoint`, `test_project_detail_parity_with_fastapi`, `test_telugu_unicode_http_parity` | Returns `{id, revision, approved, updated, project}`. |
| `/api/projects/{pid}/history` | `/api/projects/{pid}/history` | `GET` | **100% PARITY** | None | `test_project_history_endpoint` | Returns list of revisions ordered by revision DESC. |
| `/api/projects/{pid}/draft` | `/api/projects/{pid}/draft` | `GET` | **100% PARITY** | None | `test_project_draft_endpoint` | Returns draft body if present, or `{}` if none. |
| `/api/projects/{pid}/preflight` | `/api/projects/{pid}/preflight` | `GET` | **100% PARITY** | None | `test_project_preflight_parity` | Pure validation calculation (`ready`, `errors`, `warnings`). |
| `/api/projects/{pid}/jobs` | `/api/projects/{pid}/jobs` | `GET` | **100% PARITY** | None | `test_project_jobs_endpoint` | Returns jobs list for project ordered newest first. |
| `/api/projects/{pid}/takes` | `/api/projects/{pid}/takes` | `GET` | **100% PARITY** | None | `test_project_takes_endpoint` | Returns Voice Lab takes list with optional scene filter. |
| `/api/voice/capabilities` | `/api/voice/capabilities` | `GET` | **100% PARITY** | None | `test_voice_capabilities_parity` | Returns verified TTS capabilities from registry. |
| `/api/assets` | `/api/assets` | `GET` | **100% PARITY** | None | `test_assets_list_and_detail` | Returns list of media asset metadata records. |
| `/api/assets/{aid}` | `/api/assets/{aid}` | `GET` | **100% PARITY** | None | `test_assets_list_and_detail` | Returns single asset metadata record. |
| Nonexistent project | `/api/projects/{nonexistent}` | `GET` | **100% PARITY** | None (HTTP 400 `{'detail': 'Project not found'}`) | `test_error_parity_nonexistent_project` | Exact status and detail match. |

#### Deferred Endpoints (Non-Pure Reads / Action Endpoints)
1. `/api/assets/{aid}/thumbnail`: Generates thumbnail on disk using FFmpeg subprocess if uncached. **Deferred to Stage M4**.
2. `/api/assets/{aid}/media`, `/api/assets/{aid}/file`: Binary media file streaming. **Deferred to Stage M4**.
3. `/api/jobs/{jid}/audio/{scene_id}`: Synthesized audio file streaming. **Deferred to Stage M4/M5**.
4. `/api/exports/{jid}/{name}`: Reel and captions download binary streaming. **Deferred to Stage M4/M5**.
5. `/api/projects/{pid}/jobs/{jid}/apply`, `/api/projects/{pid}/apply-job/{jid}`: Mutating action that updates project revision and creates receipts. **Deferred to Stage M5**.
6. `/api/stock`: Pixabay provider HTTP search. **Deferred to Stage M4**.

#### Unicode & Security Verification
- **Telugu Script & Roman Transliteration**: Verified identical SHA-256 (`5b934e4d9b523c7f648b0b12c0f87a595d21391740966bef01988bcb71ff7d64`) over HTTP between FastAPI (:8787) and Django (:8788).
- **Security Headers**: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cache-Control: no-store` enforced on all `/api/` responses.
- **Write Protection**: Mutating requests rejected (HTTP 403 / 405).

#### Test Results Summary
- **Pytest Suite**: **84 passed, 2 skipped, 0 failed** in 65.66s.
  - Workflow regression tests (20): PASSED
  - Phase 3 Celery/Redis tests (9): PASSED
  - Phase 4 Voice Lab tests (21 passed, 2 skipped): PASSED
  - Django M1 tests (5): PASSED
  - Django M2 tests (13): PASSED
  - Django M3 tests (16): PASSED
- **UI Smoke Test**: PASSED with **0 runtime errors**.
- **Alembic Revision**: `0003_phase4_voice_lab` (Unchanged).
- **Database Row Counts**: Exactly 0 writes from Django.
- **Baseline Video**: `test-outputs/phase-3-home/phase3_home_reel.mp4` (7,481,435 bytes) untouched.



