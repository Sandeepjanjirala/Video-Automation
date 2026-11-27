# Video Studio on a shared Ubuntu VPS

The automation is opt-in. Nothing deploys from GitHub Actions, changes Cloudflare,
or contacts your VPS. The existing domain remains `studio.sandeepwebsolutions.in`.
Never run `docker compose down -v`, prune volumes, or reset the production database.
EduNexa's port 8002 is explicitly rejected by preflight.

## Architecture and audit findings

| Evidence in the original repository | Finding and change |
| --- | --- |
| `Dockerfile` invoked `run.py`; `run.py` invoked Django `runserver` | Docker now runs Gunicorn WSGI. Local Windows startup stays intact. |
| `requirements.txt` pinned unsupported Django 5.1.15 | Move to Django 5.2.19 LTS with REST framework 3.17.0 and Gunicorn 27.0.1; health reports the installed Django version. |
| `core/views.py` defines a process-local write token; `studio/access.py` has process-local login throttling | Gunicorn uses one process with two threads. Multiple processes would produce inconsistent browser tokens/throttling. |
| `config/urls.py` explicitly routes static files through authenticated views | Preserve this working static handling; no collectstatic or WhiteNoise is required. No frontend build is required at runtime. |
| Compose has PostgreSQL 18, Redis 7 AOF, two Celery queues and an outbox dispatcher | Keep those components and existing database initialization (`manage.py migrate`). Do not upgrade PostgreSQL major versions during adoption. |
| `studio/tasks.py` derived parallel scenes and encoding threads from visible host CPU count | Production uses one render task, one scene, one encoding/filter thread. `studio/media.py` also bounds preview/audio FFmpeg and serializes FFmpeg across containers via the media mount. |
| API cancellation signalled a PID stored by a different container | Distributed cancellation now relies on the owning worker's persisted-state polling rather than signalling an unrelated local PID. |
| Docker mode set `ALLOWED_HOSTS=['*']` | Production mode permits the configured domain and loopback hosts. |
| Redis maxmemory and container limit were both 256 MiB | Keep 256 MiB maxmemory/noeviction; allow 384 MiB container memory for AOF/allocator overhead. This is a starting ceiling, not a measured peak. |
| Only database and Redis had health checks; no log rotation | Add API, directed worker ping and dispatcher heartbeat checks, bounded Docker logs, queue/lease reports. |
| `.env.example` and `studio/config.py` contained a provider credential | Removed both copies. Rotate that credential separately: deleting the current value does not erase Git history. Existing private environment/settings values are preserved. |
| No deploy/update/restore scripts existed | Add guarded operations, database backups, image rollback and dry-run temporary cleanup. |

The data model uses PostgreSQL for projects, revisions, timeline/caption JSON, jobs,
assets and voice metadata. `./data:/app/data` holds uploaded media, recordings,
settings, generated exports, caches and temporary work. The PostgreSQL volume
mount stays `/var/lib/postgresql` for version 18; Redis stays `/data`.
Preflight compares existing mounts and Compose labels before any recreation.

## Prerequisites

Ubuntu 22.04, Docker Engine, Compose v2.24+ (optional env-file syntax and `--wait`),
Bash, Python 3, curl, flock/util-linux, Git, openssl and sufficient disk space are
required. Nginx and Certbot are host services. Install/configure them separately
with the host administrator's authorization. Scripts do not install packages,
change swap, firewall rules or other applications. Allow at least 4 GiB free disk;
large media backups need additional space equal to the media size. Docker access
is privileged: use a trusted deployment account.

The production overlay is always used by the scripts. Plain `docker compose up`
does not activate its security/resource configuration. Do not use the separate
legacy `docker-compose.https.yml` on a host already using Nginx.

## Fresh installation

