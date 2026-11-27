# Deployment validation — 27 November 2026

Validation ran locally with Docker Desktop's Linux engine and synthetic credentials.
No SSH connection, live deployment, paid API request, Nginx edit or EduNexa change
was made. This is evidence about this repository and isolated test environment,
not a claim about the VPS's currently deployed revision.

## Completed checks

| Check | Evidence |
| --- | --- |
| Docker test image builds | Python 3.12, Django 5.2.19 LTS, REST framework 3.17.0, Gunicorn 27.0.1, Celery, PostgreSQL driver, FFmpeg 7, FFprobe, libass, Noto Telugu fonts and Node DOM-test dependencies installed successfully. Exact Python constraints were applied; `pip check` passed. |
| Fresh isolated production stack | All six services reached healthy state using the production overlay and unique test volumes/network. No host ports or existing media were used. |
| Initialization repeated | Three Django migrations applied; a second migrate invocation reported no migrations to apply. `makemigrations --check --dry-run` found no model drift. |
| Real HTTP smoke | Gunicorn served login and static CSS, rejected unauthenticated bootstrap, issued a browser token, and accepted project creation/readback with that token. |
| Database backup/restore | Real custom-format pg_dump, archive inspection, restore to a newly created `studio_restore_fixture`, and migration-table readback succeeded. |
| Persistence | API container recreated; a media sentinel survived. PostgreSQL container recreated; application database/schema checks still passed. |
| Restart behavior | Redis and render worker restarted and became healthy. This is not a mid-render crash simulation. |
| Dependency failure/recovery | Stopping PostgreSQL and then Redis in the isolated fixture made API health checks fail as expected; restarting each restored healthy checks. All application services recovered before regression tests. |
| Missing tools/fonts | Injected missing FFprobe and a fallback non-Telugu font result caused the actual health helper to exit nonzero. These are mocked failures; installed packages were not removed. |
| Synthetic media | One-second draft/full H.264 encodes and duration probes passed without provider calls. |
| Functional audit | 157 tests passed in 181.06 seconds on the final supported dependency versions, including the check that no provider credential is built into defaults. The subsequent isolated outage/recovery run also passed all 157 in 138.16 seconds. |
| Deployment guard tests | 19 passed in Linux, including port ownership/conflicts, missing/weak settings, low disk, foreign project and volume mismatch, retention scope, active jobs, lock contention, dirty Git, rollback schema/image-contract boundaries and failure recovery. |
| Bash and ShellCheck | Bash syntax checks passed; ShellCheck passed for all 11 operational shell scripts. ShellCheck ran with no network and no host mounts, receiving only script bytes on stdin. |
| Secret handling | Private `.env` remained ignored and untracked. Both tracked copies of the exposed provider key were removed; a nonprinting scan found no remaining tracked copy. Rotation remains necessary because history was preserved. |
| Diff hygiene | `git diff --check` passed. |

The functional suite uses real PostgreSQL and FFmpeg plus mocked external providers.
It covers authentication, projects, Telugu conversion/voice workflows, stock/media
flows, timelines, caption styles, framing, aspect ratios, previews, full/draft exports,
downloads, cancellation, leases and recovery. DOM tests do not replace an interactive
browser session. A successful mocked provider test does not prove provider availability.

Guard tests use fake Docker/Git for failure injection. They execute actual Bash
control flow, but they do not prove an entire update/rollback on the real VPS.
The isolated integration script exercises startup and persistence commands directly;
host preflight, public TLS and actual Git update/rollback are separate checks.

Local ignored evidence is under `test-outputs/`: `deployment-verified.log`,
`deployment-final-source.log`, `deployment-supported-deps.log`, `deployment-outages.log`
and uniquely named `studio_deploy_test_*` fixture
directories. Test containers/networks were stopped/removed; test volumes remain
available for inspection. No global Docker cleanup was used.

## Failures found and corrected

The initial capped-container run passed 151 tests and failed two large-video
fixture encodes. Those calls bypassed scene-renderer thread limits. Applying
limits in the shared media runner fixed both without changing resolution/CRF.
Serializing FFmpeg exposed a cancellation-after-lock-wait race; checking state
before launch and preserving cancellation errors fixed it. An old test then
expected a generic process failure for distributed cancellation; it now verifies
both local signalling and distributed worker polling explicitly.

Review also found that pre-migration failures left an idle application stopped,
and that pending-migration checks alone could not prove rollback compatibility.
Failure recovery now restores previously running services before the migration
boundary. Automatic rollback requires the recorded migration history to match,
and its selected image persists across maintenance commands.
Legacy images without the dispatcher heartbeat contract are rejected before
rollback stops services. Their recovery requires the original deployment configuration.

## Measurements and pending verification

A local functional-test snapshot showed API memory around 187 MiB inside its
512 MiB limit. This was one sample on Docker Desktop, not a VPS peak, capacity
benchmark or proof that all possible user videos fit. The 1 GiB render limit and
other existing ceilings remain conservative starting settings. Real simultaneous
Video Studio/EduNexa peak RSS, swap pressure, disk growth and export duration
must be measured on the host before tuning them.

Pending checks requiring authorized host access or a dedicated matching staging host:

- Original Compose project, mounts, PostgreSQL major version and server-only changes.
- Real domain DNS, origin certificate chain/expiry, Cloudflare routing, Nginx
  bootstrap/reload, certificate issuance and renewal. These were not invoked.
- End-to-end `update.sh` across two real Git releases and actual retained-image
  rollback, with a backward-compatible schema. The control-flow guards are tested.
- Mid-render worker/host crash, broker/database outage during active jobs, deliberate
  OOM and actual disk exhaustion. No destructive failure simulation was run against
  production. Idle dependency outages were tested in isolated containers; low disk
  and missing tools/fonts were injected. Existing expired-lease/cancellation tests
  use the isolated database and controlled subprocesses.
- Human browser smoke test and real external-provider availability/credentials.
- Off-host backup transport and a recovery drill using a copy of actual production
  data. Local synthetic backup restoration already passed.

The environment template does not rewrite existing settings. Existing 220 MiB
application upload limits and completed-export retention remain unchanged;
temporary retention is configurable. Python dependencies are constrained to the
tested image's exact versions. Base image tags and OS package repositories remain
floating; a locked image registry/release promotion workflow
is not implemented. Application containers retain the existing root user to avoid
changing ownership of established bind-mounted media without a migration plan.

## References

The dependency startup checks follow Docker's documented distinction between
running and healthy services: [Compose startup order](https://docs.docker.com/compose/how-tos/startup-order).
The single-process threaded WSGI configuration uses
[Gunicorn's documented worker/thread settings](https://gunicorn.org/reference/settings/).
The dependency update addresses [Django 5.1's end of security support](https://www.djangoproject.com/weblog/2025/dec/03/django-60-released/)
using the [5.2.19 security release](https://docs.djangoproject.com/en/5.2/releases/5.2.19/).
