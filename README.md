# Telugu Reel Studio

A local workspace for turning your researched script into a vertical Instagram Reel or a
long-form 16:9 YouTube video: script and Roman Telugu captions → footage → narration →
draft review → MP4 export.

**Publishing is not implemented.** Download the final MP4 and upload it to Instagram or
YouTube yourself.

## What it does

- **Two formats:** a vertical 9:16 Reel or a 16:9 YouTube video from the same script.
- **One click to a draft:** the script is split into scenes, one Gemini request completes each
  scene (Telugu narration text, Roman captions, a footage search phrase), stock footage is
  matched to the script, and a 540×960 draft is rendered with free text-to-speech narration.
- **Your own narration:** upload recordings (one file for the video or one per scene) instead
  of text-to-speech. Uploads are checked; trims and levels are settings, so the original file
  is never changed.
- **Captions:** Roman Telugu captions with an editor, caption styles, optional highlighted key
  words, and captions on or off.
- **Sound and export checks:** narration levelled to about -16 LUFS, music lowered while the
  narration speaks, peaks limited; **Ready to render?** lists anything that blocks a render,
  and a framing preview shows how each clip is cropped.
- **Use it from a phone:** the studio runs on a computer or server; phones sign in with a
  password.

The full walkthrough of every screen is in the [user guide](docs/USER_GUIDE.md).

| Story: paste a script, make a draft | Captions: Telugu caption styles | On a phone |
|---|---|---|
| ![The Story step of a sample project](docs/screenshots/story.png) | ![The Captions step with caption styles](docs/screenshots/captions.png) | ![The studio on a phone](docs/screenshots/phone.png) |

## Current architecture

- Django + Django REST Framework; Django templates and local jQuery.
- PostgreSQL for projects, immutable revisions, assets, jobs, and voice takes.
- FFmpeg/FFprobe for video processing, subtitles, narration normalization, and music ducking.
- Local mode: one worker process launched by `run.py`.
- Distributed mode: Redis, a transactional outbox dispatcher, and Celery workers.
  Both modes execute the same leased job pipeline.
- Optional providers: Gemini for research/script preparation, Pixabay for footage,
  Edge community TTS, Azure Speech, and Sarvam speech. Provider credentials are separate.

```mermaid
flowchart LR
    browser["Browser (computer or phone)<br/>Django templates + jQuery"] --> django["Django + DRF<br/>projects, revisions, assets, settings"]
    django --> db[("PostgreSQL<br/>projects · immutable revisions<br/>assets · jobs · outbox")]
    db --> local["Local mode: one worker<br/>started by run.py"]
    db --> outbox["Distributed mode: outbox dispatcher"] --> redis[("Redis")] --> celery["Celery workers"]
    local --> pipeline["Leased job pipeline<br/>footage · narration · captions · render"]
    celery --> pipeline
    pipeline --> ffmpeg["FFmpeg / FFprobe"] --> mp4["MP4: 9:16 Reel or 16:9 video"]
    pipeline -. "optional online services" .-> providers["Gemini · Pixabay<br/>Edge TTS · Azure · Sarvam"]
```

The records of the migration from FastAPI, React, SQLite and Alembic to Django and PostgreSQL
are in [`docs/history/`](docs/history/); they are not current setup instructions.

## Start on Windows

Python 3.12 and FFmpeg/FFprobe are required. FFmpeg needs `libx264`, `libass` (the `ass`
filter), `ebur128`, `alimiter`, `sidechaincompress`, `silencedetect` and `blackdetect` (any
FFmpeg 5.1 or newer full build has them; the Docker image's FFmpeg 7.1 does). Node.js 24.15+ (or 22.22.2+) is needed only
for frontend tests; Node 20 is too old for the pinned jsdom dependency.

1. Run `SETUP_WINDOWS.bat` to install Python dependencies into `.venv`.
2. Start PostgreSQL. With Docker available, infrastructure can be started using
   `docker compose up -d postgres redis`.
3. The default local database is `postgresql://postgres:postgres@127.0.0.1:5433/studio`.
   Override it with `STUDIO_DATABASE_URL` when needed (as an environment variable or a line in
   `.env`, which is read before the database settings). Percent-encode special
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
   are never pushed and `git pull` never changes them. They are the **defaults**: Settings shows
   "Default key from .env in use", and typing a key there replaces it for this studio (saved in
   `data/settings.json`); **Use the default key from .env** goes back. Never put real keys in
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
`STUDIO_PASSWORD` (Settings then says the password is set on the server). Without the HTTPS
file, `docker compose up` keeps the studio on the server's own `127.0.0.1:8788`, also behind
the password. Each installation generates its own secret key in `data/secret_key` (or set
`DJANGO_SECRET_KEY`); `STUDIO_ALLOWED_HOSTS` adds host names, and `STUDIO_BEHIND_PROXY=1`
trusts an HTTPS proxy's headers.

To start the entire distributed stack, use `docker compose up --build`. Put
`STUDIO_PASSWORD=...` in the `.env` file next to `docker-compose.yml` first: in Docker every
browser signs in with it (no browser counts as "this computer"), and Compose refuses to start
without one. The API keys in `.env` reach the containers too.
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

## Using the studio

See the [user guide](docs/USER_GUIDE.md) for the full walkthrough: video format, one-click drafts,
step-by-step editing, your own narration, the caption editor, the sound check and framing preview,
and the providers, files and recovery options.

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
uses separate media/settings, disables online services, leaves the studio's Redis rate limits
alone (`STUDIO_CACHE_REDIS_URL=off`), and retains test artifacts
under `test-outputs/`. The test database account needs CREATE DATABASE permission.
It tests real FFmpeg exports, uploads, editor controls, concurrency, cancellation,
voice selection, quotas, and fresh migrations. Browser-DOM tests use Node.js/jsdom.

**Do not run the historical integration suites against your working database.**
Several older fixtures use hardcoded `studio`/`studio_test` databases, alter roles,
or assume obsolete migration stages and already-running servers. The isolated
runner deliberately runs `tests/test_audit.py`; it does not certify those old suites.

See [`docs/history/AUDIT_2026-10-30.md`](docs/history/AUDIT_2026-10-30.md) for the last full audit and its remaining gaps.

## Known limitations

- Publishing is not implemented: download the MP4 and upload it yourself.
- A single-user studio: one person's projects, one password for other devices.
- Only `tests/test_audit.py` is maintained and run (locally and in CI); the other files in
  `tests/` are older integration suites kept for reference.
- Gemini, Sarvam, Azure and Pixabay are optional online services with their own quotas and
  costs; Edge TTS is a community integration without an availability guarantee.

## More documentation

- [User guide](docs/USER_GUIDE.md): every screen and option, providers, files and recovery.
- [Changelog](CHANGELOG.md): what changed, and how each change was checked.
- [Migration history](docs/history/): the move from FastAPI/React/SQLite to Django/PostgreSQL.

## License

[MIT](LICENSE).
