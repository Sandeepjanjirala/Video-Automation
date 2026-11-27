"""Host-side stdlib utilities. Compose configuration stays in memory, never in logs."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import urllib.request


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inspect(name):
    result = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def validate(config):
    services = config['services']
    env = services['api']['environment']
    db = services['postgres']['environment']
    require(len(env.get('DJANGO_SECRET_KEY', '')) >= 40, 'DJANGO_SECRET_KEY must have at least 40 characters.')
    require(len(env.get('STUDIO_PASSWORD', '')) >= 12, 'STUDIO_PASSWORD must have at least 12 characters.')
    require(len(db.get('POSTGRES_PASSWORD', '')) >= 16, 'Database password must have at least 16 characters; preserve existing credentials and review adoption if weaker.')
    for key in ('POSTGRES_USER', 'POSTGRES_DB', 'POSTGRES_PASSWORD'):
        require(re.fullmatch(r'[A-Za-z0-9_-]+', db[key]), f'{key} must be URL-safe; review encoding without changing existing credentials.')
    domain = env['STUDIO_DOMAIN']
    require(re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', domain) and '.' in domain, 'Invalid domain.')
    require(env.get('REEL_DATA_DIR') == '/app/data', 'Persistent media must use /app/data.')
    require(not services['api']['environment'].get('STUDIO_MIGRATION_DATABASE_URL'), 'Remove host-specific migration URL from container configuration after reviewing credentials.')
    ports = set()
    for service, spec in services.items():
        name = spec.get('container_name')
        existing = inspect(name) if name else None
        if existing:
            labels = existing['Config'].get('Labels') or {}
            require(labels.get('com.docker.compose.project') == config['name'], f'{service}: existing container belongs to another Compose project.')
            require(labels.get('com.docker.compose.service') == service, f'{service}: container name conflict.')
            for mount in spec.get('volumes', []):
                if mount['target'] not in ('/app/data', '/var/lib/postgresql', '/data'):
                    continue  # New read-only operational scripts are not persistent user data.
                actual = next((m for m in existing['Mounts'] if m['Destination'] == mount['target']), None)
                require(actual is not None, f'{service}: missing existing mount; manual adoption review required.')
                if mount['type'] == 'volume':
                    expected = config['volumes'][mount['source']]['name']
                    require(actual.get('Name') == expected, f'{service}: persistent volume mismatch; refusing a new empty database.')
                elif mount['type'] == 'bind':
                    require(Path(actual['Source']).resolve() == Path(mount['source']).resolve(), f'{service}: media path mismatch; preserve the existing bind mount.')
        for port in spec.get('ports', []):
            number = int(port['published'])
            require(number != 8002 and number not in ports, 'Port conflicts with EduNexa or another Studio service.')
            ports.add(number)
            require(port.get('host_ip') == '127.0.0.1', 'Backend ports must bind loopback only.')
            # A running existing service owns its current published port, not arbitrary new ports.
            owned = bool(existing and existing['State']['Running'] and any(
                p['HostPort'] == str(number) and p['HostIp'] == '127.0.0.1'
                for bindings in existing['NetworkSettings'].get('Ports', {}).values() if bindings for p in bindings))
            if not owned:
                with socket.socket() as probe:
                    try:
                        probe.bind(('127.0.0.1', number))
                    except OSError:
                        raise ValueError(f'Port {number} is occupied by another process; do not stop unrelated services.') from None
    require(shutil.disk_usage('.').free >= int(os.environ.get('STUDIO_MIN_DISK_MB', '4096')) * 1024 ** 2,
            'Insufficient free disk space; free space safely before building or backing up.')
    require(os.access('.env', os.R_OK), '.env is unreadable.')
    require(Path('.env').stat().st_mode & 0o077 == 0, 'Protect .env with chmod 600 .env.')
    if Path('/proc/meminfo').exists():
        mem = {line.split(':')[0]: int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()}
        require(mem['MemAvailable'] >= 512 * 1024, 'Less than 512 MiB RAM available; postpone maintenance.')
        if mem['SwapTotal'] == 0:
            print('[WARN] No swap configured. No host setting was changed.')
    data = Path('data')
    require(not data.is_symlink(), 'data/ symlink requires manual mount review.')
    if data.exists():
        require(os.access(data, os.R_OK | os.W_OK | os.X_OK), 'data/ must be writable by the deployment operator.')
    else:
        data.mkdir(mode=0o700)
    print('[OK] Configuration, port ownership, persistent mounts, permissions and free space checked.')
    cert = Path('/etc/letsencrypt/live') / domain / 'fullchain.pem'
    if cert.exists():
        result = subprocess.run(['openssl', 'x509', '-checkend', '604800', '-noout', '-in', str(cert)], capture_output=True)
        require(result.returncode == 0, 'Origin certificate expired, unreadable or expires within seven days.')
        print('[OK] Origin certificate valid for at least seven days.')
    else:
        print('[WARN] Origin certificate not readable/found. Review Nginx certificate paths before adoption.')


def https(config):
    env = config['services']['api']['environment']
    domain = env['STUDIO_DOMAIN']
    port = config['services']['api']['ports'][0]['published']
    for url in (f'http://127.0.0.1:{port}/login', f'https://{domain}/login'):
        with urllib.request.urlopen(url, timeout=15) as response:
            require(response.status == 200 and b'<form' in response.read(1024 * 1024), 'Studio login page not reachable.')
        print('[OK] HTTP verification:', url)
    # Cloudflare edge verification cannot prove the origin certificate is correct.
    context = ssl.create_default_context()
    with socket.create_connection(('127.0.0.1', 443), timeout=10) as conn:
        with context.wrap_socket(conn, server_hostname=domain) as tls:
            cert = tls.getpeercert()
            import time
            require(ssl.cert_time_to_seconds(cert['notAfter']) - time.time() > 7 * 86400,
                    'Origin certificate expires within seven days.')
    print('[OK] Origin TLS chain, hostname and expiry verified (independent of Cloudflare).')


def retention(folder, keep):
    keep = int(keep)
    require(keep >= 1, 'STUDIO_BACKUP_KEEP must be at least one.')
    files = sorted((p for p in Path(folder).glob('studio-*.dump') if not p.is_symlink()
                    and re.fullmatch(r'studio-\d{8}T\d{6}-\d+\.dump', p.name)),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for file in files[keep:]:
        if file.is_symlink() or not re.fullmatch(r'studio-\d{8}T\d{6}-\d+\.dump', file.name):
            continue
        file.unlink()
        file.with_suffix('.dump.sha256').unlink(missing_ok=True)


def nginx(config):
    env = config['services']['api']['environment']
    domain = env['STUDIO_DOMAIN']
    require(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]+', domain), 'Invalid domain.')
    port = int(config['services']['api']['ports'][0]['published'])
    require(port != 8002, 'EduNexa port is prohibited.')
    proxy = f'''    client_max_body_size 220m;
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
    }}
'''
    http = f'''# Generated for Video Studio only; review before installing.
server {{
    listen 80;
    server_name {domain};
    location /.well-known/acme-challenge/ {{ root /var/www/studio-acme; }}
{proxy}}}
'''
    https_config = f'''server {{
    listen 80;
    server_name {domain};
    location /.well-known/acme-challenge/ {{ root /var/www/studio-acme; }}
    location / {{ return 301 https://$host$request_uri; }}
}}
server {{
    listen 443 ssl;
    server_name {domain};
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
{proxy}}}
'''
    for filename, content in [('nginx-http.conf', http), ('nginx-https.conf', https_config), ('domain', domain)]:
        Path('.deployment', filename).write_text(content)
    print('[OK] Review .deployment/nginx-http.conf and nginx-https.conf; no host configuration changed.')


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'retention':
            retention(sys.argv[2], sys.argv[3])
        else:
            config = json.load(sys.stdin)
            {'preflight': validate, 'https': https, 'nginx': nginx}[sys.argv[1]](config)
    except ValueError as exc:
        print(f'[FAIL] {exc}', file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f'[FAIL] {type(exc).__name__}: inspect configuration/connectivity privately; no secrets logged.', file=sys.stderr)
        sys.exit(1)
