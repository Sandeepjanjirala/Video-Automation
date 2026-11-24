"""Stock search contracts. Pixabay is preferred; Pexels is an explicit fallback."""
import collections
import json
import re
import threading
import time
from urllib.parse import urlparse

from .config import DATA
from . import store


def safe_stock_url(url, provider):
    try:
        p = urlparse(url)
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
            return False
        host = p.hostname or ''
        if provider == 'pixabay':
            return host in ('pixabay.com', 'cdn.pixabay.com')
        if provider == 'pexels':
            return host in ('videos.pexels.com', 'player.vimeo.com') or host.endswith('.vimeocdn.com')
    except (ValueError, TypeError):
        pass
    return False


def safe_preview_url(url, provider):
    """Preview frames come from the providers' image hosts."""
    if not isinstance(url, str):
        return False
    try:
        p = urlparse(url)
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
            return False
        return p.hostname == {'pixabay': 'cdn.pixabay.com', 'pexels': 'images.pexels.com'}.get(provider)
    except (ValueError, TypeError):
        return False


# A 1080x1920 crop of landscape footage needs 1920 source rows to stay sharp; larger files only
# when that download stays reasonable. (At 60 MB many 4K clips fell back to 1080p, enlarged 1.8x in a reel.)
SHARP_DOWNLOAD_LIMIT = 120 * 1024 * 1024


def best_file(choices, landscape=False):
    """The file to download. A reel: the smallest with 1920 rows (4K landscape, or a vertical 1080x1920)
    within SHARP_DOWNLOAD_LIMIT, else the tallest one within it (1440 rows are enlarged 1.33x, 1080 rows
    1.8x). A 16:9 video: the one nearest 1920 pixels wide (4K adds nothing)."""
    if not landscape:
        fitting = [v for v in choices if 0 < v.get('size', 0) <= SHARP_DOWNLOAD_LIMIT]
        sharp = [v for v in fitting if v.get('height', 0) >= 1920]
        if sharp:
            return min(sharp, key=lambda v: v['size'])
        taller = [v for v in fitting if v.get('height', 0) > 1080]
        if taller:
            return max(taller, key=lambda v: v['height'])
    return min(choices, key=lambda v: abs(max(v.get('width', 0), v.get('height', 0)) - 1920))


# Requests allowed per window, a little under the providers' limits (Pixabay 100 a minute, Pexels 200 an
# hour). Requests go out at once until the window is full, so a reel's few dozen searches take seconds
# (spacing every request 0.7 s apart made them take half a minute); a long YouTube video then waits for
# the oldest request to leave the window.
WINDOWS = {'pixabay': (90, 60.0), 'pexels': (180, 3600.0)}
LONGEST_WAIT = 65  # seconds; a longer wait (Pexels' hourly window is full) is reported instead
_sent = {}
_pace = threading.Lock()
_queries, _queries_lock = {}, threading.Lock()  # one lock per search


def _wait(provider, now):
    """Seconds until the provider's window has room for one more request."""
    count, window = WINDOWS[provider]
    sent = _sent.setdefault(provider, collections.deque())
    while sent and now - sent[0] >= window:
        sent.popleft()
    return 0.0 if len(sent) < count else sent[0] + window - now


def room(provider):
    """True when a request to the provider could go out now without waiting."""
    with _pace:
        return _wait(provider, time.monotonic()) == 0


def paced(provider, call):
    """Run one provider request within its rate-limit window (WINDOWS), waiting out a rate-limit
    answer (HTTP 429) twice before giving up."""
    for attempt in range(3):
        with _pace:
            wait = _wait(provider, time.monotonic())
            if wait > LONGEST_WAIT:
                raise ValueError(f'{provider.title()} request limit reached for this hour. Try again later or use the other source.')
            if wait > 0:
                time.sleep(wait)
            _sent[provider].append(time.monotonic())
        try:
            return call()
        except ValueError as exc:
            if 'HTTP 429' not in str(exc) or attempt == 2:
                raise
            time.sleep(20 * (attempt + 1))


def second_source(provider='auto'):
    """'pexels' when a scene that searches Pixabay first (provider 'auto') can also ask Pexels now: both
    keys are set and Pexels' hourly window has room. Otherwise None."""
    if provider != 'auto':
        return None
    from .providers import online
    try:
        cfg = online()
    except ValueError:
        return None
    return 'pexels' if cfg.get('pixabay_key') and cfg.get('pexels_key') and room('pexels') else None


