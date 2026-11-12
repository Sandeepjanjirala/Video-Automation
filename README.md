# Telugu Reel Studio

A local workspace for turning your researched script into a vertical Instagram Reel or a
long-form 16:9 YouTube video: script and Roman Telugu captions → footage → narration →
draft review → MP4 export.

**Publishing is not implemented.** Download the final MP4 and upload it to Instagram or
YouTube yourself.

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
6. Put your API keys in a `.env` file next to the project, once per computer: copy
   `.env.example` to `.env` and fill in `STUDIO_GEMINI_KEY`, `STUDIO_SARVAM_KEY`,
   `STUDIO_PIXABAY_KEY` (and optionally Pexels/Azure). `.env` is ignored by Git, so your keys
   are never pushed and `git pull` never changes them; keys in `.env` are used instead of keys
   typed in Settings, and Settings shows "Set in the .env file" for them. Never put real keys in
   `.env.example` or any other file in the repository. FFmpeg paths stay in Settings.

The launcher checks database migrations before starting. Keep its terminal open
while jobs run. This is a single-user app using Django's development server.

## Use it on your phone

The studio runs on your computer (or a server); a phone, tablet or another computer uses it in
the browser. The screens adapt to the phone: a top bar with **☰** for your projects and
Settings, the four steps as a bottom bar, full-screen dialogs and touch-sized controls. The
computer does the AI, downloads and rendering, so it must stay on and awake while a video is made.

**Same Wi-Fi (home or office):**

1. On the computer, open **Settings & providers** and set **Password for phone and other
   devices** (at least 8 characters). The computer itself never asks for it.
2. Start the studio with `START_PHONE_ACCESS.bat` instead of `START_WINDOWS.bat`. Its window
   prints the address to open, such as `http://192.168.1.20:8788`. Allow Python through Windows
   Firewall for private networks when Windows asks.
3. On the phone (same Wi-Fi), open that address and sign in. The phone stays signed in for
   30 days; **☰ → Sign out of this device** signs out. Use the browser's **Add to Home screen**
   to open it like an app.

Without a password, other devices see "Access from other devices is off" and can do nothing.
Changing the password signs every device out; ten wrong passwords from one device pause
sign-in for 15 minutes. **Turn off access from other devices** in Settings removes the
password. This is plain HTTP inside your own network; do not forward the port on your router.

