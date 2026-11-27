#!/usr/bin/env bash
source "$(dirname -- "$0")/deployment-common.sh"
lock
[[ $# -eq 0 || ( $# -eq 1 && ( "$1" == --dry-run || "$1" == --apply ) ) ]] || die 'Usage: cleanup.sh [--dry-run|--apply]'
du -sh data backups
if [[ "${1:---dry-run}" == --apply ]]; then
  quiesce
  dc run --rm --no-deps -e "STUDIO_TEMP_DAYS=${STUDIO_TEMP_DAYS:-7}" api python scripts/production_check.py cleanup --apply
  resume_services
else
  dc exec -T -e "STUDIO_TEMP_DAYS=${STUDIO_TEMP_DAYS:-7}" api python scripts/production_check.py cleanup
fi
log 'Only expired data/tmp children are eligible. Uploads, exports, cache and projects are retained.'
