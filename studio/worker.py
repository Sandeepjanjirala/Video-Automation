import os
import time
from . import store
from .config import DATA, settings


def process(job):
    try:
        p = job['snapshot']['project']
        if job['kind'] == 'render':
            pf = store.preflight(p)
            if not pf['ready']:
                raise ValueError(f"Preflight validation failed: {pf['errors'][0]['message']}")
            from .media import render
            result = render(job)
        elif job['kind'] == 'voice':
            if not p['scenes'] or any(not s.get('narration', '').strip() for s in p['scenes']):
                raise ValueError('Cannot prepare voice: every scene needs Telugu narration text')
            from .media import prepare_voices
            result = {'voices': prepare_voices(job)}
        elif job['kind'] == 'research':
            from .providers import research
            result = research(job['snapshot']['project'])
        elif job['kind'] == 'draft':
            from .providers import draft
            result = draft(job['snapshot']['project'], job['snapshot']['options'].get('script', ''))
        elif job['kind'] == 'stock':
            from .providers import download_stock
            result = {'asset': download_stock(job['snapshot']['options']['video_id']),
                      'scene_id': job['snapshot']['options']['scene_id']}
        elif job['kind'] == 'stock_all':
            from .providers import download_stock, stock_search
            selected, used = [], set()
            scenes = [s for s in job['snapshot']['project']['scenes'] if s['visual'] != 'graphic' and not s['video_asset']]
            for i, scene in enumerate(scenes):
                store.progress(job['id'], f'Finding footage {i + 1}/{len(scenes)}')
                if not scene['keywords'].strip():
                    selected.append({'scene_id': scene['id'], 'error': 'Add English search keywords'})
                    continue
                try:
                    candidates = stock_search(scene['keywords'])
                    candidates.sort(key=lambda h: (h['id'] in used, h['video']['height'] < h['video']['width']))
                    if not candidates:
                        raise ValueError('No suitable stock result. Upload footage or choose a graphic.')
                    hit = candidates[0]
                    selected.append({'scene_id': scene['id'], 'asset': download_stock(hit['id'])})
                    used.add(hit['id'])
                except ValueError as exc:
                    selected.append({'scene_id': scene['id'], 'error': str(exc)})
            result = {'selections': selected, 'note': 'Keyword-selected candidates. Review relevance, framing, and rights before rendering.'}
        else:
            raise ValueError('Unknown job type')
        store.progress(job['id'], 'Complete', 'succeeded', result)
    except Exception as exc:
        # Never store credentials, request URLs or provider response bodies in jobs.
        message = str(exc) if isinstance(exc, ValueError) else f'{type(exc).__name__}: processing failed'
        for key in ('gemini_key', 'pixabay_key', 'azure_key'):
            secret = settings()[key]
            if secret:
                message = message.replace(secret, '[redacted]')
        store.progress(job['id'], message[:2000], 'failed')


def main():
    store.init()
    lock = (DATA / 'worker.lock').open('a+b')
    lock.seek(0)
    lock.write(b'0')
    lock.flush()
    lock.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit('A worker is already running for this data folder')
    with store.connection() as db:
        db.execute("UPDATE jobs SET state='failed',progress='Interrupted by shutdown. Retry to resume cached work.' WHERE state='running'")
    print('Telugu Reel Studio worker ready', flush=True)
    try:
        while True:
            (DATA / 'worker-heartbeat.txt').write_text(str(time.time()), 'utf-8')
            job = store.claim()
            if job:
                process(job)
            else:
                time.sleep(0.5)
    finally:
        lock.close()


if __name__ == '__main__':
    main()
