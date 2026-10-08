# Telugu Reel Studio — first runnable release (0.1)

A separate, local application for Sandeep's workflow: multi-topic stories, Telugu speech,
Roman Telugu captions, adjustable duration, and manual publishing to Instagram and YouTube.
The existing ShortsGenius project is not changed or required.

This release implements the core editing-to-export workflow. It is not a claim of
production certification or guaranteed monetization. Live provider credentials,
voice quality, and the Windows launcher still need validation on your PC.

## Start on Windows

1. Extract this ZIP to a new folder, for example `Documents\TeluguReelStudio`.
2. Run `SETUP_WINDOWS.bat`. It creates a project-only virtual environment and installs
   dependencies. Python 3.12 x64 is preferred; the launcher also accepts installed
   Python 3.11–3.14. Only Python 3.12 was tested for this delivery.
3. Run `START_WINDOWS.bat`. Keep the terminal open.
4. Open **http://127.0.0.1:8787** in Chrome or Edge.
5. Open **Settings & providers**. If FFmpeg is not on PATH, paste the full paths to
   `ffmpeg.exe` and `ffprobe.exe` that already work for ShortsGenius. Do not paste
   surrounding quotes. Save, reopen Settings, and use **Check saved media setup**.

Use an FFmpeg build with `libx264`, `subtitles`/libass, `loudnorm`, and
`sidechaincompress`. There is no FFmpeg installer bundled here and no existing
configuration is overwritten. The app binds only to the local loopback interface.

The CPU is used for encoding, with four encode threads and one active job. The
Intel UHD 730 is not required. This configuration is intended for your i5-13400 / 16 GB
machine, but render speed on that hardware has not been benchmarked. No local
video-generation model, CUDA dependency, or large model download is required.

## Your first reel — no API keys required

1. Create a new story. Enter a title, topic, and target duration.
2. Enter a short scene's narration in **native Telugu script**. Enter the same spoken
   words in English letters in **Roman Telugu captions**. Add more scenes as needed.
3. Add factual review notes (or explicitly identify original fiction). Save and approve.
4. In **Voice & visuals**, upload a video for each scene or explicitly select a
   simple animated title card. Confirm each scene's relevance and usage rights.
5. For narration, either upload an audio-only recording for every scene, or enable
   online services and use the optional Edge community TTS integration.
6. Prepare voice previews, review pronunciation, then render a 540p draft.
7. Review framing and caption timing. Adjust as needed, save and approve, and render
   the final 1080p export.
8. Download `reel.mp4`. Publish manually. No social accounts are connected.

Entering Roman Telugu in the caption field does not automatically make the TTS
speak correctly. Native Telugu narration text is separate for that reason. You can
paste a Roman Telugu script into the import box and use configured Gemini to
transliterate it; generated results require review before applying.

## Optional online automation

All online services are disabled until enabled in Settings. Each action is initiated
by a button; there are no scheduled calls or automatic paid fallbacks.

| Action | Provider / requirement |
|---|---|
| Research and claim review | Gemini API key + available model that supports Google Search grounding |
| Draft from research / prepare your own script | Gemini API key + compatible model |
| Stock search, candidate selection and downloads | Your Pixabay API key |
| No-key online speech | Optional `edge-tts` community integration |
| Official speech API | Azure Speech resource key + region |
| Fully offline narration | Upload your own scene audio |

Gemini's model ID is intentionally configurable with no silently chosen default.
The adapter uses the documented `generateContent` interface and its `google_search`
tool. Select an available model in your API account. A ChatGPT subscription is not
used by this application and no API credits are included.

The Gemini request limit is per UTC day and counts attempted requests, including
failures. It is **not a rupee spending cap**: token and grounding-search charges vary
by model/provider. Use provider-side quotas and billing controls where available.

Edge TTS uses an unofficial integration with an online Microsoft service, not a
local model or contracted API. Availability can change. Evaluate its terms and
commercial suitability yourself; use an appropriately licensed recording or an
official Speech API when required. No monetization or voice-service entitlement is
implied by this package.

## Topic → research → script → video

1. Enter the topic and choose an editorial preset.
2. Click **Research / check claims**. The worker returns a sourced, model-assisted
   report; a report without grounded source links is rejected.
3. Read the completed job's report. **Apply result**, review it, then save.
4. Click **Draft from research**, or paste your own script and choose **Prepare my
   script with AI**. Existing script text is not overwritten until you apply a result.
5. Edit the script and its Roman captions. Add your own evidence notes and approve.
6. Click **Find footage for unassigned scenes** to download keyword-selected
   candidates. Existing assigned uploads and explicit graphic scenes are preserved.
   Apply the result, inspect every selected clip, and confirm relevance and rights.
   Missing matches remain missing; they are never replaced by unrelated demos.
7. Replace individual selections with uploads or another Pixabay result whenever needed.
8. Prepare narration, review, render a draft, and export the final cut.

