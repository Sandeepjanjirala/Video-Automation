#!/usr/bin/env bash
source "$(dirname -- "$0")/deployment-common.sh"
# Internal entrypoint, only after update.sh holds the maintenance lock.
[[ -e /proc/$$/fd/9 ]] || die 'Use scripts/update.sh, not update-finish.sh.'
bash scripts/preflight.sh
export STUDIO_RELEASE=current
dc build api
start_release
