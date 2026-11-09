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
`loudnorm`, and `sidechaincompress`. Node.js 24.15+ (or 22.22.2+) is needed only
for frontend tests; Node 20 is too old for the pinned jsdom dependency.

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

The image is based on Debian 13 (FFmpeg 7.1, libass 0.17.3, Noto Telugu fonts). Its
`test` build target adds Node.js 24 and the development dependencies, so the complete
audit suite (including the browser-DOM tests) runs in a container:

```powershell
docker build --target test -t studio-test .
docker run --rm -e STUDIO_DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/postgres studio-test
```

Point it at a disposable PostgreSQL server: the runner creates and drops its own uniquely
named database. Plain `docker build .` still produces the application image used by Compose.

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
   Adjust each scene's framing and trim in its preview (see **Framing preview** below).
6. New projects use **Edge Mohan**, a free online male Telugu narrator. Enable
   online services for synthesis, or upload recordings for offline work. Voice Lab
   auditions, accepted takes, caching and revision history remain available.
7. Save and approve after edits. Prepare narration to review it, then open the
   **Captions** tab (see below) and render a **540×960 draft**. Inspect framing,
   pronunciation, and captions.
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

### Caption editor

The **Captions** tab edits each scene's caption text (Roman Telugu or Telugu script)
and start/end times, in seconds from the start of that scene's narration (`1.25` or
`0:01.25`). Timings are checked against the *measured* length of the audio the render
will use: an attached recording, or prepared narration whose text and voice settings
still match. Scenes without such audio show **Not measured**; choose **Prepare
narration** first. Measuring never synthesizes speech.

- **Play** seeks to a caption and stops at its end; **Start/End = playhead** copy the
  paused audio position. **Split** divides at the playhead (or midpoint), **Merge with
  next** joins two captions, and **Reset to automatic timing** rebuilds captions from
  the measured audio using the Roman captions or the Telugu narration.
- Invalid times, empty text, captions past the narration's end (100 ms tolerance) and
  overlaps are flagged and block saving. Very short captions only warn.
- Edits are drafts until **Save captions**, which creates a new revision to approve.
  The page warns before leaving, switching or creating a project, approving, preparing
  narration or rendering while caption edits are unsaved. If a text or narration change
  clears a scene's timings, **Restore previous captions** brings them back for review.
- Saved captions flow unchanged into the shared timeline, SRT and `timing-manifest.json`.
  Telugu-script captions render with Nirmala UI on Windows; the Docker image installs
  `fonts-noto-core` for Noto Sans Telugu.

### Caption styles

The **Caption style** panel on the Captions tab offers four presets for the whole reel:
**Classic outline** (the original style, and the default for every existing project),
**Black box** (a solid box behind each line), **Yellow accent** (yellow text, heavy
outline) and **Translucent background** (one see-through panel per caption). Styles
never change caption text, timings, size or bottom margin; size and margin can be set
here or on the Export tab. The panel previews the style live over a scene's real frame;
**Render exact caption** draws it with FFmpeg/libass exactly as draft and final exports
do, including title-card scenes. Scene framing previews use the selected style too.
Telugu captions use complex (HarfBuzz) shaping in every style, so joined letters render
correctly. Box styles need libass 0.17 or newer (the bundled FFmpeg build has it).

### Framing preview

Each scene with an uploaded or stock video or image shows a 9:16 preview of exactly
how it will sit in the reel:

- **Frame shape**: full vertical 9:16, or a 4:5, 1:1 or 16:9 band centred in the reel.
  **Framing** fills that shape (cropping edges) or shows the whole picture with padding.
- **Zoom** (1–3×) and **horizontal/vertical position** choose the visible part; drag
  the preview to reposition. Position sliders are disabled on an axis with nothing to crop.
- Videos: **Play** previews the trimmed part, looping it or holding its last frame as
  the export will. **Trim start/end** accept seconds or **Start/End = playhead**; end 0
  means the clip's end. Trimmed loops now restart at the trim start (previously
  `-stream_loop` restarted at 0 s).
- **Render exact frame** asks FFmpeg for the frame at the playhead using the export's
  filters and caption style, at 1080×1920. The live preview's caption is a
  position guide; the exact frame shows real libass text.

The preview and renderer share one geometry (`studio/framing.py`, mirrored in
`studio/static/js/framing.js`), computed in even output pixels from the picture size
FFmpeg sees after phone rotation metadata (EXIF photos and rotated videos) and
non-square pixels. Every scene is normalised to BT.709 limited range, so a full-range
JPEG scene no longer switches the joined reel's colour range. Older projects keep
their previous framing via defaults (zoom 1, centred, full frame, no trim end).

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

New renders also include `timing-manifest.json`, downloadable from the export card.
Its versioned JSON records measured narration durations, playback speed, 30 fps
scene boundaries, holds, scene-local caption cues, reel-global caption cues, and
the measured MP4 duration. Rendering and SRT export use the same shared timeline.
Small differences between planned and measured duration can result from audio
encoding. Automatic caption alignment remains approximate and needs review.
Older exports remain available but do not gain a manifest until re-rendered.

Failed jobs can be retried. Retrying current inputs requires approval of the current
revision; original-input retries retain their original snapshot. Cancelled and
completed jobs cannot be restarted by duplicate queue delivery. Crashed execution
leases expire and become retryable failures. Do not delete original assets while
saved projects still reference them.

## Verification

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Push-Location frontend
npm install
Pop-Location
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
