# Stage 1: shared timeline and timing manifest — 6 November 2026

Implemented in Django + jQuery without merging or copying the old architecture.
The earlier Windows fixes below remain in the working tree. No commits, pushes,
PR merges or branch deletions were performed; `branch-local-work` remains at
`0a5692c`.

- Added `studio/timeline.py` as a pure timing calculation module. It uses actual
  probed narration durations, matches prepared audio by scene ID, applies the
  existing 0.8–1.25 target-fit limits, preserves holds, and accumulates integer
  frame counts at 30 fps. It rejects missing/duplicate narration, nonfinite or
  invalid durations, and invalid cue ordering/bounds without replacing edits.
- Rendering, burned captions, SRT offsets, mixing duration and output validation
  now consume that shared timing model. Existing caption alignment and the
  existing 100ms manual cue-end tolerance are retained. Voice auditions remain
  original-speed audio; no caption-editor behavior changed in this stage.
- Added schema-version-1 `timing-manifest.json` with project/revision/job identity,
  dimensions, measured audio durations, playback speed, scene frame boundaries,
  holds, scene-local and reel-global cues, alignment labels, planned duration and
  measured MP4 duration. Django serves it only for completed exports, and the
  jQuery export card provides a download link only when the artifact exists in
  the job result. Old exports remain accessible without a manifest.
- Preserved image/video rendering, loop/freeze, audio cache fingerprints, cache
  reuse, lease fencing, cancellation, protected publication, and playback fixes.
  No database migrations or edits to existing project bodies were necessary.

Verification: **49 tests passed in 16.17 seconds** using the isolated audit runner.
Coverage includes natural/target timing, speed limits, scene ID matching, unchanged
input cues, invalid inputs, real multi-scene renders with manual cues/holds/music,
manifest HTTP delivery, SRT/manifest agreement, draft/final dimensions and cache
reuse, plus all prior regression tests. Audit artifacts:
`test-outputs/studio_audit_d21a7a1c53e0/`.

Chrome completed a new two-scene project with fresh free Mohan narration, manual
Telugu review, prompt saving, video/PNG uploads, freeze selection, approval,
540x960 draft and 1080x1920 final exports, playable previews, and manifest
downloads. Both downloaded MP4s fully decoded through FFmpeg. Browser page errors:
zero. Mobile horizontal overflow: false. Export screenshot visually inspected.
Scripts, screenshots, MP4s and manifests: `test-outputs/stage1/`.

Django system checks and migration drift checks passed; git diff check passed
with only the existing Windows line-ending notice. The server and local worker
were restarted with no pending jobs and remain running at http://127.0.0.1:8788.

Stage 1 is complete. Caption editor, crop previews, caption style presets and
the final Stage 5 acceptance pass remain pending separate stage approvals.
Automatic alignment is still approximate; this stage does not claim forced
alignment or eliminate small audio-encoding duration differences.

---

# Windows verification — 6 November 2026

Verified on the user's Windows computer, on `improve/free-preview-editor`, based
on commit `182bcc1`. Changes below are local and uncommitted. No branch was merged
or deleted and no GitHub push or PR merge was performed.

## Setup and simple explanations

- Reused the existing Python 3.12.10 `.venv`; installed the missing Django/DRF
  dependencies and Playwright for browser verification.
- With user approval, created the Docker Compose PostgreSQL 18 service on
  `127.0.0.1:5433` and applied all three Django migrations. It uses the persistent
  `studio_postgres_data` volume. The existing Windows PostgreSQL service on port
  5432 was left alone because its credentials were unavailable. This is a new
  database; old projects have not been imported from another database.
- FFmpeg/FFprobe and the required filters are installed and working.
- Node 20 was too old for jsdom. Installed Node 24.22.2 through the existing NVM
  installation and used it through a process-local PATH during testing. The
  machine's default Node selection remains unchanged. For future frontend tests,
  run `nvm use 24.22.2`, or prepend that installed version to the shell's PATH.
- Run npm from `frontend`; the README's former prefix command failed on this
  Windows setup. Refreshed its stale React-era lockfile to match package.json and
  updated the vulnerable transitive undici dependency. npm audit: zero findings.
- Free Edge speech initially returned HTTP 403 with edge-tts 7.2.8. Upgrading to
  7.3.1 fixed live Mohan synthesis. Both dependency lists now pin that version.
  Upstream release notes: https://github.com/rany2/edge-tts/releases
- Doctor now explains that Redis/Celery services are optional in local worker
  mode instead of reporting their absence as a setup error.

The application is running at http://127.0.0.1:8788 with its local worker in a
hidden background process. For a future start, open Docker Desktop, run
`docker compose up -d postgres`, then `START_WINDOWS.bat`. Do not start a second
copy while port 8788 is occupied. Current run logs are in
`test-outputs/local-verification/server.log` and `server-errors.log`.

## Comparison with branch-local-work

That branch's unique commit, `0a5692c`, extends the older FastAPI/React architecture.
Its important additions absent from this Django branch are:

- Shared scene/project timeline calculations and a `timing-manifest.json` export.
- Caption cue split, merge and reset endpoints and an interactive cue editor.
- Cue provenance/review markers, text mismatch warnings, and caption style presets.
- Server-generated crop previews and footage orientation/reuse warnings.

