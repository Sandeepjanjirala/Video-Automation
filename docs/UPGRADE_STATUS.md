# Telugu Reel Studio — Upgrade Status & Technical Audit

**Last Updated**: 2026-10-09  
**Current Phase**: Phase 3 — Distributed Processing: Redis, Linux Celery Workers, Transactional Outbox Dispatcher, Execution Leases & Granular Stage Pipeline  
**Status**: Phase 3 Complete — All 29 Automated Tests Passing; UI Smoke Tests Passing with 0 errors; Real 1080x1920 MP4 Regression Video Verified; Awaiting User Approval to Advance  

---

## 1. System Environment Profile

| Component | Host Specification / Status | Notes |
|---|---|---|
| **Operating System** | Microsoft Windows 10 Pro (x64, Build 10.0.19045) | Primary development host |
| **CPU** | 13th Gen Intel Core i5-13400 (10 Cores: 6P+4E, 16 Logical Processors) | 4 worker encode threads configured |
| **RAM** | 16 GB Total (~15.6 GB visible, ~2.7 GB free physical) | Connection pool sized at 10 (max overflow 20) |
| **GPU / Acceleration** | Intel UHD Graphics 730 (Integrated, 128 MB dedicated RAM) | No NVIDIA GPU / No CUDA. CPU libx264 encoding |
| **Python Runtime** | Python 3.12.10 in Windows `.venv` & Python 3.12.3 in WSL Linux `/opt/celery_venv` | Python 3.12 across Windows host and Linux WSL |
| **FFmpeg Version** | Windows: FFmpeg 9.0.2-full_build; Linux WSL: FFmpeg 8.0.1 | Filters available: `subtitles` (libass), `loudnorm`, `sidechaincompress` |
| **WSL2 Runtime** | Ubuntu 24.04 (WSL 2) Active & Running | Host for Linux PostgreSQL 18, Redis 7, and Celery workers |
| **PostgreSQL Database** | PostgreSQL 18 on private port `127.0.0.1:5433` | Port 5433 prevents collision with Windows 5432 services |
| **Redis Broker & Backend** | Redis 7 on private port `127.0.0.1:6379` | `appendonly yes` (AOF), `maxmemory 512mb`, `noeviction` |
| **Celery Environment** | Celery 5.6.3 running on Linux (WSL / Docker) | Concurrency: Render (1 slot), Network (2 slots) |
| **Alembic Migrations** | `0001_phase2_initial` and `0002_phase3_leases_and_outbox` applied | Tables: `workspaces`, `projects`, `revisions`, `assets`, `jobs`, `job_stages`, `drafts`, `application_receipts`, `outbox`, `usage` |
| **Active Port** | `127.0.0.1:8787` (FastAPI / Uvicorn) | Single-user loopback binding with CSRF protection |

---

## 2. Phase 3 Architecture & Distributed Execution Layer

