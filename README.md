# Telugu Reel Studio

A local workspace for turning your researched script into a vertical video:
script and Roman Telugu captions → footage → narration → draft review → MP4 export.

**Instagram publishing is not implemented yet.** Download the final reel and upload
it manually. YouTube long videos are outside the current scope.

## Current architecture

- Django + Django REST Framework; Django templates and local jQuery.
- PostgreSQL for projects, immutable revisions, assets, jobs, and voice takes.
- FFmpeg/FFprobe for video processing, subtitles, narration normalization, and music ducking.
- Local mode: one worker process launched by `run.py`.
- Distributed mode: Redis, a transactional outbox dispatcher, and Celery workers.
  Both modes execute the same leased job pipeline.
- Optional providers: Gemini for research/script preparation, Pixabay for footage,
  Edge community TTS, Azure Speech, and Sarvam speech. Provider credentials are separate.

The older migration documents in `docs/` describe historical stages, including
removed FastAPI, React, SQLite, and Alembic components. They are not current setup instructions.

## Start on Windows

Python 3.12 and FFmpeg/FFprobe are required. FFmpeg needs `libx264`, `subtitles`,
`loudnorm`, and `sidechaincompress`. Node.js is needed only for frontend tests.

1. Run `SETUP_WINDOWS.bat` to install Python dependencies into `.venv`.
2. Start PostgreSQL. With Docker available, infrastructure can be started using
   `docker compose up -d postgres redis`.
3. The default local database is `postgresql://postgres:postgres@127.0.0.1:5433/studio`.
   Override it with `STUDIO_DATABASE_URL` when needed. Percent-encode special
   characters in URL credentials. `STUDIO_MIGRATION_DATABASE_URL` can supply a
   separate migration identity. Existing scoped identities in `backups/` are
   supported; their grants must cover the application's operations.
4. Initialize/update the database:

   ```powershell
   .venv\Scripts\python.exe manage.py migrate
   .venv\Scripts\python.exe doctor.py
   ```

5. Run `START_WINDOWS.bat` and open **http://127.0.0.1:8788**.
6. Configure FFmpeg paths and optional provider credentials in Settings.

The launcher checks database migrations before starting. Keep its terminal open
while jobs run. This is a single-user local app using Django's development server,
not an authenticated public hosting service.

To start the entire distributed stack, use `docker compose up --build`.
Compose runs migrations before the API, dispatcher, and workers. The image includes
templates, static assets, FFmpeg, and fonts. Local and distributed workers must not
be run against the same job stream simultaneously.

## Make a reel from your script

1. Create a story and paste your script into **Quick script importer**.
2. Select **Telugu written in English letters** (default) or native Telugu.
   **Split into scenes** works locally. Each scene preserves your Roman Telugu
   input/captions separately from its native Telugu narration. **Convert to Telugu**
   opens a review window: use your configured Gemini or Sarvam text API, or paste
   Telugu manually. Apply only after checking names and pronunciation. Editing a
   converted Roman source requires another narration review before export.
3. Add your research/evidence notes, or identify the script as original fiction.
   Save and approve the current revision.
4. Upload a video or JPEG/PNG/WebP image, reuse library media, find free stock,
   or select title cards. Automatic stock search uses Pixabay first, then Pexels
   when no results are found (or only Pexels is configured). Each scene also has
   a source selector. Provider errors are shown; choose the other source explicitly
   if credentials or quota fail. Review relevance and usage rights.
5. Each scene has a **Prompt** button. Edit, save and copy its 5–10 second prompt
   into a video generator of your choice, then upload the result. Prompt creation
   is local and free; this app does not call a video generation API. External free
   quotas, watermarks and usage terms depend on that service. Choose **Loop clip**
   or **Hold the last frame** for short footage; images hold for the narration.
6. New projects use **Edge Mohan**, a free online male Telugu narrator. Enable
   online services for synthesis, or upload recordings for offline work. Voice Lab
   auditions, accepted takes, caching and revision history remain available.
