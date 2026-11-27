#!/usr/bin/env bash
source "$(dirname -- "$0")/deployment-common.sh"
lock
clean_git
[[ -f .deployment/previous-revision ]] || die 'No recorded release to roll back to.'
revision=$(cat .deployment/previous-revision)
[[ "$revision" =~ ^[a-f0-9]{64}$ ]] || die 'Invalid recorded image ID.'
docker image inspect "studio-local:rollback-$revision" > /dev/null
contract=$(docker image inspect --format '{{ index .Config.Labels "io.video-studio.deployment-contract" }}' "studio-local:rollback-$revision")
[[ "$contract" == 1 ]] || die 'Retained image predates the supported deployment contract. Services were not stopped; recover with its original Compose configuration after reviewing schema compatibility.'
[[ -f .deployment/previous-schema && "$(schema_fingerprint)" == "$(cat .deployment/previous-schema)" ]] || die 'Migration history changed since the retained image. Automatic rollback refused; review schema compatibility manually.'
quiesce
backup_database
export STUDIO_RELEASE="rollback-$revision"
# Old application must accept the current schema. This never reverses migrations.
dc run --rm --no-deps api python manage.py migrate --check
export STUDIO_SAFE_RESUME=0
dc up -d --no-deps --no-build --wait --wait-timeout 180 api worker_network worker_render dispatcher
printf '%s\n' "$STUDIO_RELEASE" > .deployment/active-release
bash scripts/healthcheck.sh
printf '%s\n' "$revision" > .deployment/deployed-revision
warn "Old image is running; code checkout was not rewritten. Diagnose before the next update. Database unchanged."
