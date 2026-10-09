import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('REEL_DATA_DIR', str(ROOT / 'data'))).resolve()
DEFAULTS = {
    'gemini_key': '', 'gemini_model': 'gemini-2.5-flash', 'pixabay_key': '',
    'azure_key': '', 'azure_region': '', 'online_enabled': False,
    'tts_provider': 'edge', 'voice': 'te-IN-ShrutiNeural',
    'daily_ai_calls': 20, 'ffmpeg': '', 'ffprobe': '',
}
SECRETS = {'gemini_key', 'pixabay_key', 'azure_key'}


def setup():
    for name in ('assets', 'cache', 'exports', 'tmp'):
        (DATA / name).mkdir(parents=True, exist_ok=True)


def settings():
    setup()
    path = DATA / 'settings.json'
    return DEFAULTS | (json.loads(path.read_text('utf-8')) if path.exists() else {})


def save_settings(values):
    cfg = settings()
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ValueError('Unknown setting')
        if key in SECRETS and not value:
            continue
        cfg[key] = value
    if cfg['tts_provider'] not in ('edge', 'azure'):
        raise ValueError('Choose edge or azure')
    if cfg['voice'] not in ('te-IN-ShrutiNeural', 'te-IN-MohanNeural'):
        raise ValueError('Choose a supported Telugu voice')
    if not isinstance(cfg['online_enabled'], bool):
        raise ValueError('Online setting must be boolean')
    cfg['daily_ai_calls'] = max(1, min(100, int(cfg['daily_ai_calls'])))
    for key in DEFAULTS.keys() - {'online_enabled', 'daily_ai_calls'}:
        if not isinstance(cfg[key], str) or len(cfg[key]) > 2000 or '\n' in cfg[key] or '\r' in cfg[key]:
            raise ValueError('Invalid setting value')
    tmp = DATA / 'settings.tmp'
    tmp.write_text(json.dumps(cfg, indent=2), 'utf-8')
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(DATA / 'settings.json')
    return public_settings()


def public_settings():
    cfg = settings()
    return {**{k: v for k, v in cfg.items() if k not in SECRETS},
            **{k + '_configured': bool(cfg[k]) for k in SECRETS}}


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
