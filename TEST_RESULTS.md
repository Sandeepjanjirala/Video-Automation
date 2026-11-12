# API keys: .env defaults that Settings can change — 12 November 2026

Same branch. Requested change: the keys in `.env` should be the defaults, changeable from Settings (before, a key
in `.env` locked its Settings field with "Set in the .env file").

- `studio/config.py`: a key typed in Settings (stored in `data/settings.json`) replaces the `.env` default;
  `use_default_keys` in a Settings save drops it again. Only typed keys are stored; a stored copy identical to the
  `.env` key (written by earlier versions) counts as the default and is dropped on the next save. Public settings
  report each key's `_source` (`settings`, `env` or none) and `_env_default`; keys never reach the browser.
- Settings screen: every key field can be typed in. It says "Default key from .env in use" or "saved here", and
  **Use the default key from .env instead** appears where a typed key replaces a default.

Verification: the .env key test now covers default → typed key → other settings saved → back to the default; a new
browser-DOM test covers the Settings fields and what a save sends. Real Chrome against an isolated studio reading a
test `.env` with fake keys: defaults shown and editable, the fake keys absent from the page, a typed Gemini key
stored and shown as "saved here" (the untouched Pixabay default not copied), "use the default" cleared it again.

# Uploaded narration, one continuous soundtrack, and a release audit — 12 November 2026

Branch `feature/narration-upload-and-release-audit`. No new dependencies, no database migration (new project and
scene fields have defaults, so saved projects load unchanged). Scene renders are cached under a new key (renderer 7),
so the first render of an existing project re-encodes its scenes once.

**Uploaded narration**
- One recording for the whole video (`narration_mode: track`, scene `track_start`), or one per scene (scene
  `audio_trim_start/end`, `audio_gain_db`). `studio/narration.py` analyses a recording once (waveform, pauses with a
  threshold set from its own noise and speech levels, EBU R128 loudness), places scenes by text length at pauses or
  splits a recording without a script, and cuts 48 kHz PCM slices on exact sample positions. Uploads with
  `purpose=narration` must contain sound and decode to the end; videos and pictures are refused with a reason.
- Voice tab: **Narration** panel (narration.js) with the waveform, draggable scene lines (mouse or touch), typed
  times, "at playhead", per-scene play, narration end, volume, replace / remove / restore boundaries, and the
  per-scene list with trim and volume. Recordings need no Telugu text; editing a recording's text warns instead of
  detaching it. One recording cannot be assigned to several scenes (it would play in full in each).
- Durations come from decoded audio. Caption timing labels say where they come from: word times measured by the
  voice engine (Edge, Azure), an estimate spread over the speech between pauses (recordings, Sarvam; Sarvam's and
  Azure-fallback "word timings" were only the length shared out by characters and are no longer used as such), or
  the user's own. A change of a scene's audio resets only the captions it affects.

**Sound and export**
- `studio/soundtrack.py` builds the sound once: narration levelled to about -16 LUFS (one gain for a whole
  recording), joined as PCM, clip sound (new per-scene **Clip sound**, default silent) and music (ducking and
  fades, both switchable) mixed under it, a -1.5 dBFS limiter, one AAC encode. Scenes render picture only.
  Before, each scene's sound was loudness-normalised separately and its AAC joined with stream copy: the audit
  measured 28 irregular timestamp steps in a 10-scene export (gaps and overlaps at joins).
