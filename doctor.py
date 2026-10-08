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