def search(query='', video_id=None, provider='auto'):
    from .providers import online
    cfg = online()
    if provider not in ('auto', 'pixabay', 'pexels'):
        raise ValueError('Choose Pixabay, Pexels, or Pixabay first.')
    if not isinstance(query, str) or len(query) > 100:
        raise ValueError('Stock search must be at most 100 characters.')
    if provider == 'auto':
        if video_id is not None:
            raise ValueError('Choose the source provider when downloading a clip.')
        configured = [p for p in ('pixabay', 'pexels') if cfg.get(p + '_key')]
        if not configured:
            raise ValueError('Add a Pixabay or Pexels API key in Settings, or upload your own visual.')
        for source in configured:
            # Do not hide authentication or quota errors as empty search results.
            hits = search(query, provider=source)
            if hits:
                return hits
        return []
    if not cfg.get(provider + '_key'):
        raise ValueError(f'Configure your {provider.title()} API key in Settings.')
    if video_id is not None and (not isinstance(video_id, int) or video_id <= 0):
        raise ValueError('Stock video ID must be a positive integer.')

    key = store.digest({'provider': provider + '-v4', 'query': query, 'id': video_id})
    path = DATA / 'cache' / (key + '.stock.json')
    # Shots of one video often share a phrase: the first search asks the provider, the others wait for its answer.
    with _queries_lock:
        lock = _queries.setdefault(key, threading.Lock())
    with lock:
        try:
            if time.time() - path.stat().st_mtime < 86400:
                return json.loads(path.read_text('utf-8'))
        except (OSError, ValueError):  # not cached yet (or being replaced by another worker)
            pass
        results = _fetch(cfg, provider, query, video_id)
        store.write_cache(path, json.dumps(results))
        return results


def _fetch(cfg, provider, query, video_id):
    """The provider's search (or one clip's details) as the studio's hit records."""
    from .providers import request
    if provider == 'pixabay':
        params = {'key': cfg['pixabay_key'], 'safesearch': 'true', 'per_page': 20}
        params.update({'id': video_id} if video_id else {'q': query})
        raw = paced(provider, lambda: request('GET', 'https://pixabay.com/api/videos/', params=params).json())
        hits = raw.get('hits', [])
    else:
        url = 'https://api.pexels.com/v1/videos/' + (f'videos/{video_id}' if video_id else 'search')
        raw = paced(provider, lambda: request('GET', url, headers={'Authorization': cfg['pexels_key']},
                                              params={} if video_id else {'query': query, 'per_page': 20}).json())
        hits = [raw] if video_id else raw.get('videos', [])
    results = []
    for h in hits:
        if provider == 'pixabay':
            choices = [v for v in h.get('videos', {}).values()
                       if safe_stock_url(v.get('url'), provider) and 0 < v.get('size', 0) < 180 * 1024 * 1024]
            creator, source = h.get('user', ''), h.get('pageURL', '')
            license_url = 'https://pixabay.com/service/license-summary/'
            tags = h.get('tags') or ''
            thumbnails = [(h.get('videos', {}).get(size) or {}).get('thumbnail') for size in ('tiny', 'small', 'medium', 'large')]
            preview = next((url for url in thumbnails if safe_preview_url(url, provider)), '')
        else:
            choices = [{**v, 'url': v['link']} for v in h.get('video_files', [])
                       if v.get('file_type') == 'video/mp4' and safe_stock_url(v.get('link'), provider)
                       and v.get('width') and v.get('height')]
            creator, source = h.get('user', {}).get('name', ''), h.get('url', '')
            license_url = 'https://www.pexels.com/license/'
            # Pexels has no tags; its page address describes the clip ("/video/octopus-swimming-123/").
            tags = ' '.join(re.findall(r'[a-z]+', urlparse(source).path.lower().replace('video', ' ')))
            preview = h.get('image') if safe_preview_url(h.get('image'), provider) else ''
        if not choices:
            continue
        results.append({'id': h['id'], 'provider': provider, 'creator': creator, 'source_url': source,
                        'tags': tags or query or f'{provider.title()} video {h["id"]}',
                        'duration': h.get('duration'), 'video': best_file(choices), 'files': choices, 'license_url': license_url,
                        'preview': preview, 'animation': h.get('type') == 'animation',
                        'low_quality': bool(h.get('isLowQuality')), 'downloads': h.get('downloads') or 0})
    return results