```bash
git clone https://github.com/Sandeepjanjirala/Video-Automation.git
cd Video-Automation
cp .env.production.example .env
chmod 600 .env
nano .env
# Set new unique secrets ONLY on a new database. Generate locally, e.g. openssl rand -hex 32.
# Configure the domain's Nginx and origin certificate as described below.
bash scripts/preflight.sh
bash scripts/deploy.sh
bash scripts/healthcheck.sh --synthetic
```

Set `POSTGRES_PASSWORD` (at least 16 characters), `DJANGO_SECRET_KEY` (at least 40)
and `STUDIO_PASSWORD` (at least 12). Use URL-safe database credentials because the
existing Compose file constructs a connection URL. Optional Gemini, Sarvam,
Azure, Pexels and Pixabay credentials may remain empty; offline functionality and
deployment checks do not need them. Configure voice/provider preferences in the
existing application Settings. Keep real `.env` files and backups out of Git.

The scripts validate, build one shared image, start healthy PostgreSQL/Redis,
run one-off migrations, verify tools/fonts/storage, start application services,
then verify local HTTP, public HTTPS and the origin TLS certificate. Migration
does not require a running API container. A failed check exits nonzero and leaves
data untouched; it does not silently restore a database.

## Adopt an existing installation safely

Do this in the **existing checkout**, with the original data directory. Before
pulling, record the existing Git revision, container images, Compose project name
and mounts. Do not print full container environments or `docker compose config`
to shared logs: those contain credentials.

```bash
git status --short
git rev-parse HEAD
docker inspect --format '{{.Image}}' studio-api
docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' studio-postgres
docker inspect --format '{{json .Mounts}}' studio-postgres studio-api
```

1. Back up the currently running database with the existing configuration before
   adopting new code. Copy the backup and `data/` to protected off-host storage.
2. Pull `main` only after preserving any server-only changes. Never overwrite
   `.env`. Add missing values from `.env.production.example` manually.
3. Preserve `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD` and original host
   ports. Docker environment changes do **not** change an initialized database's
   password. If old credentials are weak or include URL delimiters, stop and plan
   a coordinated database/URL credential migration; preflight intentionally refuses
   unsupported values instead of rotating them.
4. Preserve the existing `data/secret_key` as `DJANGO_SECRET_KEY`. Do not paste it
   into chat or logs. This keeps signed browser sessions consistent.
5. Confirm host-specific `STUDIO_*_DATABASE_URL` overrides do not point containers
   at host loopback. Review any dedicated writer/migration identities individually.
6. Check `data/` and the existing database major version. Nonstandard mount paths,
   external databases or project identities need an explicit reviewed configuration
   adjustment; never start with newly named empty volumes.
7. Run preflight, let all queued/running jobs finish (or cancel intentionally in
   the UI), then run deploy. Keep the existing Nginx site and SSL certificate.

```bash
chmod 600 .env
bash scripts/preflight.sh
bash scripts/deploy.sh
bash scripts/healthcheck.sh
```

For the first backup before these scripts exist:

```bash
umask 077
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%S)
docker exec studio-postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "backups/pre-adoption-$stamp.dump"
docker exec -i studio-postgres pg_restore --list < "backups/pre-adoption-$stamp.dump" >/dev/null
(cd backups && sha256sum "pre-adoption-$stamp.dump" > "pre-adoption-$stamp.dump.sha256")
```

## Updates and recovery

```bash
bash scripts/update.sh
```

Requires a clean `main` checkout and a fast-forward from `origin/main`. A shared
flock blocks concurrent maintenance. The script refuses queued/running jobs,
stops API admissions, checks again to close the race, then stops workers and the
dispatcher. It retains the running image by immutable image ID, backs up the DB,
fast-forwards code, builds, migrates and checks health. There is a maintenance
window, including build time; this is **not zero downtime**. An unchanged revision
only runs health checks. If an earlier update failed after checkout, use
`bash scripts/deploy.sh` after diagnosis rather than assuming another pull fixes it.

