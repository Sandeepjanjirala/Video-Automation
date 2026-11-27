#!/usr/bin/env bash
# Shared implementation. Never source .env as executable shell code.
set -Eeuo pipefail
umask 077
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$ROOT"
log() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
trap 'printf "[FAIL] Operation stopped at line %s. No database restore was attempted.\n" "$LINENO" >&2' ERR
for tool in docker python3 flock curl git; do command -v "$tool" >/dev/null || die "Install $tool first."; done
[[ -f .env ]] || die 'Create .env from .env.production.example and set secrets; existing .env is never overwritten.'
[[ ! -L .deployment ]] || die '.deployment must not be a symbolic link.'
[[ ! -L backups ]] || die 'backups must not be a symbolic link.'
mkdir -p .deployment backups
chmod 700 .deployment backups
# Existing fixed container names let us discover the original Compose project, even after a directory rename.
existing_project=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' studio-postgres 2>/dev/null || true)
if [[ -n "$existing_project" ]]; then
  [[ -z "${COMPOSE_PROJECT_NAME:-}" || "$COMPOSE_PROJECT_NAME" == "$existing_project" ]] || die 'Compose project mismatch.'
  export COMPOSE_PROJECT_NAME="$existing_project"
fi
COMPOSE=(docker compose --env-file "$ROOT/.env" -f "$ROOT/docker-compose.yml" -f "$ROOT/docker-compose.production.yml")
dc() { "${COMPOSE[@]}" "$@"; }
if [[ -z "${STUDIO_RELEASE+x}" && -f .deployment/active-release ]]; then
  STUDIO_RELEASE=$(cat .deployment/active-release)
fi
export STUDIO_RELEASE=${STUDIO_RELEASE:-current}
[[ "$STUDIO_RELEASE" == current || "$STUDIO_RELEASE" =~ ^rollback-[a-f0-9]{64}$ ]] || die 'Invalid active image selection.'
resume_services() {
  export STUDIO_SAFE_RESUME=0
  local service
  local services=()
  for service in ${STUDIO_RESUME_SERVICES:-}; do
    case "$service" in api|worker_network|worker_render|dispatcher) services+=("$service");; *) die 'Invalid maintenance service.';; esac
  done
  if [[ ${#services[@]} -gt 0 ]]; then dc start "${services[@]}"; fi
}
finish_operation() {
  local status=$1
  if [[ "$status" != 0 && "${STUDIO_SAFE_RESUME:-0}" == 1 ]]; then
    warn 'Operation failed before migrations; restarting the previously running services.'
    resume_services || warn 'Restart failed; inspect the previously running services manually.'
  fi
}
trap 'finish_operation "$?"' EXIT
schema_fingerprint() {
  dc exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT app, name FROM django_migrations ORDER BY app, name"' | sha256sum | cut -d ' ' -f 1
}
lock() { exec 9> .deployment/operation.lock; flock -n 9 || die 'Another deployment/backup/maintenance operation is running.'; }
clean_git() { [[ -z "$(git status --porcelain)" ]] || die 'Commit or relocate local tracked/untracked changes first; do not discard them.'; }
check_idle() {
  local count
  count=$(dc exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM jobs WHERE state IN ('"'"'queued'"'"', '"'"'running'"'"')"')
  [[ "$count" == 0 ]]
}
backup_database() {
  local stamp file
  stamp=$(date -u +%Y%m%dT%H%M%S)-$$
  file="$ROOT/backups/studio-$stamp.dump"
  log 'Creating database backup'
  dc exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$file.partial"
  dc exec -T postgres pg_restore --list < "$file.partial" > /dev/null
  mv -- "$file.partial" "$file"
  (cd backups && sha256sum "$(basename "$file")" > "$(basename "$file").sha256")
  log "Verified archive structure: $file (a restore drill is still required)"
  # Only our completed archives; partial files and unrelated backups are never removed.
  python3 scripts/deployment_host.py retention "$ROOT/backups" "${STUDIO_BACKUP_KEEP:-7}"
}
quiesce() {
  check_idle || die 'Queued/running jobs exist. Let them finish or cancel in the UI, then retry.'
  STUDIO_RESUME_SERVICES=$(dc ps --status running --services api worker_network worker_render dispatcher)
  export STUDIO_RESUME_SERVICES STUDIO_SAFE_RESUME=1
  dc stop api
  # Close the race with requests accepted between the first check and API shutdown.
  if ! check_idle; then
    resume_services
    die 'A job arrived during maintenance entry. API restarted; retry after it completes.'
  fi
  dc stop dispatcher worker_network worker_render
  warn 'Application is in maintenance. If a later step fails, inspect it before restarting services.'
}
remember_release() {
  local image_id
  image_id=$(docker inspect --format '{{.Image}}' studio-api)
  schema_fingerprint > .deployment/previous-schema
  printf '%s\n' "${image_id#sha256:}" > .deployment/previous-revision
  docker image tag "$image_id" "studio-local:rollback-$(cat .deployment/previous-revision)"
}
start_release() {
  dc up -d --no-deps --wait --wait-timeout 120 postgres redis
  export STUDIO_SAFE_RESUME=0  # Never restart old code automatically after migration begins.
  dc run --rm --no-deps migrate
  dc run --rm --no-deps api python scripts/production_check.py tools
  dc up -d --no-deps --wait --wait-timeout 180 api worker_network worker_render dispatcher
  printf '%s\n' "$STUDIO_RELEASE" > .deployment/active-release
  bash scripts/healthcheck.sh
  git rev-parse HEAD > .deployment/deployed-revision
  log "Deployment verified: $(cat .deployment/deployed-revision)"
}
