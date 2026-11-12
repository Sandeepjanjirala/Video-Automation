import json
import logging
import os
import shutil
import uuid
from pathlib import Path

# httpx logs every request URL at INFO, and Pixabay's API key travels in the URL: keep it to warnings.
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('REEL_DATA_DIR', str(ROOT / 'data'))).resolve()
DEFAULTS = {
    'gemini_key': '', 'gemini_model': '', 'pixabay_key': '', 'pexels_key': '',
    # Tried in order when the main model is out of quota, busy or retired (each has its own free quota).
    'gemini_fallback_models': 'gemini-3-flash-preview, gemini-3.1-flash-lite, gemini-flash-lite-latest, gemini-3.5-flash',
    'azure_key': '', 'azure_region': '', 'sarvam_key': '', 'online_enabled': False,
    'tts_provider': 'edge', 'voice': 'te-IN-MohanNeural',
    'daily_ai_calls': 20, 'ffmpeg': '', 'ffprobe': '',
    # PBKDF2 hash of the password other devices sign in with (studio/access.py); never sent to the browser.
    'access_password_hash': '',
}
SECRETS = {'gemini_key', 'pixabay_key', 'pexels_key', 'azure_key', 'sarvam_key'}
# Keys in the .env file next to the project (git-ignored, so pulls and pushes never touch it) are used
# instead of keys typed in Settings. .env.example lists the names.
ENV_KEYS = {'gemini_key': 'STUDIO_GEMINI_KEY', 'pixabay_key': 'STUDIO_PIXABAY_KEY', 'pexels_key': 'STUDIO_PEXELS_KEY',
            'azure_key': 'STUDIO_AZURE_KEY', 'sarvam_key': 'STUDIO_SARVAM_KEY'}


def load_env_file(path=None):
    """Read KEY=value lines from .env into the environment (variables already set win). The test runner
    turns this off with STUDIO_ENV_FILE= so tests never use real keys."""
    target = os.environ.get('STUDIO_ENV_FILE', str(ROOT / '.env')) if path is None else path
    if not target or not Path(target).is_file():
        return
    for raw in Path(target).read_text('utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        value = value.strip().strip('"').strip("'")
        if name.strip() and value:
            os.environ.setdefault(name.strip(), value)


load_env_file()


def setup():
    for name in ('assets', 'cache', 'exports', 'tmp'):
        (DATA / name).mkdir(parents=True, exist_ok=True)


def settings():
    setup()
    path = DATA / 'settings.json'
    cfg = DEFAULTS | (json.loads(path.read_text('utf-8')) if path.exists() else {})
    for key, name in ENV_KEYS.items():
        if os.environ.get(name):
            cfg[key] = os.environ[name]
    # With keys in .env, online services start on (Settings can still turn them off).
    if 'online_enabled' not in (json.loads(path.read_text('utf-8')) if path.exists() else {}) and os.environ.get('STUDIO_ONLINE') == '1':
        cfg['online_enabled'] = True
    return cfg


def save_settings(values):
    cfg = settings()
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ValueError('Unknown setting')
        if key in SECRETS and not value:
            continue
        cfg[key] = value
    for key in DEFAULTS.keys() - {'online_enabled', 'daily_ai_calls'}:
        if not isinstance(cfg[key], str) or len(cfg[key]) > 2000 or '\n' in cfg[key] or '\r' in cfg[key]:
            raise ValueError('Invalid setting value')
    if cfg['tts_provider'] not in ('edge', 'azure', 'sarvam'):
        raise ValueError('Choose edge, azure, or sarvam')
    from .voice.registry import TELUGU_VOICE_CATALOG
    if cfg['voice'] not in TELUGU_VOICE_CATALOG and not cfg['voice'].startswith(('te-IN-', 'azure:')):
        raise ValueError('Choose a supported Telugu voice')
    if not isinstance(cfg['online_enabled'], bool):
        raise ValueError('Online setting must be boolean')
    try:
        cfg['daily_ai_calls'] = max(1, min(100, int(cfg['daily_ai_calls'])))
    except (TypeError, ValueError):
        raise ValueError('Daily AI calls must be an integer') from None
    for name in ('ffmpeg', 'ffprobe'):
        # The studio runs this program, so it must be the real tool on this computer (not a network share).
        if cfg[name] and (Path(cfg[name]).name.lower() not in (name, name + '.exe') or cfg[name].startswith(('\\', '//'))
                          or not Path(cfg[name]).is_file()):
            raise ValueError(f'{name} must be the full path of {name}.exe on this computer')
    # Keys that come from .env stay there: they are not copied into settings.json, so removing one from
    # .env removes it (a key typed in Settings in the same save is stored as typed).
    path = DATA / 'settings.json'
    on_disk = json.loads(path.read_text('utf-8')) if path.exists() else {}
    for key, name in ENV_KEYS.items():
        if os.environ.get(name) and not values.get(key):
            cfg[key] = on_disk.get(key, '')
    tmp = DATA / f'settings.{uuid.uuid4().hex}.tmp'
    tmp.write_text(json.dumps(cfg, indent=2), 'utf-8')
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(DATA / 'settings.json')
    return public_settings()


def public_settings():
    cfg = settings()
    return {**{k: v for k, v in cfg.items() if k not in SECRETS and k != 'access_password_hash'},
            **{k + '_configured': bool(cfg[k]) for k in SECRETS},
            **{k + '_from_env': bool(os.environ.get(name)) for k, name in ENV_KEYS.items()},
            'access_password_set': bool(cfg.get('access_password_hash')),
            'access_password_from_server': bool(os.environ.get('STUDIO_PASSWORD'))}


def executable(name):
    configured = settings().get(name)
    if configured:
        if os.name != 'nt' and ('.exe' in configured.lower() or '\\' in configured or (len(configured) > 1 and configured[1] == ':')):
            configured = None
        elif os.name == 'nt' and ('/' in configured and not Path(configured).exists()):
            configured = None
    found = configured or shutil.which(name)
    if not found:
        raise ValueError(f'{name} is missing. Set its full path in Settings.')
    return str(found)
