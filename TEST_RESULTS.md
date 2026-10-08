# Verification — Telugu Reel Studio 0.1

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
