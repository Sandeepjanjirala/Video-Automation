from studio.config import executable, public_settings
from studio.media import run
import sys

print('Python:', sys.version.split()[0])
for name in ('ffmpeg', 'ffprobe'):
    try:
        print(name + ':', run([executable(name), '-version'], timeout=10).splitlines()[0])
    except ValueError as exc:
        print(name + ':', exc)
try:
    filters = run([executable('ffmpeg'), '-hide_banner', '-filters'], timeout=10)
    for name in ('subtitles', 'loudnorm', 'sidechaincompress'):
        print(name, 'available' if name in filters else 'MISSING')
except ValueError:
    pass
cfg = public_settings()
for key in ('online_enabled', 'tts_provider', 'gemini_key_configured', 'pixabay_key_configured', 'azure_key_configured'):
    print(key + ':', cfg[key])

try:
    from studio.db import check_connection, get_database_url
    check_connection()
    url = get_database_url()
    safe_url = url.split('@')[-1] if '@' in url else url
    print('postgresql: connected (' + safe_url + ')')
except Exception as exc:
    print('postgresql: ERROR (' + str(exc).splitlines()[0] + ')')

try:
    from studio.celery_app import app as celery_app
    import redis
    broker_url = celery_app.conf.broker_url
    r = redis.Redis.from_url(broker_url, socket_timeout=2)
    info = r.info('memory')
    print('redis: connected (' + broker_url + ', maxmemory=' + str(info.get('maxmemory_human', 'N/A')) + ')')
    print('celery: configured (queues=' + ', '.join(celery_app.conf.task_queues.keys()) + ')')
except Exception as exc:
    print('redis/celery: ERROR (' + str(exc).splitlines()[0] + ')')