Stock matching uses keywords and prioritizes an unused portrait candidate when
available. It is not semantic visual verification. A general stadium clip is not
evidence of a particular match or event. The output report retains source/creator
and license references; these records do not replace permission review.

## Duration and captions

- Target is adjustable from **10 to 180 seconds** for this release.
- **Natural narration pace** uses actual recorded/synthesized audio length plus
  scene holds; the target guides script generation, not a forced cutoff.
- **Fit target** allows only 0.8–1.25x global narration speed adjustment. If a target
  needs a larger change, the job pauses with an error asking you to edit the script
  or target. It does not silently cut narration.
- Scene lengths are rounded up to 30 fps frames; final measured duration is reported.
- Edge word boundaries are mapped by word index only when counts agree. This is
  an initial alignment, not guaranteed phonetic alignment.
- When counts differ, or Azure/uploaded audio lacks word boundaries, caption timing
  is estimated by text length and clearly labelled for review.
- Use **Load generated timings for editing** and **Edit caption timestamps** to
  correct `start`, `end`, and `text` per scene. Times refer to the original narration;
  target-fit speed adjustment is applied during rendering. Empty cues mean automatic.
- Narration or Roman-text edits clear old manual cues. A changed narration invalidates
  its uploaded-audio binding: re-upload the correct recording or remove it to use TTS.
- Caption size and bottom margin are adjustable. Preview against each platform's UI.

## Editing, revision history, retries

Saving creates a new immutable revision. A no-op save preserves approval. Any actual
edit conservatively requires re-approval, including visual/layout edits in this first
release. All jobs snapshot their project revision and voice provider. A completed
result can be applied only when its originating revision is still current and there
are no unsaved edits. This prevents an old AI draft from overwriting new work.

Use **Revision history** to load an earlier version as a draft. Saving that draft
creates a new revision and keeps history. Failed jobs can retry their original
inputs; to use edited inputs, queue a new job. Narration and encoded scenes are
cached. An unchanged retry reuses completed work; caption/visual changes can reuse
narration. Scene cache invalidation is conservative and may rerender more than the
minimal set when scene metadata changes.

The worker is a separate process with a SQLite queue and an exclusive single-worker
lock. Interrupted running jobs become failed at worker restart; press Retry. Queued
jobs remain queued. Leave the launcher open until work finishes; cancellation and
fine-grained job prioritization are not implemented in this release.

## Outputs and data

Every successful render exposes:

- `reel.mp4` — 540×960 draft or 1080×1920 final, H.264 video / AAC audio, 30 fps.
- `captions.srt` — the burned caption content and timings as a separate file.
- `source-report.json` — revision, duration, media provenance, caption alignment mode,
  research source links, music asset record, and your review notes.
- `project.json` — the render input snapshot, without provider keys.

These last two files can contain your editorial notes: review before sharing them.

Projects, assets, caches, settings, and exports live under the application's `data/`
folder. Keep it out of Git and back it up while the application is stopped. Keys are
stored in `data/settings.json`; filesystem permissions protect them, but there is no
OS credential-vault integration yet. The API never returns saved key values.

The application has no cloud hosting, public authentication, publisher, scheduler,
or analytics integration. It is a single-user local workspace, not an internet-facing
service. Upload limit is 200 MB per file; downloaded stock clips are limited to 180 MB.
There is no automatic cache deletion in this release; close the app before manually
removing cache files. Keep original `assets/` files because saved projects reference them.

## Included and deferred

Implemented: React/TypeScript editor, FastAPI API, durable local worker, immutable
revisions, human approval, Gemini adapters, Pixabay search and automatic candidate
sourcing, scene uploads, Edge/Azure voice adapters, uploaded audio, editable captions,
trim/crop/contain framing, simple title-card fades, music ducking, draft/final rendering,
validation, retry, and export provenance.

Deferred: advanced timeline UI, high-quality animated maps/graphics templates,
Remotion integration, AI presenters, generated video, forced phonetic alignment,
automatic live-news freshness rechecks at export, bulk scheduling, publishing,
analytics, multi-user access, and cloud deployment. There is no claim that this
first release produces a polished monetizable reel without editorial work.

## Developer commands

```powershell
.venv\Scripts\python.exe doctor.py
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests -q
```

The prebuilt React interface is included: Node.js is **not required to run the app**.
To change it, install Node.js, enter `frontend/`, run `npm ci`, then `npm run build`.
`package-lock.json` records the frontend build dependencies. Python direct dependencies
are pinned; `requirements-tested.txt` also records the tested transitive environment.

On Linux/macOS: create a venv, install `requirements.txt`, make FFmpeg/FFprobe available,
and run `python run.py`. The application itself has no Windows-only runtime dependency.

## Provider references checked for this release

- https://ai.google.dev/gemini-api/docs/generate-content/google-search
- https://ai.google.dev/gemini-api/docs/google-search
- https://pixabay.com/api/docs/
- https://pixabay.com/service/license-summary/
- https://github.com/rany2/edge-tts
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support

See `TEST_RESULTS.md` for the actual verification scope and remaining limits.
