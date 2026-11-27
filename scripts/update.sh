#!/usr/bin/env bash
source "$(dirname -- "$0")/deployment-common.sh"
lock
clean_git
[[ "$(git branch --show-current)" == main ]] || die 'Updates require a clean main branch.'
bash scripts/preflight.sh
git fetch origin main
git merge-base --is-ancestor HEAD origin/main || die 'Local main diverged; resolve without rewriting history.'
if [[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]]; then
  bash scripts/healthcheck.sh
  exit 0
fi
remember_release
quiesce
backup_database
git merge --ff-only origin/main
# Run the new scripts after checkout; keep the inherited flock descriptor held.
exec bash scripts/update-finish.sh