- Whole-video recordings play without padding; picture cuts go to the nearest frame (no drift).
- Export validation now checks H.264 yuv420p 30 fps, 48 kHz stereo AAC, equal sound and picture length and fast
  start, then decodes the file while measuring black picture, long silences and loudness (listed on the video's card).
- Quality check (preflight) warnings stay visible when a render is allowed: estimated caption timing, edited
  recording text, enlarged sources (over 1.5x), HDR clips, captions over two lines; recording placement errors name
  the scene. Telugu titles get a Telugu font; captions wrap only between words (no break inside a conjunct).

**Fixes from the audit** (two read-only audit passes; each finding verified in the code before fixing)
- Unsaved edits: the draft endpoint returned the bare project while the editor expected `{body, updated}`, so the
  "Unsaved draft found" banner never appeared; autosave now stores the base revision and the banner works (and
  reopens the project you had open). Applying a job result saves unsaved edits first and keeps the draft.
  "Prepare my script with AI" results merge field by field instead of replacing edited scenes; conflicts are
  listed (an override hid them); a footage result no longer turns a title card back into footage.
- Jobs: cancelling a render now stops every FFmpeg it runs (the job recorded one process of up to three); retry
  respects one job of a kind; the local worker survives a database restart and is restarted by the launcher.
  Unsaved caption timings are set aside when the scene's words or audio change under them.
- Security: Pixabay's key no longer reaches logs (httpx request lines); `.env` keys are not copied into
  `settings.json`; phone mode answers only to this computer's addresses (no DNS rebinding); FFmpeg paths in
  Settings must be real `ffmpeg.exe`/`ffprobe.exe` files; error messages show file names, not server folders.
- Install: `.env` is read before the database settings (`STUDIO_DATABASE_URL` in `.env` works); Docker requires
  `STUDIO_PASSWORD` (it locked every browser out without one) and passes `.env` keys to the containers.
- Files: re-uploading (or re-downloading a stock clip of) a file whose copy went missing repairs it; stock downloads
  are written to a `.part` file first; synthesis temporary files are removed (208 had accumulated in `data/cache`).

**Verification**
- Audit suite: **143 passed**, 0 failed (`scripts/run_audit_tests.py`): the 124 from `main` plus 19 new, and several
  existing ones extended (the caption-timing tests now expect the new labels and the recording warning). New tests: narration upload checks and analysis;
  placement at pauses; slices joining back sample for sample; a three-tone recording rendered with each picture cut
  and pitch change at the same moment; continuous timing over 50 scenes; recording trim and volume; preflight
  messages; caption resets; clip sound; music options; the narration panel in a browser DOM (jsdom).
- The older test files (`tests/test_django_m1`–`m8`, `test_phase3/4`, `test_workflow`) are not run by the runner
  and fail at fixture setup against the current schema (for example `NotNullViolation` on `projects.created_at`);
  unchanged here.
- Real Chrome (Playwright) against an isolated studio (own PostgreSQL database and data folder; free Edge voice
  and Pixabay only, no Gemini or Sarvam calls), three projects:
  - A: one recording for the whole video (22.6 s, Telugu speech with pauses and room noise), six scenes placed
    automatically: all six starts fell in the pause before their sentence (0.15–0.49 s ahead of the words); a
    clip with 30 % clip sound, a looping clip, a photo, a freezing clip, a low-resolution clip, a title card; music
    with ducking; a caption edited and saved; draft 540x960 (9 s) and final 1080x1920 (14 s); then a new take
    (26.4 s) re-placed (all in pauses), cues reset, rendered; unsaved edits offered back after a reload.
  - B: recordings per scene (MP3 trimmed to the speech, WAV +3 dB), the third scene read by the free voice,
    Pixabay footage found and applied, a half-copied M4A refused; draft and final.
  - C: free voice; a clip removed from disk blocks the render with its name; a damaged clip makes the render fail,
    and after repair **Retry** succeeds; a long render cancelled.
- Exports (FFprobe and a full decode): all H.264 High yuv420p 30 fps, AAC 48 kHz stereo, fast start, picture and
  sound within 1 ms of each other, no decode errors, no black picture or long silence; loudness -15.0 to -16.2
  LUFS, true peak -1.5 to -5.4 dBTP. Project A's final export follows the original recording with a constant 10 ms
  offset (one measuring step) at 0.5, 5.4, 10.9, 16.3 and 18.2 s (correlation 0.993–0.997 with music mixed in): no
  gaps, no drift. Frames checked by eye for every scene type (captions inside the frame and readable).
- Not verified: listening by ear (no audio review here: loudness, sync and continuity were measured instead); a
  real human recording (the "own recordings" were Telugu speech from the free Edge voice with phone-like silence
  and noise); Gemini, Sarvam and the one-click draft (quota and paid services not used); Docker (compose file only
  validated with `docker compose config`); a physical phone (390 px browser layout checked); HDR footage (only
  detected and warned about, not tone-mapped). Cancellation was proven by a unit test (two concurrent FFmpeg runs
  of one job stop within seconds); in the browser run the cancel landed between encodes.

# Footage that follows the script — 12 November 2026

Branch `feature/better-footage-match`. No new dependencies. New optional scene field `shot_weights` (defaults to
empty; no database migration, older projects and their cached renders are unchanged).

- `studio/footage.py` is a plan → search → check → search-again loop (the shape of a LangGraph agent, written
  directly so it needs no new framework and keeps the Gemini backup models and the daily limit):
  - Plan per part of the line: a scene needing N clips (`shots_needed`) is split by Gemini into N consecutive parts
    in spoken order, each with its words, an intended shot and 3 search phrases (older one-shot answers still work).
  - Every part is searched and checked on its own (`Scene i shot k of N: said while "…"`); a part shows 6
    candidates, later parts search their specific and broad phrase only.
  - Search again: shots with no accepted clip or a best score below 7 go back to Gemini with the phrases tried and
    the clips seen; the new phrases are searched (clips already seen are skipped) and checked; the better clip wins.
  - Clips are assigned in spoken order, one per part, never reused across scenes; the selection carries
    `shot_weights` (each part's share of the line, from the length of its words).
- `framing.planned_parts`/`shot_lengths`: the render shows each clip for its part's share (a part under 1.2 s joins
  the clip before it); equal parts as before without weights. Applying a footage search stores the weights; a
  manual clip (upload, library, single stock result, **Use only the main clip**) clears them.
- `scripts/run_audit_tests.py`: the loop removing key variables reused the name of the temporary database, so the
  runner could not drop it after the run (fixed; 8 earlier test databases, 72 MB, are left for you to remove).

Real check (Gemini + Pixabay, downloads skipped, projects not changed), last footage result → new loop:

| Project | Scenes with no clip | AI-matched scenes, mean score |
|---|---|---|
| Lake Natron ("My next story", 10 scenes) | 5 → 0 | 5, 7.2 → 10, 7.5 (4 found by the second search) |
| Human body (6 scenes) | 0 → 0 | 6, 8.8 → 6, 8.7 |

Long lines now get one clip per part in spoken order (e.g. the Lake Natron scene about birds and bats: three clips
for 23 %, 14 % and 62 % of the line). gemini-2.5-flash was over quota, so the backup gemini-3-flash-preview
answered; the runs then reached the app's daily Gemini limit (56), after which the keyword ranking was used as
designed (every scene still got a clip).

Verification: **124 passed** (121 previous + 3 new: a second search keeps the better clip, one clip per part of a
long line in spoken order with its share, render timing of planned parts; the AI-pick, extra-shot apply and real
render tests now also cover the second search, stored/cleared shares and per-part cut times).

# API keys from .env and the first Sarvam video — 12 November 2026

Same branch. No database changes.

- `studio/config.py` reads `.env` (git-ignored) at start: `STUDIO_GEMINI_KEY`, `STUDIO_SARVAM_KEY`,
  `STUDIO_PIXABAY_KEY`, `STUDIO_PEXELS_KEY`, `STUDIO_AZURE_KEY` override keys saved in Settings; `STUDIO_ONLINE=1`
  starts online services on. Public settings report `<key>_from_env` (never the key); Settings disables those
  fields. `.env.example` (committed) has the names with empty values. `scripts/run_audit_tests.py` sets
  `STUDIO_ENV_FILE=` and drops key variables, so tests never use real keys.
- One-click draft: footage search also records a paid voice (Sarvam, Azure) in the background
  (`prefetch_voice`), since that run records the voice right after footage anyway; a manual footage search still
  records only the free voice.
- Checked live: both keys valid (Gemini lists models; Sarvam returns Telugu audio); the app's Sarvam request
  (`bulbul:v3`, speaker `shubh`) answers in 1.2 s.

Verification: **121 passed** (120 previous + 1 new: .env keys override Settings, are never exposed, are not loaded
by the test runner; plus the paid-voice prefetch case in the footage test). Real Chrome: a 6-line script, Voice tab
**Upgrade to Sarvam voice**, then **Create draft video automatically** → 16.8 s 540×960 draft in 84 s (AI 7 s,
footage 69 s, voice 1 s recorded during footage, render 4 s); all six narrations are Sarvam WAV (24 kHz).

# Faster video creation — 12 November 2026

Same branch. No new dependencies, no database changes.

Measured on real one-click runs of 7-scene reels (job times from the database), before → after:

| Step | Before | After |
|---|---|---|
| AI scene preparation (Telugu, captions, search words) | 25 s | 7–8 s |
| Footage (plan, search, AI clip check, downloads) | 128 s | 50 s |
| Voice | 26 s | 1 s (recorded during footage) |
| Draft render | 13 s | 4–6 s |
| **Whole one-click draft** | **about 195 s** | **65 s** |

- `providers.gemini(fast=True)`: `thinkingBudget: 0` (2.5 Flash) or `thinkingLevel: low` (Gemini 3 and
  `-latest` models); a model answering HTTP 400 is asked again without it. Used for scene preparation,
  per-scene Convert to Telugu, shot planning and the clip check (planning 16 s → 5.5 s and clip check
  35 s → 19 s on the same scenes, with equivalent picks; the normal planning answer was malformed JSON).
- `footage.find`: searches for all scenes in parallel (4 workers; `stock.paced` still spaces requests),
  planning and clip-check batches 2 at a time, clips assigned in scene order then downloaded 4 at a time.
- `media.prefetch_voices`: 4 narrations at once in the voice and render jobs; the footage job records the
  free Edge voice in the background (never a paid provider).
- Render: scenes rendered 3 at a time (`RENDER_WORKERS`, from the CPU count), results kept in scene order.
- One-click progress is checked every second instead of every two.

Verification: **120 passed** (115 previous + 5 new: thinking settings and the HTTP 400 retry, parallel
narration with errors left to the scene loop, scene order of a parallel real render, overlapping downloads
with one clip per scene and a failed download reported, free voice recorded during footage but not a paid one).
Real Chrome: fresh volcano script 91 s (before fast footage AI), fresh space script 65 s; clips on topic.

# Simpler screens and a "How to use" guide — 12 November 2026

Same branch. No server changes.

- Story tab: **Your script** first (video format moved here, script format, paste box, Create draft video
  automatically, Split into scenes, Prepare my script with AI; how Split works in a fold), then **Your scenes**
  (each row: the two text boxes and Convert to Telugu; search words, Prompt, Move up, Remove, Upload narration in
  **More**), then folded **Project details & AI writing** and **Review notes**. Voice & visuals: Voice (free/Sarvam
  buttons, Prepare narration) with **Voice settings** folded; **Voice Lab** and **Background music** folded; each
  scene card shows its picture, a one-line summary, rights and captions, with **Change footage & framing** folded.
  Captions: help folded, each scene's timing editor folded with its status visible, **Caption size, position and
  preview text** folded. Preview & export: layout folded, **Ready to render?** and **Your videos** open.
  **Production jobs** folded on every tab; it opens by itself while a job runs or a result waits to be applied.
- `studio/static/js/ui.js`: fold-outs (`details.fold[data-fold]`) remember which ones the user opened in this
  browser (also across redraws of scene cards); opening by the app is not remembered. Approval without review
  notes opens them; "Edit captions & timing" and caption errors open that scene. `guide_modal.html`: a six-part
  guide (first time, reel, YouTube, changing things, phone, problems), opened from **How to use** buttons and once
  on a first visit.

Verification: **115 passed** (114 previous + 1 new DOM test: every former control still present, which parts start
open or folded, folds remembered across redraws, caption scene opening, review-notes jump, jobs panel auto-open
not remembered, guide first-visit/once/buttons/Esc). Real Chrome at 1440×900 and 390×844: every tab renders without
horizontal overflow or page errors (`test-outputs/folds/`).

# Automatic Telugu after splitting and highlighted caption key words — 12 November 2026

Branch `feature/auto-telugu-and-caption-highlights` (from `main`). No database migrations.

- **Split into scenes** now continues automatically: one fill job (the one-click draft's preparation step,
  `StudioAutopilot.prepareScenes`) writes every scene's Telugu narration, stock words, headings and caption
  key words; text the user wrote is kept. Without online services or a Gemini key, or when the job fails,
  the split scenes stay and the notice says what to do.
- **Scripts in Telugu letters** (with English words, e.g. "ఒక lake దగ్గరకు వెళ్లిన animals"): each such line goes
  to the Telugu narration box whatever the script format, and the Roman captions box (English letters only)
  is written from it. Before this fix the split put Telugu into the Roman box, the project could not be saved
  (the model rejects Telugu there) and the narration stayed empty. Saving, restoring an autosave and the fill
  job move Telugu letters found in the Roman box to an empty narration.
- **Highlight key words** (`Project.caption_emphasis`: `keywords` for new projects, `none` for existing ones;
  `Scene.emphasis`): `media.ass_text` colours key words and numbers with `{\1c&H00D7FF&}…{\r}` after
  wrapping (white on Yellow accent); words come from the AI (up to three, copied from the line, filler words
  dropped, asked only together with other missing fields), are editable per scene on the Captions tab, and
  fall back to the scene's stock search words. Same in drafts, final exports, exact-frame and caption
  previews and the live CSS previews. Projects without the option produce byte-identical subtitle files and
  keep their scene cache.

Verification: **114 passed** (109 previous + 5 new: Telugu letters in the Roman box become narration; highlighting on/off, wrapping unchanged, fallback words,
yellow-accent colour, byte-identical plain captions; AI key words filtered and no extra requests; yellow pixels
in a real render and caption preview only when on; DOM behaviour of automatic Telugu after splitting, its failure
and no-Gemini messages, the highlight switch, swatches and per-scene key words). Real Chrome: a 7-line Roman
Telugu script → Split into scenes → Telugu narration for 7 of 7 scenes in 27 s (English words written as spoken,
e.g. "లేక్ నేట్రాన్", "టెంపరేచర్ 60 డిగ్రీస్"), key words such as "Lake Natron Tanzania" and "60 degrees"; then the
one-click draft shows them in yellow in the video (`test-outputs/emphasis/`). A 7-line script in Telugu letters
with English words, default script format: each line in the Telugu narration box at once, Roman captions
("Oka lake daggaraku vellina animals...") for 7 of 7 scenes 16 s later, project saved (`telugu_split.py`).

# Phone, PC and deployment — 10 November 2026

Same branch, `feature/one-click-draft`. No database migrations.

- **Phone layout** (≤740 px): compact top bar with **☰** opening the project list, Sign out and
  Settings as a drawer (closes on choosing, tapping outside or Esc); the four steps as a fixed bottom
  bar; stacked section heads, 16 px inputs (no iPhone zoom), touch-sized buttons, full-screen dialogs
  with a sticky title and close button, a 270 px 9:16 preview, long messages wrapped. Desktop unchanged
  except the open tab is now underlined (`.active` had no style). Web app manifest and icons for
  **Add to Home screen**.
- **Fixed:** the Captions tab of a YouTube project could not measure narration (caption-timing validated
  a partial project without `format`, so more than 30 scenes failed) and the error widened the page.
- **Access from other devices** (`studio/access.py`, `AccessMiddleware`): the studio PC (loopback, not
  proxied) needs nothing; other devices sign in at `/login` with the password from Settings (PBKDF2
  hash, never sent to the browser) or `STUDIO_PASSWORD`; signed 30-day HttpOnly SameSite=Lax cookie
  bound to the password; 10 wrong passwords per address pause sign-in 15 minutes; refused entirely
  while no password is set. The secret key is no longer the fixed default: `DJANGO_SECRET_KEY` or a
  generated `data/secret_key` (git-ignored). `/login` uses `Referrer-Policy: same-origin` so browsers
  send its real origin to the cross-origin check (no-referrer made Chrome send `Origin: null`).
- **Deployment:** `START_PHONE_ACCESS.bat` (`STUDIO_LAN=1`) listens on the network and prints the
  address(es) to open; `STUDIO_ALLOWED_HOSTS`, `STUDIO_BEHIND_PROXY`; Compose passes `STUDIO_PASSWORD`;
  `docker-compose.https.yml` adds Caddy with automatic HTTPS (`docker compose config` validated).

Verification: **109 passed** (105 previous + 4 new: refused without a password and the proxied-loopback
case; sign-in, wrong password, open-redirect guard, cookie flags, forged cookie, password change signs out,
sign-out, guess limit, removal, referrer policy and foreign origin; server password from the environment;
secret key; plus the drawer in the projects DOM test). Real Chrome at 390×844 and 1440×900 on every tab
of a reel and a YouTube project: no horizontal overflow, no page errors (`test-outputs/mobile/after`).
Real phone access over the network address (`http://10.1.79.50:8788`, `START_PHONE_ACCESS` mode):
refused without a password → password set in Settings on the PC → sign-in page, wrong password
message, signed in with 31 projects, Sign out shown only on the phone, signed out → 401; test password
removed afterwards (`test-outputs/mobile/access`).

# YouTube video format (16:9, long-form) — 10 November 2026

Same branch, `feature/one-click-draft`. No database migrations (`format` is a field in the project JSON).

- **Video format** (`Project.format`: `reel` default, `youtube`; `studio/formats.py`, mirrored in `state.js`):
  YouTube renders 1920×1080 (draft 960×540), up to 400 scenes and 60 minutes (reel: 30 scenes, 3 minutes;
  model and timeline enforce both), a new shot about every 6 s, captions on a 1920×1080 ASS canvas with
  48-character lines, 200 px side margins and 28% of the reel's bottom margin. Framing windows (4:5, 1:1)
  centre horizontally in a landscape frame; Python and the browser mirror the rule.
- **Story tab**: Video format select, format-specific durations (reel 30 s–3 min, YouTube 5–30 min),
  header badge, sidebar label, export buttons, previews (16:9 stage, wider column, metadata-only video
  preload for long projects); switching to a reel is refused when the project has more than 30 scenes.
- **Long scripts**: script split up to the format's scene limit; scene preparation in batches of 30
  with the whole script as context; shot planning 40 scenes per request; clip check 16 scenes per request
  with six candidates each; Pixabay/Pexels requests paced (0.7 s / 0.4 s) and HTTP 429 waited out;
  landscape clips ranked first and 1080p files downloaded; one-click time limits grow per scene; project
  saves up to 8 MB; final decode check scales with length; AI storyboard draft writes a long-form script
  (up to 60,000 output tokens, one scene per ~5 s of target).

Verification: **105 passed** (94 previous + 11 new: format limits; 20-minute timeline; landscape captions;
real FFmpeg YouTube draft and final at 960×540 / 1920×1080 with format-aware exact-frame preview; landscape
framing windows and 6 s pacing; landscape ranking, 1080p file choice, batched planning and picking;
batched scene preparation; request pacing and 429 wait; long-form AI draft; a >1 MB project save; DOM
behaviour of the format switch). Real Chrome: a 42-line Roman Telugu YouTube script ("ocean secrets")
with **Create draft video automatically** → 39 scenes, 35 AI-checked landscape clips + 4 title cards,
2.9-minute 960×540 draft in 9.2 minutes, no scene longer than 6.3 s on one clip, no page errors; then
**Render 1920×1080 YouTube video** → 1920×1080, 173 s, 118 MB, in about a minute
(`test-outputs/autopilot/youtube-*.png`, `youtube_e2e.py`, `youtube_final.py`).

# Captions on/off and a new shot every ~4 seconds — 10 November 2026

Same branch, `feature/one-click-draft`. No database migrations (both are fields in the project JSON).

- **Captions switch** (`Project.captions_enabled`, default on): on the Captions and Preview & export tabs,
  kept in step. Off: the renderer writes no caption cues (title cards keep their heading), `captions.srt`
  is still exported, and the readiness check no longer requires Roman caption text.
- **Shot pacing** (`Scene.extra_shots`, up to 4 library clip IDs; `framing.scene_shots`): a video scene
  longer than ~4 s cuts between its own clip and its extra shots in equal parts (ceil(length / 4) clips);
  extra shots play from their start, cropped to fill the frame. `footage.find` estimates each scene's
  length (0.6 s + Roman words / 2), tells Gemini how many clips a scene needs, and downloads that many
  accepted, unused clips. Footage search sets the extra shots; an upload, library choice or single stock
  result clears them; the scene card shows **+N more shot(s)** with **Use only the main clip**. Preflight
  reports a missing extra shot. The source report lists the extra shots used.

Verification: **94 passed** (88 previous + 6 new: a real FFmpeg render cutting red/green/blue clips at
3 s each for 9 s of narration and keeping one clip for 2 s; `scene_shots` framing and image scenes; extra
shots applied by footage search, cleared by assign-media, missing-clip preflight; captions off in preflight
and rendering with the SRT kept; footage downloads extra shots for a long scene; DOM behaviour of the switch
and the extra-shots line). Real Chrome on the 10-line facts script: 14 clips in a 41.8 s draft, every clip
on screen 1.9–3.9 s (before: up to 6.9 s), detected cuts within 0.02 s of the plan, all clips on topic
(`test-outputs/autopilot/C-pacing-clips.png`). Captions turned off with the switch, approved and rendered:
same frames without captions, SRT still exported (`captions-on-vs-off.png`).

# Gemini backup models and project delete — 10 November 2026

Same branch, `feature/one-click-draft`. No database migrations.

- **Backup Gemini models** (`providers.gemini`, setting `gemini_fallback_models`, Settings field):
  on HTTP 429, 503 or 404 the next model is tried; other errors stop at once; only the last
  model waits out a 503 or short per-minute limit; every HTTP attempt counts toward the daily limit.
  Default order chosen from the models this key can use on the free tier (probed 10 November):
  gemini-3-flash-preview, gemini-3.1-flash-lite, gemini-flash-lite-latest, gemini-3.5-flash
  (gemini-2.5-flash-lite and gemini-2.5-pro: retired for new users; gemini-3.5-flash-lite: wrong Telugu word).
- **Delete project** (`DELETE /api/projects/<id>`, `services.delete_project`, allowed in the mutation
  middleware): removes the project, revisions, autosave, voice takes, jobs, job stages, receipts,
  outbox events and export folders; library assets stay; refused (409) while a job is queued or running.
  UI: a bin per sidebar project (hover/focus) and **Delete** in the header, one confirmation; deleting
  the open project opens the next one, or the welcome screen after the last.

Verification: **88 passed** (84 previous + 4 new: backup models on 429/404, no backup on 400 and the
all-busy message; delete with history/jobs/exports/assets/other projects and the 409 refusal; DOM
behaviour of both delete buttons). Real API: main model set to retired gemini-2.5-pro → answered by
gemini-3-flash-preview ("తేనె"). Real Chrome: two throwaway projects deleted from the sidebar bin and
the header, both confirmations shown, API returns not-found for both, all other projects unchanged,
no page errors (`test-outputs/autopilot/delete_e2e.py`).

# Footage matched to the script — 10 November 2026

Same branch, `feature/one-click-draft`. No database migrations.

- `studio/footage.py`, used by "Find footage for all scenes" (`stock_all`) and the one-click draft:
  Gemini plans the shot for every scene from the whole script with three search phrases; every
  phrase plus the scene's subject word is searched (20 results each); phrases take turns in a
  candidate list ranked by the provider's order, subject-weighted tag match, film over animation,
  resolution, vertical shape and popularity (green screen and "subscribe" overlays last); Gemini
  then scores preview frames of ten candidates per scene and each scene gets its best clip not
  already used (none scoring 5 or more: title card). Without Gemini: keywords and ranking only.
- Stock search returns preview frames (provider image hosts only), film/animation and quality flags,
  and the sharpest file whose download stays under 60 MB (4K for landscape footage cropped to 9:16).
- Scene preparation asks for concrete, filmable search words instead of abstract ones.
- `providers.gemini` sends images and waits out a per-minute rate limit ("retry in 12s"), not a daily one.

Verification: **84 passed** (79 previous + 5 new: stock search fields and file choice, planning and
AI picking with unique clips per scene, ranking without Gemini, the `stock_all` job, Gemini image
parts and rate-limit retry). The 233-test full suite fails the same 30 legacy milestone tests on
`main` and on this branch (legacy SQLite parity, Alembic revision, database roles, old Sarvam voice
names, fakes without the `provider` argument); none are new.

Real Chrome with Gemini and Pixabay on a 10-line facts script: before, 4 clips were unrelated
(heartbeat line for the octopus, spider, animated "Shocking" text, a likes graphic). After: all 10
scenes AI-checked at 6–10/10 (two vertical octopus clips, honey dripping into a jar, Venus,
reaction shots for hooks, a phone for "Comment cheyyandi"); 41.8 s draft, no page errors.
Contact sheet: `test-outputs/autopilot/C-final-clips.png`.

# One-click draft video — 10 November 2026

Branch `feature/one-click-draft` (from `main`). No database migrations.

- **Create draft video automatically** (Story tab, `studio/static/js/autopilot.js`): chains the
  existing jobs: scene preparation → `stock_all` → rights, save, approve → voice → draft render.
  Asks once for a review note when the project has none; stops at the first failed step.
- AI fill mode for existing scenes (`providers.fill_scenes`, draft job option `fill`): one Gemini
  request completes only what is missing (Telugu narration, Roman captions, stock search phrase,
  heading); text the user wrote is kept; a scene still without Telugu is retried with the
  per-scene converter. Chat formatting (`**`, `#`, `Title:` labels) is removed by
  `providers.clean_caption`, mirrored in "Split into scenes" (`Scenes.cleanCaption`). Pasted scripts
  are split by their own lines, so no line is dropped or merged.

Verification: **79 passed** (72 previous + 7 new): caption cleaning; fill keeps user text,
regenerates stale narration, makes no model call when nothing is missing, rejects unusable output,
retries a scene with the converter; fill job applies without touching visuals or framing; DOM
behaviour of the button (step order, single confirmation, review-note prompt, title-card fallback,
rights, approval, failure stop, cancel). Real Chrome with Gemini, Pixabay and Edge Mohan:
a ChatGPT-style facts script (7 lines with `Title:` and `**`) → 7 scenes with Telugu narration and
search phrases such as "octopus three hearts" → 28.2 s 540×960 draft in one click; a script split
locally first → 3 scenes → 8.3 s draft. Both MP4s decode; no page errors. Artifacts:
`test-outputs/autopilot/`.

# Stage 5: final verification and PR preparation — 9 November 2026

Verification of the complete Django + jQuery application, plus the fixes it led to.
No existing projects, data, settings or branches were changed or deleted; the local
`main` branch was not touched. Artifacts: `test-outputs/stage5/` and
`test-outputs/stage5/pr-prep/`.

Fixes made during verification:
- **Translucent caption halo**: libass also outlines BorderStyle 4 text, which drew a dark
  halo around translucent captions. Its OutlineColour is now fully transparent; the other
  three presets are byte-identical.
- **Telugu shaping on FFmpeg 7.1**: Debian 13's FFmpeg 7.1 `subtitles` filter laid Telugu
  out without complex shaping (broken conjuncts). Captions are always ASS, so exports and
  previews now use the `ass` filter (`media.CAPTION_FILTER`), which shapes by default on
  every version; on FFmpeg 9 its output is pixel-identical to before. Scene render cache
  version bumped so no scene rendered with the old filter is reused.
- **Docker**: base image moved to Debian 13 (FFmpeg 7.1.5, libass 0.17.3, Noto Telugu
  fonts); new `test` build target with Node.js 24.22.2 and dev dependencies.
- **Tests**: the shaping test compares the export filter with `ass` complex/simple shaping
  (works on FFmpeg 5.1–9); style distinctness is checked on the bright clip only (a black
  box on the near-black title card is barely distinguishable from an outline).

Results:
- Windows: **72 passed** (isolated runner); `manage.py check` clean; no migration
  changes; `migrate --plan` on the working database: nothing pending. `doctor.py`:
  FFmpeg 9.0.2 with libass/HarfBuzz/FriBidi/libx264, loudnorm, sidechaincompress; Nirmala UI.
- Docker (`docker build --target test`, run on a private network with a temporary
  PostgreSQL container; the working database and running app were not used): **72
  passed**, including the DOM tests; `manage.py check` clean; no migration changes.
  Telugu captions in all four styles rendered with joined conjuncts; EXIF photos and
  rotated videos upright.
- Real Chrome, full workflow (projects `5a83638c…` and, after the fixes, `64fc40ae…`):
  Roman script → scenes → Gemini and reviewed manual Telugu; prompt saved and copied;
  Pixabay search, download and apply; video and EXIF-photo uploads; crop, zoom, drag,
  position, trim from the playhead with loop (playback stayed in 1.0–2.45 s), hold last
  frame, 4:5 photo; Edge Mohan narration (3.792, 3.312, 3.264 s); Telugu reset, split,
  merge, seek/stop, overlap warning blocking save, project-switch and leave-page guards;
  exact previews of all four styles; black-box draft and final exports.
- MP4 checks (draft and final): H.264 30 fps, AAC 48 kHz stereo, uniform BT.709
  limited-range tags, 10.42 s vs manifest 10.40 s, contiguous scenes, manifest durations
  equal measured narration, SRT equals manifest, −16.1 LUFS, every scene audible; all 7
  captions vs exact FFmpeg preview: draft 30.6–37.6 dB, final 30.2–41.2 dB; loop restart
  38.0/42.9 dB; held frames identical. Final reel:
  `test-outputs/stage5/pr-prep/FINAL-TEST-REEL-1080x1920.mp4`.
- All four styles re-exported in Chrome after the fixes: classic, black box and yellow
  unchanged; translucent export vs exact 36.2 (draft) / 37.6 (final) dB.
- Existing projects: all 20 earlier projects opened on every tab without changes; the
  13 without a caption style show Classic; the oldest rendered a valid draft.
- Failure recovery: a Sarvam job without a key failed before any request with an
  actionable message; "Re-run with current" recovered it after switching back to Edge;
  a cancelled render left no export; the next render succeeded. No page errors.

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
