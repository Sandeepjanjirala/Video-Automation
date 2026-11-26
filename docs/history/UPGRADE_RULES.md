# Telugu Reel Studio — Upgrade Rules & Operating Guidelines

## Mission & Goal
A reliable, scalable reel-production application with natural conversational Telugu narration, Roman Telugu captions, and high-quality vertical video (9:16, 1080x1920).
All work is strictly incremental: improve **THIS** repository; do not replace it with an external application (e.g. ShortsGenius).

---

## Preserved Workflows

### Workflow A: Uploaded Media to Finished Reel
1. Upload MP4 / user media assets.
2. Reusable footage assigned across selected scenes.
3. Natural Telugu narration (synthesized or uploaded audio recording).
4. Synchronized Roman Telugu captions.
5. Optional background music with automatic speech-triggered ducking.
6. Vertical 9:16 reel rendering (540p draft preview and 1080p final cut).

### Workflow B: Topic / Script Automation to Finished Reel
1. Topic prompt or user-supplied script (Telugu Unicode or Roman Telugu).
2. AI-assisted research and claim review with grounded sources (Gemini).
3. Editable storyboard draft with native Telugu narration, Roman transliteration, stock keywords, and graphic headings.
4. Sourcing footage for unassigned scenes (Pixabay stock search & downloads).
5. Voice preparation & pronunciation review (Edge TTS / Azure Speech / uploaded audio).
6. Audio-caption alignment, timing review, and manual cue editing.
7. Background music ducking and final rendering.

### Capabilities to Preserve
- Per-scene video uploads and scene narration audio uploads.
- Pixabay footage search and download integration.
- Full manual scene editing (text, framing, crop position, trim start, scene order).
- Explicit graphic title card scenes (no external video required).
- Draft (540x960) and final (1080x1920) H.264 / AAC 30fps export pipeline.
- Revision history and immutable project revision snapshots.
- Completely offline operation when supplied media and recordings are used.
- Existing voices (`te-IN-ShrutiNeural`, `te-IN-MohanNeural`) as primary working options.

---

## Architecture Direction

- **Core Stack**: Keep React (frontend), FastAPI (API backend), and FFmpeg (media processing).
- **Primary Database**: Upgrade from single-file SQLite to PostgreSQL using SQLAlchemy ORM and Alembic migrations.
- **Queue & Distributed Execution**: Upgrade from single-process SQLite queue to Redis and Celery.
- **Storage Interface**: Introduce an abstract storage provider (local filesystem first, pluggable for S3/MinIO object storage later).
- **Structure**: One modular application repository; avoid creating distributed microservices per function.
- **Data Protection**: SQLite remains a protected migration source. Avoid maintaining two divergent production database systems.

---

## Target Environment & Constraints

- **Host OS**: Microsoft Windows 10 Pro (x64).
- **Hardware Profile**: 13th Gen Intel Core i5-13400 (10 cores, 16 threads), 16 GB RAM, Intel UHD Graphics 730 integrated.
- **Linux Infrastructure**: WSL2 Ubuntu 24.04 and Docker Desktop available on the host machine.
- **Resource Constraints**:
  - No dedicated NVIDIA GPU or CUDA runtime.
  - Do not assume unlimited CPU or RAM.
  - Do not change Windows OS security settings or install system-wide software without explicit user permission.
  - Celery and Redis stack will target supported container/WSL2 environments.

---

## Non-Negotiable Rules

1. **Verify Before Editing**: Inspect actual source code, configuration files, schemas, and tests before editing. Inspection reports are leads, not substitutes for active code verification.
2. **Protect User Data**: Never delete, wipe, or reset user databases (`studio.sqlite3`), media assets (`data/assets`), cache, revisions, or exports to make tests pass. Use isolated temporary directories for all tests. Never overwrite uncommitted changes.
3. **Protect Credentials**: Inspect secret names and configuration flags only (`*_configured`). Never log, commit, copy into reports, or leak API keys (`gemini_key`, `pixabay_key`, `azure_key`). Do not commit user media or database files to source control.
4. **Verified Migration Protocol**: Before migrating real data, obtain user approval, create an atomic SQLite backup (`c.backup()`), and verify restoration in an isolated location. Keep the existing SQLite deployment intact until cutover is fully verified. Never copy a live SQLite file naively or remove WAL/journal files.
5. **No Unauthorized Live Calls**: Do not initiate paid external provider calls or transmit private user media/scripts without specific approval of provider, payload, and budget. Mocked tests are required for automated test suites.
6. **Documentation & Specification Grounding**: Validate model IDs, voice capabilities, dependency versions, and API parameters against official provider documentation. Pin tested dependency versions. Do not invent endpoints.
7. **No Deceptive Fallbacks**: Never silently substitute unrelated stock footage, generic title cards, an unselected speaker voice, or simulated export files after an operation fails.
8. **Incremental Phased Delivery**: Implement strictly one approved phase at a time. Keep the application runnable at all checkpoints with working, tested code.
9. **Frontend Bundle Verification**: Whenever UI code (`frontend/`) is changed, rebuild the distribution bundle (`studio/static/app.js`) and verify that the served bundle reflects the change.
10. **Scope Boundaries**: Do not add Kubernetes, Kafka, billing/metering systems, automated social publishing, voice cloning, or generative AI video models in these phases. Provide documented extension hooks instead.

---

## Persistent Handoff Workflow
- Read `docs/UPGRADE_RULES.md` and `docs/UPGRADE_STATUS.md` at the start of every phase.
- Update `docs/UPGRADE_STATUS.md` upon completion of each phase with changed files, test results, limitations, and the next scheduled phase.
