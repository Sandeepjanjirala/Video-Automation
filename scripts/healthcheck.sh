#!/usr/bin/env bash
source "$(dirname -- "$0")/deployment-common.sh"
[[ $# -eq 0 || ( $# -eq 1 && "$1" == --synthetic ) ]] || die 'Usage: healthcheck.sh [--synthetic]'
failed=0
for service in postgres redis api worker_network worker_render dispatcher; do
  id=$(dc ps -q "$service")
  if [[ -z "$id" ]]; then warn "$service missing"; failed=1; continue; fi
  state=$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id")
  if [[ "$state" != 'running healthy' ]]; then warn "$service: $state"; failed=1; else log "$service healthy"; fi
  docker inspect --format 'Restart count: {{.RestartCount}}' "$id"
done
dc exec -T api python scripts/production_check.py report || failed=1
dc config --format json | python3 scripts/deployment_host.py https || failed=1
if [[ "${1:-}" == --synthetic ]]; then dc exec -T api python scripts/production_check.py synthetic || failed=1; fi
mapfile -t containers < <(dc ps -q)
if [[ ${#containers[@]} -gt 0 ]]; then docker stats --no-stream "${containers[@]}"; fi
free -m
df -h .
if command -v vmstat >/dev/null; then vmstat 1 2; fi
[[ "$failed" == 0 ]] || die 'Health verification failed; review FAIL/WARN entries above.'
log 'All required health checks passed.'