### 1. Celery on Linux & Worker Pools
- Celery 5.6.3 configured in [`studio/celery_app.py`](file:///c:/Users/Sandeep.J/Documents/Studio/studio/celery_app.py) targeting Linux execution (WSL2 Ubuntu and Docker Compose).
- Separate worker pools with conservative resource limits:
  - **Render Worker Pool**: Dedicated `render` queue with concurrency 1 slot (`-c 1`). CPU libx264 encoding and FFmpeg concatenation.
  - **Network Worker Pool**: Dedicated `network` queue with concurrency 2 slots (`-c 2`). HTTP provider requests, Edge TTS voice synthesis, and Pixabay downloads.
- Shared development media volume mounted at `data/` (`/app/data` in container).

### 2. Granular Stage Decomposition
The production workflow is partitioned into discrete, idempotent stages without creating a task per video frame or blocking workers synchronously on child tasks:
1. `research`: Topic research and claim verification via grounded provider calls.
2. `draft`: Storyboard script generation and scene structure.
3. `footage` (`stock` / `stock_all`): Pixabay stock footage acquisition and SHA-256 deduplication.
4. `narration`: Telugu speech synthesis and audio cache validation.
5. `alignment`: Word-index or text-proportional Roman Telugu caption alignment.
6. `scene_rendering`: Individual scene rendering (540p draft / 1080p final) with ASS subtitle burn-in.
7. `assembly`: Lossless FFmpeg concat demuxer join into continuous video.
8. `mixing`: Background music sidechain ducking compression.
9. `validation`: FFprobe stream/dimension/duration verification and FFmpeg null-muxer full decode.

### 3. Transactional Outbox Pattern & Repeat-Safe Dispatch
- Jobs and Outbox events are persisted in the **same PostgreSQL transaction** in [`studio/store.py`](file:///c:/Users/Sandeep.J/Documents/Studio/studio/store.py).
- Outbox Dispatcher ([`studio/dispatcher.py`](file:///c:/Users/Sandeep.J/Documents/Studio/studio/dispatcher.py)):
  - Polls unprocessed events using `SELECT ... FOR UPDATE SKIP LOCKED` to prevent concurrency contention.
  - If Redis is offline or write-rejected under memory pressure, events are retained in PostgreSQL with exponential backoff and jitter; accepted work is **never lost**.
  - Periodic reconciliation identifies queued jobs lacking outbox records and reconstructs them automatically.

### 4. Small JSON Payloads
- Celery task signatures accept only small JSON identifiers (`job_id`), never raw media bytes, API keys, SQLAlchemy model instances, or machine-specific absolute file paths.
- Paths are resolved uniformly relative to `DATA_DIR` across Windows and Linux environments.

### 5. Idempotent Execution Leases & Fencing
- Execution leases with unique worker tokens (`lease_token`), expiration timestamps (`lease_expires_at`), and periodic heartbeats every 15s during long tasks.
- `verify_lease_and_not_cancelled` performs fencing checks before publishing output artifacts or updating terminal state, preventing stale workers from overwriting newer work.
- `recover_expired_leases()` recovers **only expired ownerships** (`lease_expires_at < now`); it never blindly resets all running jobs on worker startup.
- `operation_key` database-enforced idempotency prevents duplicate job submissions.

### 6. Atomic, Validated Artifact Publication
- Narration cache entries are marked ready only when **both** the audio file and its timing JSON metadata exist and validate.
- Concurrent synthesis requests write to unique temporary paths (`*.part.mp3`, `*.part.json`) before atomic `os.replace`, preventing partial or corrupted reads.

### 7. Reliability, Visibility & Retries
- Configured Celery with `task_acks_late=True` and `task_reject_on_worker_lost=True` so dropped tasks return to the queue.
- Bounded retries with backoff for transient network issues; non-retryable user input/credential failures fail immediately with actionable guidance.
- Provider uncertainty is tracked explicitly in database stages (`ambiguous_outcome: True`).

### 8. Shared Rate & Budget Controls Across Workers
- Distributed sliding-window rate limiter via Redis DB 2 ([`studio/ratelimit.py`](file:///c:/Users/Sandeep.J/Documents/Studio/studio/ratelimit.py)).
- PostgreSQL atomic upsert (`INSERT ... ON CONFLICT (day, provider) DO UPDATE`) in `Usage` table tracking request counts, estimated units, actual billable units, and uncertain calls.

### 9. Subprocess Cancellation
- Active OS PIDs for FFmpeg/FFprobe subprocesses are tracked in `Job.subprocess_pid`.
- Cancellation stops only the target job's subprocess tree (`taskkill /F /T` on Windows; process tree kill on Linux) without disrupting the worker process or other jobs.

### 10. Redis Persistence & Memory Limits
- Redis configured with `appendonly yes` (AOF durability), `maxmemory 512mb`, and `maxmemory-policy noeviction`.
- Logical database separation:
  - DB 0: Celery broker (`redis://127.0.0.1:6379/0`)
  - DB 1: Celery result backend / chord synchronization (`redis://127.0.0.1:6379/1`, 24h result expiry)
  - DB 2: Expendable cache & distributed rate limits (`redis://127.0.0.1:6379/2`)

### 11. Legacy Queue Consumer Disabled
- When running in distributed mode (`STUDIO_DISTRIBUTED=1`), `run.py` and `studio/worker.py` disengage the legacy polling worker to prevent dual queue consumers owning the same stream.
- Infrastructure ports (`5433`, `6379`) remain bound to private loopback `127.0.0.1`.

---

## 3. Mandatory Phase 3 Verification & Acceptance

### A. Automated Integration Test Suite (PostgreSQL 18 & Redis)
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workflow.py tests/test_phase3.py -v
```
**Result: 29 passed, 1 warning in 32.31s (100% Pass Rate)**

#### Phase 3 Concurrency & Failure Tests (`tests/test_phase3.py`):
1. `test_phase3_redis_outage_during_enqueue`: PASSED (Broker outage does not drop accepted work; dispatcher drains on recovery)
2. `test_phase3_operation_key_and_duplicate_delivery`: PASSED (Operation key idempotency & dual-worker lease fencing)
3. `test_phase3_worker_death_and_artifact_publication`: PASSED (Abandoned worker lease recovered; replacement worker completes safely)
4. `test_phase3_worker_startup_during_active_render`: PASSED (Worker startup preserves active unexpired leases)
5. `test_phase3_stale_completion_fencing`: PASSED (Stale worker tokens rejected by fencing checks)
6. `test_phase3_job_cancellation_and_subprocess`: PASSED (Cancellation persists and terminates subprocess tree)
7. `test_phase3_rate_limiting_and_usage_tracking`: PASSED (Redis rate slot acquisition and PostgreSQL atomic usage tracking)
8. `test_phase3_concurrent_identical_speech_requests`: PASSED (Parallel synthesis produces valid audio & timing without corruption)
9. `test_phase3_dispatcher_reconstruction_after_crash`: PASSED (Dispatcher reconciles orphaned jobs lacking outbox records)

#### Phase 2 Preserved Workflow Tests (`tests/test_workflow.py`):
- All 20 integration tests passed with zero regressions.

### B. Frontend UI Smoke Tests
```powershell
node tests/ui_smoke.cjs
```
**Result: 0 runtime errors**  
`{"reactRuntimeErrors":[],"checks":["new project","script fields","save","approval","visual edit","revision invalidation","queue render","visible offline failure"]}`

### C. Responsiveness Measurement
- Average API latency measured during active render execution: **7.0 ms**
- Rendering tasks execute out-of-process without blocking API request threads.

### D. Real Playable MP4 Regression-Test Video
- **Output Video Location**: [`data/test_output_phase3/phase3_reel.mp4`](file:///c:/Users/Sandeep.J/Documents/Studio/data/test_output_phase3/phase3_reel.mp4)
- **Accompanying Artifacts**:
  - Captions SRT: [`data/test_output_phase3/captions.srt`](file:///c:/Users/Sandeep.J/Documents/Studio/data/test_output_phase3/captions.srt)
  - Source Report: [`data/test_output_phase3/source-report.json`](file:///c:/Users/Sandeep.J/Documents/Studio/data/test_output_phase3/source-report.json)
  - Project Snapshot: [`data/test_output_phase3/project.json`](file:///c:/Users/Sandeep.J/Documents/Studio/data/test_output_phase3/project.json)
- **Specifications & Verification Metrics**:
  - **Resolution**: `1080x1920` (9:16 vertical video reel)
  - **Duration**: `9.89 seconds`
  - **File Size**: `4.63 MB` (4,859,828 bytes)
  - **Video Stream**: H.264 / libx264 (yuv420p, 1080x1920, 30 fps)
  - **Audio Stream**: AAC stereo, 48000 Hz (conversational Telugu narration mixed with ducked BGM)
  - **Footage Content**: Real moving video footage from Pixabay clips `10685` and `8897` across both scenes.
  - **Telugu Narration**: Natural synthesized conversational Telugu voice (`te-IN-ShrutiNeural`).
  - **Roman Telugu Captions**: Burned-in Roman Telugu captions (`Oka chinna oorilo...`, `Aa inti nunchi...`).
  - **Background Music**: Ducked background music track (`a4ed9853a0f645f5b94f7d4c5d3753a8.mp3`).
  - **FFmpeg Full Decode**: `ffmpeg -v error -xerror -i final -f null -` returned **0 decoding errors**.
  - **Render Elapsed Time**: **8.18 seconds**

---

## 4. Development Operations & Launchers

| Command / Script | Purpose |
|---|---|
| `scripts/launch_phase3_stack.bat` | Windows multi-service launcher (verifies PostgreSQL & Redis, starts WSL Celery/Dispatcher, starts API) |
| `scripts/launch_phase3_wsl.sh` | WSL Linux background launcher for separate worker pools (`render`, `network`) and Outbox Dispatcher |
| `docker compose up -d` | Full Linux container stack (`postgres`, `redis`, `api`, `dispatcher`, `worker_network`, `worker_render`) |
| `python doctor.py` | Full diagnostics covering FFmpeg, PostgreSQL, Redis persistence, and Celery configuration |

---

## 5. Scope Boundary & Handoff Notice

Phase 3 is fully implemented and verified. Per project instructions, **work stops here**.
Do not advance to Phase 4 until user review and explicit approval are provided.