Before migrations or activation begin, failures restart only previously running
services. Once migrations begin, services may remain stopped intentionally. Inspect the failing step
and logs privately. To restart the current configuration after resolving it:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --no-deps api worker_network worker_render dispatcher
bash scripts/healthcheck.sh
```

Use the discovered `COMPOSE_PROJECT_NAME` if the checkout directory was renamed.
Do not restart an old application against an incompatible migrated schema.
For a known backward-compatible code-only regression:

```bash
bash scripts/rollback.sh
```

Rollback uses the retained previous image and requires the recorded Django
migration history to match exactly, then checks for pending migrations. It refuses
automatic rollback across any recorded migration boundary, never reverses migrations
and never rewrites Git. Out-of-band schema edits are outside this check; review them
manually. The active image choice is persisted for later maintenance. The checkout remains at
the new code, so resolve the regression before deploying again. The last image
selection remains active when `update.sh` finds no new Git commit; use `deploy.sh`
to deliberately retry the checked-out release after fixing the problem. The previous image
is retained under `studio-local:rollback-<image-id>`; old tags accumulate until
an operator reviews which images can be removed.

Automatic rollback also requires image deployment contract `1`, which includes
dispatcher heartbeat support. Images from before this hardening release lack that
contract and are rejected before any service is stopped. Before first adoption,
retain the original Compose files, image IDs and host configuration in protected
storage. Recovering that legacy release requires its original Compose configuration,
the retained image, unchanged volume/project identities and a manual schema review;
do not apply the new health-check overlay to an incompatible old image.

## Backups, restore and cleanup

```bash
bash scripts/backup.sh                  # Online consistent PostgreSQL snapshot
bash scripts/backup.sh --media          # Requires idle jobs; brief full maintenance
bash scripts/restore.sh backups/studio-TIMESTAMP-PID.dump --into-new-database studio_restore_review
bash scripts/cleanup.sh --dry-run
STUDIO_TEMP_DAYS=7 bash scripts/cleanup.sh --apply
```

Database archives are custom pg_dump format, checked with pg_restore --list and
SHA-256. Structural checks are not a restore drill; restore into a new database
and inspect projects/assets before planning a cutover. The restore script refuses
an existing target and never switches production. Media backups are verified
tar archives containing **all** `data/`, including private provider settings.
Restore media into a separate staging directory, verify checksums and DB/media
references together, then plan an explicit maintenance cutover. Never extract an
old archive over live uploads. A code rollback does not restore user data.

`STUDIO_BACKUP_KEEP=7` keeps seven completed DB archives; the newest is retained.
Media archives and partial archives are not automatically deleted. Backups stay
local unless you copy them off-host; local disk loss would lose both application
data and these backups. Schedule backups externally only after reviewing the
maintenance window and retention/storage requirements.

Cleanup only considers expired children of `data/tmp`, skips symlinks and any
tree with recent modifications, and requires no queued/running jobs. Applying
cleanup stops admission/workers. It never removes uploads, exports, projects,
database files or cache. Completed export retention remains manual and unlimited.

## Nginx, Cloudflare and SSL

Existing production sites should **not** run the setup steps below. Routine
deployment never rewrites Nginx or requests certificates. For a new site:

```bash
bash scripts/setup-proxy.sh --render
cat .deployment/nginx-http.conf
cat .deployment/nginx-https.conf
# Each following command explicitly authorizes the named host operation:
bash scripts/setup-proxy.sh --install-http
bash scripts/setup-proxy.sh --obtain-certificate you@example.com
bash scripts/setup-proxy.sh --enable-https
sudo nginx -t
sudo systemctl status certbot.timer
sudo certbot renew --dry-run
```

Set Cloudflare A/AAAA records to the correct origin; remove a stale AAAA record
if the host cannot serve IPv6. Keep Full (strict). HTTP-01 challenge requests must
reach `/.well-known/acme-challenge/`; an edge redirect to unavailable origin HTTPS
can prevent issuance. Use a reviewed DNS challenge if your existing edge rules
prevent HTTP-01. Never weaken Full (strict) to hide an origin certificate problem.

The optional setup refuses a domain already in Nginx, reuses a valid certificate,
only upgrades its own unchanged HTTP bootstrap, and validates before reload.
It does not touch EduNexa files. Configure a Certbot deploy hook to run
`nginx -t && systemctl reload nginx` on successful renewal, with host-admin approval.
Healthcheck separately checks local TLS with the real SNI hostname; Cloudflare's
edge certificate alone does not prove the origin is valid. For error 526, check
origin certificate expiry, hostname, full chain and Nginx's selected virtual host.

The proxy accepts 220 MiB requests, overwrites forwarding headers and uses 180s
timeouts. Existing API upload limits remain unchanged. No WebSocket upgrade is
added because this application uses ordinary HTTP polling and file responses.

## Resources and reliability

Retain 512 MiB ceilings for API, network worker and PostgreSQL; 1 GiB for render;
256 MiB for dispatcher; 384 MiB for Redis. These aggregate to about 3.1 GiB and
are ceilings, not guaranteed reservations or measured simultaneous peaks. On the
shared 4 GiB host, monitor both applications during a real full render. CPU shares
favor API over heavy work. Avoid hard CPU quotas until measured. Worker concurrency
is one in each queue; FFmpeg is serialized through `data/ffmpeg.lock`. Preview
requests wait at most 30 seconds for that gate before returning a retry message.
CRF, dimensions, Telugu styles, framing and audio timing remain unchanged.

PostgreSQL has short-lived Django connections and five-second connect timeouts.
Celery uses JSON, late acknowledgements and prefetch one. PostgreSQL outbox row
locks and execution leases/fencing tolerate duplicate delivery; this is at-least-once,
**not exactly-once**. Redis AOF/noeviction preserves broker state subject to Redis's
normal fsync loss window. The dispatcher reconciles queued work. Expired running
leases become failed jobs for explicit retry, avoiding unlimited paid-provider
retries. A durable outbox may retry broker publication indefinitely with bounded
backoff; that differs from re-executing paid work. Docker `unhealthy` is diagnostic,
not an automatic restart policy. Restart exits are handled by `unless-stopped`.

```bash
bash scripts/healthcheck.sh
docker stats --no-stream studio-api studio-worker-render studio-postgres studio-redis
free -m
vmstat 1 5
df -h
du -sh data backups
docker compose -f docker-compose.yml -f docker-compose.production.yml logs --tail=100 api worker_render dispatcher
```

Inspect logs privately: pre-existing provider exception paths may include private
request details. The new scripts do not echo .env/configuration. A full disk or
Redis noeviction error must be addressed by reviewing specific data/queue use,
never global Docker pruning. For memory failures, record Docker OOM/restart status,
RSS, swap-in/out and render duration before changing limits. Idle snapshots are
not evidence of peak render requirements.

## Validation and limits

```bash
python3 -m unittest discover -s tests -p test_deployment.py -v
docker build --target test -t studio-deployment-test .
python3 scripts/test_deployment.py --image studio-deployment-test
```

The isolated test uses unique containers/volumes and synthetic credentials,
repeated migrations, real database backup/restore, service restarts, recreation,
media persistence, and the existing functional audit suite. It never reads the
real `.env` or calls paid APIs. Test volumes remain for inspection; no volume
pruning runs. See `DEPLOYMENT_VALIDATION.md` for actual results and pending checks.
GitHub CI validates scripts, guard tests, the existing audit suite and isolated
deployment. It has no SSH/deployment step.

Before production adoption, verify original mount/project identity, credentials,
Nginx/SSL/renewal, disk headroom for backups, and full-render peak RSS/CPU alongside
EduNexa. Interactive browser testing and real provider credentials require a
separate authorized session. Full automatic host provisioning, managed off-host
backups, zero downtime, arbitrary old-schema rollback and exactly-once jobs are
not claimed.
