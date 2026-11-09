"""Stock search contracts. Pixabay is preferred; Pexels is an explicit fallback."""
import json
import time
import uuid
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


def search(query='', video_id=None, provider='auto'):
    from .providers import online, request
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

    key = store.digest({'provider': provider + '-v2', 'query': query, 'id': video_id})
    path = DATA / 'cache' / (key + '.stock.json')
    if path.exists() and time.time() - path.stat().st_mtime < 86400:
        return json.loads(path.read_text('utf-8'))
    if provider == 'pixabay':
        params = {'key': cfg['pixabay_key'], 'safesearch': 'true', 'per_page': 8}
        params.update({'id': video_id} if video_id else {'q': query})
        raw = request('GET', 'https://pixabay.com/api/videos/', params=params).json()
        hits = raw.get('hits', [])
    else:
        url = 'https://api.pexels.com/v1/videos/' + (f'videos/{video_id}' if video_id else 'search')
        raw = request('GET', url, headers={'Authorization': cfg['pexels_key']},
                      params={} if video_id else {'query': query, 'per_page': 8}).json()
        hits = [raw] if video_id else raw.get('videos', [])
    results = []
    for h in hits:
        if provider == 'pixabay':
            choices = [v for v in h.get('videos', {}).values()
                       if safe_stock_url(v.get('url'), provider) and 0 < v.get('size', 0) < 180 * 1024 * 1024]
            creator, source = h.get('user', ''), h.get('pageURL', '')
            license_url = 'https://pixabay.com/service/license-summary/'
        else:
            choices = [{**v, 'url': v['link']} for v in h.get('video_files', [])
                       if v.get('file_type') == 'video/mp4' and safe_stock_url(v.get('link'), provider)
                       and v.get('width') and v.get('height')]
            creator, source = h.get('user', {}).get('name', ''), h.get('url', '')
            license_url = 'https://www.pexels.com/license/'
        if not choices:
            continue
        selected = min(choices, key=lambda v: abs(max(v.get('width', 0), v.get('height', 0)) - 1920))
        results.append({'id': h['id'], 'provider': provider, 'creator': creator, 'source_url': source,
                        'tags': h.get('tags') or query or f'{provider.title()} video {h["id"]}',
                        'duration': h.get('duration'), 'video': selected, 'license_url': license_url})
    temp = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
    temp.write_text(json.dumps(results), 'utf-8')
    temp.replace(path)
    return results
