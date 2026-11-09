# Stage 4: caption style presets — 9 November 2026

Implemented in Django + jQuery on `improve/free-preview-editor`, on top of the
uncommitted Stage 1–3 work. No commits, pushes, merges or deletions. No database
migrations (projects are JSON). Stage 5 was not started.

- `Project.caption_style`: `classic` (default; projects saved before Stage 4 load as
  classic), `black_box`, `yellow_accent`, `translucent`. Invalid values return 422.
- `media.CAPTION_STYLES` / `caption_style_line`: ASS presets for both the Arial (Roman)
  and Telugu-font styles. Classic output is byte-identical to the previous header.
  Black box uses BorderStyle 3 (box per line), translucent BorderStyle 4 (one 60%-opaque
  panel per caption); box padding is a quarter of the caption size. The scene render
  cache key now includes the style.
- `POST /api/projects/<pid>/caption-preview`: one exact JPEG frame with a caption in the
  requested style, for media scenes (Stage 3 framing pipeline) and title cards (drawn as
  the renderer does). `framing-preview` also honours `caption_style`; its Stage 3 rules
  (title cards rejected) are unchanged.
- UI: Caption style panel on the Captions tab (four swatches, size/margin synced with the
  Export tab, scene and preview-text choice, live preview over the scene's FFmpeg frame,
  Render exact caption). `captionstyle.js` mirrors the presets in CSS and is used by the
  Stage 3 scene previews.
