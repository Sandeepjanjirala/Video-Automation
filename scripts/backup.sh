#!/usr/bin/env bash
source "$(dirname -- "$0")/deployment-common.sh"
lock
[[ $# -eq 0 || ( $# -eq 1 && "$1" == --media ) ]] || die 'Usage: backup.sh [--media]'
if [[ "${1:-}" == --media ]]; then
  quiesce
  backup_database
  archive="$ROOT/backups/studio-media-$(date -u +%Y%m%dT%H%M%S)-$$.tar.gz"
  tar -czf "$archive.partial" -C "$ROOT" data
  tar -tzf "$archive.partial" > /dev/null
  mv -- "$archive.partial" "$archive"
  (cd backups && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
  resume_services
  log "Media backup: $archive (contains provider settings; protect it)."
else
  backup_database
fi
