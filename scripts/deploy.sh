#!/usr/bin/env bash
source "$(dirname -- "$0")/deployment-common.sh"
lock
bash scripts/preflight.sh
export STUDIO_RELEASE=current
dc build api
if docker inspect studio-api >/dev/null 2>&1; then
  remember_release
  quiesce
  backup_database
elif docker inspect studio-postgres >/dev/null 2>&1; then
  # An API container may have been removed while its persistent database survived.
  # Preserve that database before migrations too; do not assume this is a fresh install.
  [[ -z "$(dc ps --status running --services dispatcher worker_network worker_render)" ]] || die 'API is absent but workers are running. Restore the existing API and drain jobs before adoption.'
  backup_database
fi
start_release
