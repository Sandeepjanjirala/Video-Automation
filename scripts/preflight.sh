#!/usr/bin/env bash
source "$(dirname -- "$0")/deployment-common.sh"
[[ -f /etc/os-release ]] || die 'Linux host required.'
if ! grep -q 'Ubuntu' /etc/os-release; then warn 'Target is Ubuntu 22.04; this host differs.'; fi
docker info > /dev/null
docker compose version --short
# config JSON contains credentials: pipe only, never echo/save it.
dc config --format json | python3 scripts/deployment_host.py preflight
for tool in nginx certbot; do
  if command -v "$tool" >/dev/null; then log "$tool installed"; else warn "$tool missing; provision the HTTPS proxy before public verification."; fi
done
free -m
df -h .
if command -v vmstat >/dev/null; then vmstat 1 2; fi
log 'Preflight complete. Existing host services and certificates were not modified.'