The current branch already has manual cue JSON, framing/trim controls, library
reuse, voice caching, uploads, drafts and final renders. The additions above were
not merged or ported. `branch-local-work` remains intact.

## Repairs and verification

- Fixed draft export headings that incorrectly claimed 1080x1920.
- Fixed background polling replacing video/audio players every eight seconds.
  Unchanged job results retain their DOM; exports also retain their players when
  unrelated jobs change. Chrome verified continuous playback across a quiet poll.
- Fixed mobile title/badge overflow and wrapping in export cards. Verified Story,
  Voice and Export at 390px, plus desktop at 1440px; screenshots inspected visually.
- Removed the sidebar's false claim that Celery is active in every installation.
- Added a readable Edge HTTP 403 recovery message and a regression test covering
  partial audio cleanup. Added DOM regressions for playback preservation and labels.
- Final isolated audit: **37 passed in 12.95 seconds**. Fresh database migrations,
  deterministic provider tests, real uploads, renders, cancellation, concurrency,
  and the rendered-page DOM tests are included. Artifacts:
  `test-outputs/studio_audit_deecc118804b/`.
- Django checks, migration drift check, pip check and git diff check passed.
- Real Chrome workflow used local script splitting, manual reviewed Telugu entry,
  prompt saving, video and PNG scene uploads, short-clip freeze selection, save,
  approval, live free Mohan narration, draft render and final render/download.
- A separate live Voice Lab audition generated playable audio. No paid provider
  calls were made. Browser runs ended with no JavaScript page errors.
- Both downloaded MP4s fully decoded through FFmpeg: H.264/AAC, 30 fps, 540x960
  draft and 1080x1920 final, each 5.688 seconds. Inspected a final frame with
  readable Roman Telugu captions. This was a short two-scene diagnostic sample,
  not a long-form render or a subjective pronunciation assessment.

Final sample project: `d9b250cec4274b77bb95f235640eccd4`, titled
"Windows Telugu workflow verification". Earlier diagnostic projects are retained.
Browser scripts, screenshots, generated fixture media, downloaded `draft.mp4` and
`final.mp4`, and job metadata are in `test-outputs/local-verification/` (gitignored).

Historical integration suites were not run because some target hardcoded live
databases or obsolete migration stages. Paid conversion, paid speech, live stock
search, distributed Celery deployment, and publishing were not part of this run.
The app still requires manual publishing of exported MP4s.

---

# Historical verification — Telugu Reel Studio 0.1

Date: 8 October 2026. Tests executed in this delivery environment, not on the user's PC.

## Passed

- **10 Python integration/workflow tests**, run with Python 3.12 and real FFmpeg.
- Immutable job snapshots, approval tied to revision, no-op saves, and stale-save rejection.
- Human review notes required before approval; missing narration/captions rejected.
- Overlapping captions and stale uploaded-narration bindings rejected.
- Local write token / origin checks; saved provider secrets omitted from API responses.
- Online providers disabled by default; research without grounding sources rejected.
- Pixabay 24-hour search cache and download-host checks (provider replies mocked).
- Daily Gemini request cap; exclusive job claim and failed-job retry.
- Automatic footage selection preserves assigned media and reports missing keywords.
- Excessive duration-fit speed changes rejected rather than cutting the script.
- **Real 1080×1920 H.264/AAC MP4 export**, combining an explicit title card and uploaded
  test-pattern video, burned Roman captions, diagnostic narration audio, and music ducking.
- Export dimensions, audio presence, measured duration, full media decode, SRT creation,
  and reuse of unchanged rendered-scene caches checked.
- A rendered frame was visually inspected: title and Roman captions were present and legible.
- React/TypeScript production bundle built successfully with esbuild.
- **React DOM smoke test against a running API/worker**: new project, script edits,
  save, approval, visual change, revised approval, render queue, and visible offline
  provider failure. No React runtime errors in this flow.

The audio in the automated render test was a generated diagnostic tone, not Telugu
speech. Its purpose was to validate stream composition and timing, not voice quality.
The stock provider test used mock metadata, not a live Pixabay download.

Python test result: `10 passed, 1 warning in 3.88s`. The warning concerns the test
client's deprecated httpx integration; it did not affect the passing checks.

## Not verified

- Live Gemini, Google Search grounding, Pixabay, Azure, or Edge speech requests.
- Actual account access, API models, current quotas, charges, or provider entitlement.
- Telugu pronunciation and expressive narration quality.
- Native Windows batch execution or Python 3.14 compatibility on the target machine.
- i5-13400 render speed, memory use during long reels, and large-file behavior.
- A full Chromium/Edge browser test: the browser binary download failed in this
  environment. DOM testing does not verify browser layout, media playback, file-picker
  interactions, or platform rendering differences.
- Forced phonetic alignment. Automatic Roman caption timing is approximate and must
  be reviewed; manual scene-relative cues are supported.
- Cross-platform publishing or monetization; those actions are outside the app.

## Reproduce

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests -q
```

For the optional UI DOM test, run the app against a disposable `REEL_DATA_DIR`, install
frontend dependencies with `npm ci` in `frontend/`, and run `npm run test:ui` there.
This test creates a diagnostic project and intentionally queues an offline failure.
Keep online services disabled for that test. It is not a visual browser test.