**Away from home:** install [Tailscale](https://tailscale.com) (free) on the computer and the
phone, start with `START_PHONE_ACCESS.bat`, and open the computer's Tailscale address
(`http://100.x.y.z:8788`). Traffic stays encrypted inside your private Tailscale network.

**On a server** (cloud VM with Docker, 4+ CPU cores recommended for rendering): point a domain
at the server, open ports 80 and 443, then run

```bash
STUDIO_DOMAIN=studio.example.com STUDIO_PASSWORD='a long password' \
  docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build
```

Caddy obtains the HTTPS certificate and forwards to the studio; every browser signs in with
`STUDIO_PASSWORD` (Settings then shows the password as set by the server). Without the HTTPS
file, `docker compose up` keeps the studio on the server's own `127.0.0.1:8788`, also behind
the password. Each installation generates its own secret key in `data/secret_key` (or set
`DJANGO_SECRET_KEY`); `STUDIO_ALLOWED_HOSTS` adds host names, and `STUDIO_BEHIND_PROXY=1`
trusts an HTTPS proxy's headers.

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

**❓ How to use** (top of every page, in the sidebar, and on each main panel) opens a short
step-by-step guide in plain words; it also opens once on the first visit in a browser. The
screens show the main steps first: **Your script** (video format, paste, Create draft video
automatically, Split into scenes) and **Your scenes** on the Story tab; the picture of each
scene on Voice & visuals; caption on/off, highlight and style on Captions; **Ready to render?**
and **Your videos** on Preview & export. Everything else is one click away in a bar marked
**▸**: Project details & AI writing, Review notes, each scene's **More** and **Change footage &
framing**, Voice settings, Voice Lab, Background music, each scene's caption timing, caption
size and position, and Production jobs (which opens by itself while work runs or a result
waits for **Apply result**). Bars you open stay open in that browser. No option was removed.

### Video format: Instagram Reel or YouTube video

Every new project is an **Instagram Reel**. Choose **Video format** on the Story tab to make a
**YouTube video** instead; the choice is saved with the project and can be changed until the
project has more scenes than a reel allows.

| | Instagram Reel (default) | YouTube video |
|---|---|---|
| Frame | vertical 9:16, final 1080×1920, draft 540×960 | landscape 16:9, final 1920×1080, draft 960×540 |
| Length | up to 3 minutes, 30 scenes | up to 60 minutes, 400 scenes (target 5–30 minutes) |
| New shot | about every 4 seconds | about every 6 seconds |
| Stock footage | vertical clips ranked first; 4K files for sharp 9:16 crops | landscape clips ranked first; 1080p files |
| Captions | 32 characters a line, bottom margin as set | 48 characters a line, lower (28% of the reel margin), wider side margins |

Everything else works the same way, including **Create draft video automatically**, footage
matching, the captions switch, framing previews (shown in 16:9) and approval. For a long
script the AI steps run in batches: scene preparation 30 scenes per Gemini request, shot
planning 40 scenes per request and the clip check 16 scenes per request (six candidates each),
so a 20-minute script of about 240 scenes needs roughly 30 Gemini requests; the backup models
in Settings spread these over several free quotas. Pixabay searches are paced to its
100-requests-a-minute limit, and the one-click run allows more time per scene. A 20-minute
video downloads a few hundred clips (several gigabytes in `data/assets`) and takes about an
hour on a typical laptop; keep the page open. **Generate AI storyboard draft** writes a
long-form script for a YouTube project (about one scene per 5 seconds of the target).

### How fast it is

A 7-scene reel goes from pasted script to draft in about a minute (65–90 s measured, from about
3 minutes 15 seconds before these changes):

- Gemini's slow "thinking" is turned off where it adds nothing: writing Telugu and captions,
  planning shots and checking clips (each 2–3 times faster; a model that rejects the setting is
  simply asked again normally). Research and long-form AI storyboards keep full thinking.
- Footage: every scene is searched at the same time (still spaced to Pixabay's rate limit),
  long videos' AI checks run two at a time, and clips download four at a time.
- The free Mohan voice is recorded while footage is being found, so the voice step usually takes
  a second; paid voices are only recorded in the voice step. Four scenes are recorded at once.
- Scenes are rendered three at a time (on a computer with 12 or more cores), in scene order.

No new software is needed for this. FFmpeg's Intel Quick Sync encoder works on this PC, but
encoding is a small part of the time, so the tested libx264 encoder is kept.

### One click to a draft

Create a project, paste your script into **Quick script importer** (or keep scenes you
already split or wrote), and click **🚀 Create draft video automatically**. It asks once
for a review note (prefilled "Original script by me."), then:

1. splits a pasted script by its own lines and sentences, removing chat formatting such as
   `**bold**`, `# headings` and `Title:` labels;
2. completes every scene with one Gemini request: Telugu narration (English words written
   in Telugu script as pronounced), Roman captions for Telugu-only scenes, an English stock
   search phrase chosen from the scene's meaning, and a heading. Text you wrote is kept;
3. matches stock footage to the script (see **How footage is matched** below; a title card
   if no clip fits);
4. ticks the rights check on every scene, saves and approves;
5. prepares the free Edge Mohan narration and renders a **540×960 draft**.

Progress appears under the button and in **Production jobs**; a failed step stops the run
with its message, and running it again reuses finished work. Watch the draft, adjust any
scene, caption or style if needed, then click **Render 1080×1920 Reel** on **Preview &
export**. The steps below describe the same workflow done by hand.

### Step by step

1. Create a story and paste your script into **Quick script importer**.
2. Select **Telugu written in English letters** (default) or native Telugu.
   **Split into scenes** splits locally: a line in Telugu letters (English words are fine)
   becomes the Telugu narration, a line in English letters the Roman captions. Then (with
   online services and a Gemini key) it fills the other box automatically with one AI request, together with
   stock search words, headings and caption key words; text you wrote is kept. Without
   Gemini, or if that request fails, the split scenes stay and a message says so. Each
   scene preserves your Roman Telugu input/captions separately from its native Telugu
   narration. **Convert to Telugu** on a scene opens a review window: use your configured
   Gemini or Sarvam text API, or paste Telugu manually. Apply only after checking names and pronunciation. Editing a
   converted Roman source requires another narration review before export.
3. Add your research/evidence notes, or identify the script as original fiction.
   Save and approve the current revision.
4. Upload a video or JPEG/PNG/WebP image, reuse library media, find free stock,
   or select title cards. Automatic stock search uses Pixabay first, then Pexels
   when no results are found (or only Pexels is configured). Each scene also has
   a source selector. Provider errors are shown; choose the other source explicitly
   if credentials or quota fail. Review relevance and usage rights.

   **How footage is matched** (**Find footage for all scenes** and the one-click draft) is a
   plan → search → check → search-again loop:
   1. *Plan.* Gemini reads the whole script and plans what the viewer should see while each
      part of a line is spoken (the octopus itself for "Octopus ki moodu hearts untayi", the
      video's subject or a human reaction for hooks and "comment cheyyandi"). A long line is
      split into consecutive parts in spoken order, one shot per part, e.g. "Honey … paadavadu |
      ani scientists cheptunnaru | Egypt pyramids lo kuda dorikindhi" → honey jar, scientist,
      pyramids. Each shot gets three search phrases from specific to broad.
   2. *Search.* Every phrase is searched; candidates are ranked by tag match (the subject
      counts most), the provider's own order, real film over animation, resolution, a shape
      that suits the format and popularity; green-screen or "subscribe" overlays are pushed down.
   3. *Check.* Gemini looks at a preview frame of the best candidates for every shot (with the
      words it is spoken over) and scores the ones that show it out of 10.
   4. *Search again.* Shots with no clip, or only one scoring below 7, are planned again from
      what was rejected (a findable view of the same idea: the subject itself, a wider view, a
      related place or a reaction), searched with new words and checked again; the better clip
      wins. The match then says **after a second search**.
   Each scene gets its best clips not already used by another scene; a scene with nothing
   scoring 5 gets no clip (a title card in the one-click draft). The score and planned shot
   appear under **Footage search results** in Production jobs.
   Downloads use 4K files when they stay under 60 MB, so a 9:16 crop of landscape footage
   stays sharp. A short reel uses about three or four Gemini requests per run (the daily
   limit in Settings counts them); without Gemini, the scene keywords and the ranking are used.

   **A new shot about every 4 seconds, in step with the words:** a scene whose narration is
   longer than about 4 seconds gets more clips (up to four, estimated from its length at about
   two Roman Telugu words a second). When the shots were planned per part of the line, each
   clip is shown while its own words are spoken, for that part's share of the line (a part
   shorter than 1.2 s stays on the clip before it); otherwise the render cuts between them in
   equal parts. The narration stays one continuous take. Extra shots play from their start,
   cropped to fill the frame; the scene's framing and trim apply to its main clip. The scene card shows
   **+N more shot(s)** with **Use only the main clip**; choosing a clip yourself (upload,
   library or a single stock result) also replaces them.
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

### Captions on or off

Captions are on for every project. To make a reel without burned-in captions, turn off
**Show captions on the video** on the Captions tab or on **Preview & export** (the same
switch, saved with the project), then approve and render. Title cards keep their heading,
`captions.srt` is still included with the export, and Roman caption text is no longer
required by the readiness check.

### Highlighted key words

**Highlight key words** (Captions tab, on for new projects) shows each line's most important
words and every number in yellow, and the rest in the style's colour, like professional reels;
with **Yellow accent** the key words are white instead. The AI chooses up to three key words per
line, copied from the line, when it prepares the scenes (Split into scenes, Prepare my script
with AI, or the one-click draft); edit them per scene under **Key words in the highlight
colour**. A scene without key words highlights its stock search words and numbers. Projects
created before this option keep plain captions until it is turned on. Drafts, final exports and
every preview draw the same highlight.

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
in your own API account. **Backup Gemini models** in Settings (comma-separated; default
`gemini-3-flash-preview, gemini-3.1-flash-lite, gemini-flash-lite-latest, gemini-3.5-flash`)
are tried in order when a model is out of quota (HTTP 429), busy (503) or retired (404);
each model has its own free daily quota, so a free key gets more requests per day. Other
errors (such as a bad request) are reported without trying the backups. The last model
waits out high demand or a short per-minute limit before giving up. The Gemini daily
limit counts HTTP attempts, including retries and backup models. It is a request limit,
not a spending cap.
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

**Delete a project** with the bin next to it in the sidebar (shown on hover) or **Delete**
in the project header. After one confirmation it permanently removes the project, its
revision history, autosave, voice takes, production jobs and rendered videos. Clips and
images in the media library stay, because other projects can use them. A project with a
job in progress cannot be deleted until the job finishes or is cancelled.

Failed jobs can be retried. Retrying current inputs requires approval of the current
revision; original-input retries retain their original snapshot. Cancelled and
completed jobs cannot be restarted by duplicate queue delivery. Crashed execution
leases expire and become retryable failures. Do not delete original assets while
saved projects still reference them.

## Verification

On this PC, `VERIFY_WINDOWS.bat` runs dependency checks, Django checks, and the
isolated audit suite. It uses the project-local Node runtime when available.
To recreate that test environment without upgrading your system Node installation:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm.cmd install --prefix .local-tools --no-save --package-lock=false node@24.15.0
$env:PATH = "$PWD\.local-tools\node_modules\node\bin;$env:PATH"
npm.cmd ci --prefix frontend
```

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
