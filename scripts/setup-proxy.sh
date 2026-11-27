#!/usr/bin/env bash
# Optional host administration. Routine deploy/update never invoke this script.
source "$(dirname -- "$0")/deployment-common.sh"
lock
dc config --format json | python3 scripts/deployment_host.py nginx
domain=$(cat .deployment/domain)
target="/etc/nginx/sites-available/studio-$domain.conf"
enabled="/etc/nginx/sites-enabled/studio-$domain.conf"
case "${1:---render}" in
  --render) exit 0 ;;
  --install-http)
    [[ $# -eq 1 ]] || die 'Usage: setup-proxy.sh --install-http'
    if sudo test -e "$target" || sudo test -e "$enabled"; then die 'Existing configuration preserved. Review manually.'; fi
    # A domain already configured under any filename must not be duplicated.
    if sudo nginx -T 2>/dev/null | grep -F "$domain" > /dev/null; then die 'Domain already appears in Nginx; preserve its existing configuration.'; fi
    sudo install -d -m 755 /var/www/studio-acme
    sudo install -m 644 .deployment/nginx-http.conf "$target"
    sudo ln -s "$target" "$enabled"
    if ! sudo nginx -t; then
      sudo unlink "$enabled"
      die 'Nginx validation failed; new site disabled, running server not reloaded. Review the generated file.'
    fi
    sudo systemctl reload nginx
    ;;
  --obtain-certificate)
    [[ $# -eq 2 ]] || die 'Usage: setup-proxy.sh --obtain-certificate EMAIL (explicit certificate request)'
    if sudo openssl x509 -checkend 604800 -noout -in "/etc/letsencrypt/live/$domain/fullchain.pem" >/dev/null 2>&1; then
      log 'Existing valid certificate retained.'
      exit 0
    fi
    sudo certbot certonly --webroot -w /var/www/studio-acme -d "$domain" --email "$2" --agree-tos --non-interactive
    ;;
  --enable-https)
    [[ $# -eq 1 ]] || die 'Usage: setup-proxy.sh --enable-https'
    # Only replace our unchanged bootstrap HTTP configuration. Never overwrite a working HTTPS site.
    sudo cmp -s .deployment/nginx-http.conf "$target" || die 'Existing site differs from generated HTTP bootstrap; preserve and review manually.'
    sudo openssl x509 -checkend 604800 -noout -in "/etc/letsencrypt/live/$domain/fullchain.pem"
    sudo install -m 644 .deployment/nginx-https.conf "$target"
    if ! sudo nginx -t; then
      sudo install -m 644 .deployment/nginx-http.conf "$target"
      die 'HTTPS validation failed; bootstrap restored without reloading.'
    fi
    sudo systemctl reload nginx
    log 'HTTPS enabled. Verify certbot.timer and configure an Nginx reload deploy hook for renewals.'
    ;;
  *) die 'Usage: setup-proxy.sh [--render|--install-http|--obtain-certificate EMAIL|--enable-https]' ;;
esac