- Fixed: `button[hidden]` was shown because the global button rule overrode `hidden`
  (affected Stage 3's "Back to live preview" and the new panel).

Verification: **72 tests passed in 64.12 seconds** (63 previous + 9 new): classic header
unchanged and old projects stay classic; preset fields, size, margin and fonts; Telugu
complex shaping in every style (default render identical to `shaping=complex`, different
from `simple`); exact caption preview vs draft and final exports for all four styles on a
video scene and a title card (caption band PSNR 40.0–45.2 dB; wrong style 14.9–16.4 dB;
styles distinct in exports); style picker DOM behaviour.

Real Chrome (`test-outputs/stage4/`, project `8130c2a4f1c1462c80a705171a4ce463`): Edge
Mohan narration; scene 1 captions reset to Telugu script in the caption editor and saved;
each style selected in the panel, saved, approved and exported as draft and final. Export
vs exact caption (draft/final): classic 37.97/39.63 dB; black box 33.42/34.8 dB; yellow accent 36.64/36.86 dB; translucent 34.02/35.74 dB; title card 38.8–44.9 dB. Final-export caption
bounding boxes equal the exact preview's in every style; live browser captions are within
4–16 px of 1080. Cues, size and margin unchanged after every style change. No page
errors; no horizontal overflow at 390 px. The Stage 3 Chrome workflow was re-run after
these changes with identical results.

# Stage 3: accurate video and image framing previews — 6 November 2026

Implemented in Django + jQuery on `improve/free-preview-editor`, on top of the
uncommitted Stage 1 and Stage 2 work. No commits, pushes, merges or deletions.
No database migrations were needed (scenes are JSON). Stage 4 was not started.

- `studio/framing.py`: shared geometry (frame shape window, fill/contain, zoom,
  position → even-pixel scale/crop/pad), FFmpeg filter chain, display-size probe
  (rotation from video side data and EXIF, non-square pixels), trim segments, scene
  input planning and one-frame previews. `studio/static/js/framing.js` mirrors the
  geometry for the live preview.
- New scene fields with backward-compatible defaults: `frame_aspect`, `zoom`, `crop_y`,
  `trim_end` (validated: zoom 1–3, positions 0–1, trim end after trim start).
- Renderer: uses the shared chain; trimmed loops are cut and framed once, then looped
  (fixes loops restarting at 0 s); freeze holds the trim end; zoomed sources are cropped
  before scaling. Scene cache version bumped to 5.
- Bug found and fixed during verification: full-range BT.601 JPEG scenes switched the
  joined reel's colour range/matrix mid-stream. All scenes are now BT.709 limited range
  with identical tags; preview JPEGs are converted for JPEG viewers.
- APIs: `GET /api/assets/<id>/framing` (picture size FFmpeg sees) and
  `POST /api/projects/<pid>/framing-preview` (exact JPEG frame for an unsaved scene,
  geometry in `X-Framing-Geometry`; added to the mutation allowlist; never saves).
- Preflight: trim errors use the shared segment rules; a trim end past the clip is a warning.
- UI: scene cards show a 9:16 live preview (drag to pan, caption position guide,
  trimmed playback with loop/hold), frame shape, framing, zoom, position, trim with
  playhead buttons, and Render exact frame. Choosing new media resets trim end.

Verification: **63 tests passed in 42.51 seconds** (56 previous + 7 new) with the
isolated audit runner. New coverage: 864 geometry cases identical in Python and the
browser module plus jsdom editor behaviour (sliders, drag, trim validation, loop/hold
playback, exact-frame request, reset, all-digit scene IDs, legacy scenes); rotation,
EXIF orientation and pixel-aspect detection; exact preview vs exported MP4 for draft
and final (quarter-size PSNR > 28 dB, wrong-crop negative control, single colour
range/matrix across the reel); source frame identity between preview and export
under trim; trimmed loop/hold colours; field validation and preflight.

Real Chrome (`test-outputs/stage3/`, project `2468b46ff5764d05b13c004279f874d1`): four
scenes (landscape video, rotated phone video, EXIF photo, square PNG) with Edge Mohan
narration. Rotations detected; drag panned; trim set from the playhead; playback looped
within 1.50–4.00 s. Export vs exact preview (caption band excluded): draft 38.4, 32.6,
33.2, 38.6 dB; final 38.5, 33.1, 31.8, 42.5 dB. Loop restart matched the trim start
(37.7/38.2 dB); held last frames identical (≈80 dB). Live browser preview vs exact
frame 27–35 dB (screenshot scaling and browser colour conversion). Side-by-side images
`compare-scene1..4.png` show identical framing. No page errors; no overflow at 390 px.

# Stage 2: Telugu caption editor — 6 November 2026

Implemented in Django + jQuery on `improve/free-preview-editor`, on top of the
uncommitted Stage 1 work. No commits, pushes, merges or deletions were performed.
No database migrations were needed. Stage 3 was not started.

- New **Captions** tab (`studio/static/js/captions.js`, `tab_captions.html`) replacing
  the raw JSON cue textarea. Per-scene audio player with playhead, editable start/end
  (`1.25` or `0:01.25`) and Telugu/Roman text, Play (seeks to the caption and stops at
  its end), Start/End = playhead, Split (at playhead or midpoint), Merge with next,
  Delete, Reset to automatic timing (from Roman captions or Telugu narration), and
  Discard. Edits are drafts until **Save captions**; unedited automatic Roman timing
  is kept automatic so its alignment label stays accurate.
- Measured timings: `POST /api/projects/<pid>/caption-timing` (`studio/captions.py`)
  reports, for the editor's current possibly unsaved body, the ffprobe-measured length
  of the audio the render will use — an attached recording, or prepared narration whose
  speech inputs (text, voice, rate, preset, dictionary, take) still match. Synthesis is
  contract-cached, so this is the same file the renderer reuses. It never synthesizes
  and never saves. Voice jobs now store word boundaries for resets; older jobs fall back
  to text-proportional estimates over the measured length. Prepared-narration audio is
  now served with HTTP Range support (needed for seeking) and the correct WAV/MP3 type.
- Validation: invalid times, end ≤ start, start after the narration, end past the
  narration plus the renderer's existing 100 ms tolerance, empty or >160-character text,
  >200 captions and overlaps are errors that block saving; captions under 0.3 s and
  unmeasured scenes are warnings. The server model still rejects overlaps.
- Unsaved-change protection: browser leave-page prompt, confirmations before switching
  or creating a project, approving, preparing narration or rendering with unsaved
  caption edits; failed saves keep drafts and restore the project's previous captions;
  captions cleared by a text/narration change can be restored once for review.
- Telugu-script captions are now accepted (blank text rejected). They use a `Telugu`
  ASS style (Nirmala UI on Windows, Noto Sans Telugu elsewhere; `fonts-noto-core`
  added to the Dockerfile — the Docker image was **not** rebuilt or tested). Roman
  captions keep the unchanged `Default` style, so existing renders and caches are
  unaffected. The Stage 1 timeline, SRT and timing manifest consume saved cues unchanged.

Verification: **56 tests passed in 35.12 seconds** (49 Stage 1 + 7 new) using the
isolated audit runner. New coverage: Telugu/blank/overlap cue validation; measured
prepared narration with word-index automatic cues, stale detection after unsaved
narration or speech-rate edits, no synthesis while measuring, and 206 Range audio;
measured attached recordings and hash mismatch; Telugu ASS style; full HTTP workflow
(prepare narration → save edited Telugu captions → overlap rejected → approve → draft
and final renders) checking dimensions, manifest/SRT agreement with saved and automatic
cues, measured durations and full decode; and a jsdom regression of the editor
(parsing, validation, split/merge/reset/discard/restore, playback seek, save blocking,
leave/switch/approve/render guards, failed save). Artifacts:
`test-outputs/studio_audit_0579e741f5e3/`. Django checks, migration drift check and
git diff check passed.

Real Chrome workflow (`test-outputs/stage2/`): new two-scene project, manual reviewed
Telugu, video/PNG uploads, approval, live free Mohan narration (no paid calls), then in
the Captions tab: "Not measured" before narration; measured 3.648 s / 2.88 s afterwards
(browser audio duration identical); Telugu reset; split at playhead; Play seeked to
1.117 s and stopped at 2.228 s for a caption ending at 2.21 s; End = playhead; invalid
and overlap warnings with save blocked; merge; project-switch confirmation dismissed with
edits intact; save + approve; 540x960 draft and 1080x1920 final exports whose manifests
match the saved cues and measured durations exactly; SRTs contain Telugu and Roman text;
both MP4s fully decode. Final-frame inspection shows correctly shaped Telugu
("నమస్కారం, మన", conjuncts formed) and Roman captions. Leave-page prompt confirmed.
Mobile 390px: no horizontal overflow. Browser page errors: zero.

Notes: repeated workflow runs while fixing the script left five diagnostic projects
titled "Stage 2 caption editor verification"; the final one is
`3cbdf621a3de4edc961f82d56111f7f5`. They were not deleted. The server and worker were
restarted (no jobs pending) and are running at http://127.0.0.1:8788. Automatic
alignment remains word-index mapping or estimates and still needs review; this is not
forced alignment.

---

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