7. Save and approve after edits. Prepare narration to review it, then render a
   **540×960 draft**. Inspect framing, pronunciation, and captions.
8. Once satisfied, optionally choose **Upgrade to Sarvam voice**. This selects
   Shubh (Bulbul v3 male); Ratan is also available. The selection itself makes no
   paid call. Save and approve, then prepare narration or render with Sarvam.
   Old generated takes and manual cue timings are invalidated when voice settings
   change. Uploaded human recordings and selected visuals are preserved.
9. Render the **1080×1920 final**, download `reel.mp4`, and upload it manually
   to Instagram or YouTube. Final resolution never changes your voice provider.

Editing narration invalidates old normalized speech and take references. Existing
recordings must match the current narration; reattach or regenerate them when it
changes. A stale voice take cannot be accepted against a changed script.

Target duration is 10–180 seconds. Natural mode uses actual narration duration.
Target-fit mode permits only 0.8–1.25× adjustment; excessive changes require script
editing. Output is H.264/AAC at 30 fps. Caption timing is approximate unless manually
edited; automatic word-index alignment still needs review.

## Providers, files, and recovery

Online services are disabled in new installations. Select a Gemini model available
in your own API account; the app does not silently choose one. The Gemini daily
limit counts HTTP attempts, including retries. It is a request limit, not a spending cap.
Edge TTS is a community integration with an online service, not an offline voice model.
It requires no API key but has no availability guarantee. Text conversion is separate
from free voice: Gemini uses your account quota; Sarvam conversion is metered. Manual
Telugu entry and prompt creation need no API calls. Successful conversions are cached,
including Sarvam's individual chunks of at most 1,000 characters. Stock results are
cached for 24 hours; downloads are deduplicated and retain source/creator/license data.

Sarvam uses the documented Bulbul v3 speaker IDs and supported pace controls, stores
WAV audio with the correct extension, and labels its caption alignment as estimated.
Old projects using retired Sarvam IDs must select a current voice; they are not
silently remapped. Existing explicit voice selections in other projects are retained.

Provider contracts checked against the official [Sarvam TTS](https://docs.sarvam.ai/api-reference/text-to-speech/convert),
[Sarvam transliteration](https://docs.sarvam.ai/api-reference/text/transliterate-text),
and [Pexels API](https://www.pexels.com/api/documentation/) documentation.

Media, caches, settings, and exports live under `data/`, or `REEL_DATA_DIR` when set.
Projects and job records live in PostgreSQL. Back up **both**, with workers stopped.
Keys are kept in local settings and masked by the API. Export reports can contain
editorial notes. A completed render provides `reel.mp4`, `captions.srt`,
`source-report.json`, and `project.json`.

Failed jobs can be retried. Retrying current inputs requires approval of the current
revision; original-input retries retain their original snapshot. Cancelled and
completed jobs cannot be restarted by duplicate queue delivery. Crashed execution
leases expire and become retryable failures. Do not delete original assets while
saved projects still reference them.

## Verification

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm install --prefix frontend
.venv\Scripts\python.exe manage.py check
.venv\Scripts\python.exe scripts/run_audit_tests.py
```

The audit runner creates and removes a uniquely named PostgreSQL test database,
uses separate media/settings, disables online services, and retains test artifacts
under `test-outputs/`. The test database account needs CREATE DATABASE permission.
It tests real FFmpeg exports, uploads, editor controls, concurrency, cancellation,
voice selection, quotas, and fresh migrations. Browser-DOM tests use Node.js/jsdom.

**Do not run the historical integration suites against your working database.**
Several older fixtures use hardcoded `studio`/`studio_test` databases, alter roles,
or assume obsolete migration stages and already-running servers. The isolated
runner deliberately runs `tests/test_audit.py`; it does not certify those old suites.

See `docs/AUDIT_2026-10-30.md` for the changes, verification scope, and remaining gaps.
