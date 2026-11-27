#!/usr/bin/env bash
source "$(dirname -- "$0")/deployment-common.sh"
lock
[[ $# -eq 3 && "$2" == --into-new-database ]] || die 'Usage: restore.sh BACKUP.dump --into-new-database studio_restore_NAME'
archive=$(realpath -- "$1")
target=$3
[[ "$target" =~ ^studio_restore_[a-z0-9_]+$ ]] || die 'Target must start studio_restore_ and contain lowercase letters, digits or underscores.'
[[ -f "$archive.sha256" ]] || die 'Missing checksum file.'
(cd "$(dirname -- "$archive")" && sha256sum --check "$(basename "$archive").sha256")
dc exec -T postgres pg_restore --list < "$archive" > /dev/null
# createdb refuses an existing target. No DROP, --clean or production overwrite.
dc exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$target"
dc exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" --exit-on-error --single-transaction --no-owner -d "$1"' sh "$target" < "$archive"
log "Restored to isolated database $target. Production still uses its original database."
