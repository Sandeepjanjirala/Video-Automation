"""Footage matching for "Find footage for all scenes" and the one-click draft.

The scenes are matched in a plan -> search -> check -> retry loop (the shape LangGraph agents use,
written directly so it needs no extra framework and works with the backup Gemini models):

1. Plan: Gemini reads the whole script and plans, for each scene, one shot per part of its line in
   spoken order (a long line gets several), each with search phrases from specific to broad.
2. Search wide: every phrase is searched; candidates are ranked by tag match, real film over
   animation, resolution, a shape that suits the video format and popularity.
3. Check: Gemini looks at preview frames of the best candidates for every shot and scores the clips
   that show it.
4. Retry: shots with no clip, or only a weak one, are planned again from what was rejected (a
   different, findable view of the same idea), searched and checked again; the better clip wins.

Each scene's clips play in the order of its words, each for the share of the line it covers. Without
Gemini the scene keywords and the ranking alone are used. Works for vertical reels and landscape
YouTube videos (studio/formats.py).
"""
import json
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from . import formats, providers
from .framing import SHOT_SECONDS
from .stock import safe_preview_url

log = logging.getLogger('studio.footage')
MIN_SCORE = 5      # Gemini's 0-10 fit score below which a shot gets no clip
GOOD_SCORE = 7     # a shot whose best clip scores lower is planned and searched again once
MAX_SHOTS = 4      # clips per scene: its own and up to three extra shots
PLAN_BATCH = 40    # scenes planned per Gemini request (the whole script is always sent as context)
# Work done at the same time: searches (still spaced to the provider's rate limit in stock.paced),
# Gemini requests for long videos, and clip downloads.
SEARCH_WORKERS, GEMINI_WORKERS, DOWNLOAD_WORKERS = 4, 2, 4
MAX_QUERIES = 3    # planned phrases (plus keywords) searched per shot; the subject word may add one
# Candidates per shot that Gemini sees (a preview costs ~260 tokens) and shots per check request.
# A long YouTube video has hundreds of scenes, so it shows fewer candidates in bigger batches.
SHOWN = {'reel': 10, 'youtube': 6}
PART_SHOWN = 6     # candidates shown for one part of a long line (it needs one clip)
PICK_BATCH = {'reel': 12, 'youtube': 16}
STOP = set('a an and the of in on at to for with from into by is are was be this that it its his her their '
           'video footage clip stock shot scene view close closeup up'.split())
# Editing material rather than footage: green screens and subscribe or logo overlays.
UNUSABLE = re.compile(r'green ?screen|chroma|subscribe|lower third|logo reveal|\bintro\b', re.I)
NO_MATCH = 'No stock clip matched this scene well. Upload footage or use a title card.'

PLAN_PROMPT = '''You are the editor of a professional {orientation} with Telugu narration.
Read the whole script first and work out its topic, the story it tells and its mood. Then, for each
scene listed in NEED, decide what the viewer should SEE while hearing that line, as stock footage.
Rules:
- Show the specific thing the words talk about, literally: "Octopus ki moodu hearts untayi" (an octopus
  has three hearts) -> an octopus, not a heart or a heartbeat line.
- For words without a filmable subject (the title, hooks, "did you know", questions, "comment below"),
  show the video's main subject or a fitting human reaction, e.g. "amazed young woman looking at phone".
  Never use abstract words such as facts, information, shocking, concept, question or engagement.
- Real camera footage, sharp and well lit, like a documentary or a premium brand film. No text, titles,
  logos, charts, infographics, cartoons or slideshows, unless the subject can only be shown that way
  (planets, blood cells, atoms: realistic 3D animation is fine).
- Plan shots that stock libraries really have. When a detail cannot be filmed (inside a body, a
  number, a duration, a slow process, a rare event), show the subject itself; a line that continues the
  previous one shows the same subject in a different shot (another angle, a close-up, a wider view).
- Keep a consistent look across the video and do not plan the same shot twice.
NEED lists the scenes to plan; SHOTS PER SCENE gives how many shots each needs. Split a scene's line into
that many consecutive parts, in the order they are spoken, and plan one shot per part, so each picture appears
while its own words are said (one shot: the whole line).
For each shot return: words (the exact words of the line it covers, copied from the line); shot (one
sentence describing the ideal frame); queries (3 English search phrases for a stock library such as
Pixabay, 1 to 3 words each, using words such clips are tagged with, most specific first, the last broad
enough to always return results, e.g. "octopus tentacles", "octopus", "ocean underwater"; no adjectives
such as cinematic, beautiful or 4k; no numbers; no people's names).
Return JSON {{"scenes": [{{"index": 0, "shots": [{{"words": "...", "shot": "...", "queries": ["...", "...", "..."]}}]}}]}}.
'''

PICK_PROMPT = '''You are the editor of a professional {orientation} with Telugu narration.
For each shot below you see up to {shown} candidate stock clips, one preview frame each. Judge each
shot on its own: which clips show the shot's intended picture, as a viewer would expect while hearing
its words? Judge what is actually visible in the frame, not the clip name. Prefer real, sharp,
well-lit, professional footage{shape}. Reject clips that are off-topic, low quality, watermarked, green
screen, or covered in text, buttons or logos (such as "subscribe" graphics).
Score each acceptable clip from 0 to 10, honestly: 8 or more when the frame clearly shows the subject
of the intended shot; 6 or 7 for the right subject in a different kind of shot, or, for words without
a concrete subject (hook, question, "comment below"), a good-looking clip of the planned kind; 4 or
less for a loosely related clip.
A scene that "needs N clips" is long: the video cuts between N different clips during it to stay
engaging, so list at least N acceptable clips when they exist, preferably different angles or
moments of the subject rather than near-identical frames.
For every shot return its best clips, best first, at most 5, leaving out any scoring below {minimum}
(an empty list when none fits). The same clip may be listed for several shots.
Return JSON {{"scenes": [{{"index": 0, "shot": 1, "picks": [{{"candidate": 2, "score": 8}}, {{"candidate": 5, "score": 6}}]}}]}}
with index = the scene number and shot = the shot number shown (1 when the scene has one shot).
Script for context: {script}
'''

RETRY_PROMPT = '''You are the editor of a professional {orientation} with Telugu narration. The shots
below found no good stock clip: the library does not have what was planned, or the search words did not
reach it. For each, plan the same idea again as something stock libraries really have: the main subject
itself, a wider or closer view, a related place, object or a person reacting, never an abstract idea.
Use search words different from the ones already tried (they returned the clips listed as seen).
Rules for queries: 3 English phrases of 1 to 3 words that stock clips are tagged with, most specific
first, the last broad; no adjectives such as cinematic or 4k; no numbers; no people's names.
Return JSON {{"scenes": [{{"index": 0, "shot": 1, "description": "...", "queries": ["...", "...", "..."]}}]}}.
Script for context: {script}
SHOTS: {shots}
'''


def words(text):
    out = set()
    for w in re.findall(r'[a-z]+', (text or '').lower()):
        if len(w) > 2 and w not in STOP:
            out.add(w[:-1] if len(w) > 4 and w.endswith('s') and not w.endswith(('ss', 'us')) else w)
    return out


def parse(text):
    return json.loads(text.strip().removeprefix('```json').removesuffix('```').strip())['scenes']


def line(scene):
    return scene.get('roman') or scene.get('narration') or scene.get('graphic_title') or ''


def batches(items, size):
    return [items[k:k + size] for k in range(0, len(items), size)]


def parallel(fn, items, workers):
    """fn over items with up to `workers` at once, results in item order."""
    if len(items) <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(min(workers, len(items))) as pool:
        return list(pool.map(fn, items))


def close_db():
    """Worker threads open their own database connection (downloads record assets); close it."""
    import threading
    if threading.current_thread() is not threading.main_thread():
        from django.db import connections
        connections.close_all()


def clean_queries(values, limit=3):
    return [providers.clean_caption(str(q))[:100] for q in values or [] if str(q).strip()][:limit]


def plan(project, need, progress=lambda text: None):
    """{scene index: {'shot', 'queries', 'shots': [{'words', 'shot', 'queries'}, ...]}} from Gemini,
    PLAN_BATCH scenes per request, shots in spoken order; scenes of a batch Gemini could not plan are
    left out (their keywords are searched instead)."""
    shot_seconds = formats.of(project)['shot_seconds']
    script = [{'index': i, 'line': line(s), 'keywords': s.get('keywords', '')} for i, s in enumerate(project['scenes'])]
    head = (PLAN_PROMPT.format(orientation=formats.of(project)['orientation'])
            + f"Topic: {project.get('topic') or project.get('title') or ''}\n"
            + f"SCRIPT: {json.dumps(script, ensure_ascii=False)}\n")
    plans, groups = {}, batches(need, PLAN_BATCH)

    def ask(group):
        wanted = {i: shots_needed(project['scenes'][i], shot_seconds) for i in group}
        try:
            # Thinking off: as good a plan in a third of the time (and fewer malformed answers).
            return group, parse(providers.gemini(head + f'SHOTS PER SCENE: {json.dumps(wanted)}\nNEED: {json.dumps(group)}',
                                                 fast=True)[0])
        except (KeyError, TypeError, ValueError) as exc:
            log.warning('footage plan unavailable: %s', exc)
            return group, []
        finally:
            close_db()
    answers = parallel(ask, groups, GEMINI_WORKERS)
    for k, (group, rows) in enumerate(answers, 1):
        if len(groups) > 1:
            progress(f'Reading the script and planning shots ({k}/{len(groups)})')
        for row in rows if isinstance(rows, list) else []:
            try:
                i = int(row['index'])
                listed = row.get('shots') if isinstance(row.get('shots'), list) else [row]
                shots = [{'words': str(s.get('words') or '').strip()[:400],
                          'shot': providers.clean_caption(str(s.get('shot') or ''))[:300],
                          'queries': clean_queries(s.get('queries'))} for s in listed if isinstance(s, dict)]
            except (KeyError, TypeError, ValueError):
                continue
            shots = [s for s in shots if s['queries']][:MAX_SHOTS]
            if i in group and shots:
                plans[i] = {'shot': shots[0]['shot'], 'queries': shots[0]['queries'], 'shots': shots}
    return plans


def weights(queries):
    """Each word weighs as many phrases as use it, so the subject ("octopus" in "octopus tentacles"
    and "octopus eye") outweighs a detail ("eye")."""
    out = {}
    for query in queries:
        for word in words(query):
            out[word] = out.get(word, 0) + 1
    return out


def rank(hit, weight, position, landscape=False):
    """Higher is better: the provider's own relevance order (it also finds clips tagged
    "cephalopod" for "octopus"), tag match, real film, sharpness, a shape that fills the frame
    without heavy cropping, and popularity."""
    video = hit.get('video') or {}
    width, height = video.get('width', 0), video.get('height', 0)
    short = min(width, height)
    tags = words(hit.get('tags'))
    score = 1.5 * max(0, 1 - position / 20)
    score += 3 * sum(w for word, w in weight.items() if word in tags) / max(1, sum(weight.values()))
    score += -0.4 if hit.get('animation') else 0.4
    score += 0.5 if short >= 1080 else 0.2 if short >= 720 else -1
    if landscape:
        score += 0.4 if width > height else -1      # a vertical clip would lose two thirds in 16:9
    else:
        score += 0.3 if height > width else 0
    score += 0.1 * math.log10(1 + (hit.get('downloads') or 0))
    score -= 2 if hit.get('low_quality') else 0
    score -= 2 if UNUSABLE.search(hit.get('tags') or '') else 0
    score -= 0.5 if (hit.get('duration') or 10) < 4 else 0
    return score


def candidates(scene, queries, landscape=False):
    """Search every phrase and return candidates, best first, taking turns between the phrases
    so every idea of the plan is among the clips Gemini sees."""
    lists, error, weight = [], None, weights(queries)
    # The subject on its own ("octopus") gets the cleanest results; providers match longer phrases loosely.
    subject = max(weight, key=lambda word: (weight[word], -len(word)), default='')
    if weight.get(subject, 0) > 1 and subject not in queries:
        queries = [*queries, next((w for w in re.findall(r'[a-z]+', ' '.join(queries).lower()) if words(w) == {subject}), subject)]
    for query in queries:
        try:
            hits = providers.stock_search(query, provider=scene.get('stock_provider', 'auto'))
        except ValueError as exc:
            error = exc
            continue
        lists.append([hit for _, _, hit in sorted(((rank(hit, weight, p, landscape), p, hit) for p, hit in enumerate(hits)),
                                                  key=lambda item: (-item[0], item[1]))])
    if not any(lists) and error:
        raise error
    out, seen = [], set()
    queues = [iter(hits) for hits in lists]
    while queues:
        for queue in list(queues):  # each phrase in turn adds its best clip not yet taken
            hit = next((h for h in queue if (h.get('provider', 'pixabay'), h['id']) not in seen), None)
            if hit is None:
                queues.remove(queue)
                continue
            seen.add((hit.get('provider', 'pixabay'), hit['id']))
            out.append(hit)
    return out


def preview(hit):
    """(mime type, bytes) of the clip's preview frame, or None."""
    url, provider = hit.get('preview') or '', hit.get('provider', 'pixabay')
    if not safe_preview_url(url, provider):
        return None
    try:
        response = httpx.get(url, timeout=20, follow_redirects=False)
    except httpx.HTTPError:
        return None
    mime = response.headers.get('content-type', '').split(';')[0].strip()
    if response.status_code != 200 or mime not in ('image/jpeg', 'image/png', 'image/webp') or len(response.content) > 2_000_000:
        return None
    return mime, response.content


def shots_needed(scene, shot_seconds=SHOT_SECONDS):
    """Clips a scene needs so the picture changes about every `shot_seconds` (4 in a reel, 6 in a
    YouTube video), from its estimated narration length (Edge Mohan reads about two Roman Telugu
    words a second)."""
    seconds = 0.6 + len((scene.get('roman') or scene.get('narration') or '').split()) / 2.0
    return min(MAX_SHOTS, max(1, math.ceil(seconds / shot_seconds - 0.05)))


def key(hit):
    return hit.get('provider', 'pixabay'), hit['id']


def pick(project, scenes, units, pools, progress=lambda text: None, message='Checking candidate clips with AI'):
    """{(scene, shot): [(hit, score), ...] best first} judged by Gemini from preview frames (an empty
    list when no clip fits); shots Gemini could not judge are left out (the ranking is used).
    `units` maps (scene, shot) to {'words', 'shot', 'count'} (count = shots planned for the scene)."""
    kind = project.get('format') or 'reel'
    shown, shot_seconds = SHOWN.get(kind, 10), formats.of(project)['shot_seconds']
    script = json.dumps([line(s) for s in project['scenes']], ensure_ascii=False)
    prompt = PICK_PROMPT.format(orientation=formats.of(project)['orientation'], shown=shown, minimum=MIN_SCORE, script=script,
                                shape='; a landscape clip that fills a 16:9 frame' if formats.landscape(project) else '')
    chosen, groups = {}, batches(sorted(u for u in pools if pools[u]), PICK_BATCH.get(kind, 12))
    progress(message + (f' ({len(groups)} batches)' if len(groups) > 1 else ''))

    def judge(group):
        try:
            return judge_batch(group)
        finally:
            close_db()

    def judge_batch(group):
        found = {}
        # A part of a long line needs one clip, so it shows fewer candidates (a smaller, faster request).
        flat = [(u, n, hit) for u in group
                for n, hit in enumerate(pools[u][:shown if units.get(u, {}).get('count', 1) == 1 else min(shown, PART_SHOWN)], 1)]
        with ThreadPoolExecutor(8) as pool:
            frames = list(pool.map(lambda item: preview(item[2]), flat))
        parts, offered = [], {}
        for ((i, k), n, hit), frame in zip(flat, frames):
            if frame is None:
                continue
            unit = units.get((i, k), {})
            several = unit.get('count', 1) > 1
            name = f'Scene {i} shot {k}' if several else f'Scene {i}'
            label = f'{name} candidate {n} (clip {hit.get("provider", "pixabay")}-{hit["id"]}):'
            if (i, k) not in offered:  # the first frame of a shot carries its words, intended picture and clip count
                intended = unit.get('shot') or scenes[i].get('keywords', '')
                if several:
                    label = (f'\n{name} of {unit["count"]}: said while {json.dumps(unit.get("words") or line(scenes[i]), ensure_ascii=False)} '
                             f'(line {json.dumps(line(scenes[i]), ensure_ascii=False)}); intended shot: {intended}\n' + label)
                else:
                    need = shots_needed(scenes[i], shot_seconds)
                    label = (f'\nScene {i}' + (f' (needs {need} clips)' if need > 1 else '') + ': line '
                             f'{json.dumps(line(scenes[i]), ensure_ascii=False)}; intended shot: {intended}\n' + label)
            offered.setdefault((i, k), {})[n] = hit
            parts.append((label, *frame))
        if not offered:
            return found
        try:
            rows = parse(providers.gemini(prompt, images=parts, fast=True)[0])  # thinking off: about twice as fast
        except (KeyError, TypeError, ValueError) as exc:
            log.warning('footage pick unavailable: %s', exc)
            return found
        for row in rows if isinstance(rows, list) else []:
            try:
                unit = (int(row['index']), int(row.get('shot') or 1))
                picks = [(int(p['candidate']), float(p['score'])) for p in row.get('picks') or []]
            except (KeyError, TypeError, ValueError):
                continue
            if unit in offered:
                found[unit] = [(offered[unit][n], score) for n, score in picks if n in offered[unit] and score >= MIN_SCORE]
        return found
    for found in parallel(judge, groups, GEMINI_WORKERS):
        chosen.update(found)
    return chosen


def retry(project, scenes, units, pools, chosen, landscape, progress):
    """Plan, search and check again the shots whose best clip is missing or weak; keep the better clips.
    Returns the updated `chosen` (pools of retried shots gain the new candidates)."""
    weak = sorted({u for u in chosen if not chosen[u] or chosen[u][0][1] < GOOD_SCORE}
                  | {u for u in units if u in pools and not pools[u]})  # nothing found at all
    if not weak:
        return chosen
    progress(f'Looking again for better clips ({len(weak)} shot(s))')
    shots = [{'index': i, 'shot': k, 'line': line(scenes[i]), 'words': units[(i, k)].get('words') or line(scenes[i]),
              'planned': units[(i, k)].get('shot', ''), 'tried': units[(i, k)].get('queries', []),
              'seen': [h.get('tags', '')[:60] for h in pools.get((i, k), [])[:6]]} for i, k in weak]
    prompt = RETRY_PROMPT.format(orientation=formats.of(project)['orientation'],
                                 script=json.dumps([line(s) for s in project['scenes']], ensure_ascii=False),
                                 shots=json.dumps(shots, ensure_ascii=False))
    try:
        rows = parse(providers.gemini(prompt, fast=True)[0])
    except (KeyError, TypeError, ValueError) as exc:
        log.warning('footage retry unavailable: %s', exc)
        return chosen
    again = {}
    for row in rows if isinstance(rows, list) else []:
        try:
            unit = (int(row['index']), int(row.get('shot') or 1))
        except (KeyError, TypeError, ValueError):
            continue
        queries = [q for q in clean_queries(row.get('queries')) if q not in units.get(unit, {}).get('queries', [])]
        if unit in weak and queries:
            again[unit] = {**units[unit], 'shot': providers.clean_caption(str(row.get('description') or ''))[:300]
                           or units[unit].get('shot', ''), 'queries': queries}

    def search(unit):
        try:
            seen = {key(h) for h in pools.get(unit, [])}
            return unit, [h for h in candidates(scenes[unit[0]], again[unit]['queries'], landscape) if key(h) not in seen]
        except ValueError:
            return unit, []
        finally:
            close_db()
    fresh = {u: hits for u, hits in parallel(search, list(again), SEARCH_WORKERS) if hits}
    if not fresh:
        return chosen
    second = pick(project, scenes, {**units, **again}, fresh, progress, 'Checking the new candidates with AI')
    for unit, picks in second.items():
        pools[unit] = pools.get(unit, []) + fresh[unit]
        before = chosen.get(unit, [])
        chosen[unit] = sorted(before + picks, key=lambda pair: -pair[1])
        if picks and (not before or picks[0][1] > before[0][1]):  # the new plan found the better clip
            units[unit] = {**again[unit], 'retried': True}
    return chosen


def find(project, scenes, progress=lambda text: None):
    """Download the best clips for each scene. Returns stock_all selections: asset (plus extra shots in
    spoken order and their share of the line) or error per scene."""
    index = {s['id']: i for i, s in enumerate(project['scenes'])}
    by_index = {index[s['id']]: s for s in scenes}
    landscape, shot_seconds = formats.landscape(project), formats.of(project)['shot_seconds']
    started = time.monotonic()
    progress('Reading the script and planning shots')
    plans = plan(project, sorted(by_index), progress) if by_index else {}
    planned = time.monotonic()
    # One unit per planned shot (scene, shot number from 1); unplanned scenes search their keywords.
    units, errors = {}, {}
    for i, scene in sorted(by_index.items()):
        keywords = (scene.get('keywords') or '').strip()
        shots = plans.get(i, {}).get('shots') or [{'words': line(scene), 'shot': '', 'queries': []}]
        for k, shot in enumerate(shots, 1):
            # Later parts search their most specific and their broad phrase (fewer rate-limited searches).
            mine = shot['queries'] if k == 1 else list(dict.fromkeys(shot['queries'][:1] + shot['queries'][-1:]))
            queries = list(dict.fromkeys(mine + ([keywords] if keywords and k == 1 else [])))[:MAX_QUERIES]
            if queries:
                units[(i, k)] = {**shot, 'queries': queries, 'count': len(shots)}
        if (i, 1) not in units:
            errors[i] = 'Add English search keywords'

    pools = {}

    def search(unit):
        try:
            return unit, candidates(by_index[unit[0]], units[unit]['queries'], landscape), None
        except ValueError as exc:
            return unit, None, str(exc)
        finally:
            close_db()
    with ThreadPoolExecutor(SEARCH_WORKERS) as pool:
        futures = [pool.submit(search, u) for u in units]
        for n, future in enumerate(as_completed(futures), 1):
            unit, hits, error = future.result()
            if error is None:
                pools[unit] = hits
            elif unit[1] == 1:
                errors[unit[0]] = error
            progress(f'Searching footage {n}/{len(units)}')
    searched = time.monotonic()
    chosen = pick(project, project['scenes'], units, pools, progress) if pools else {}
    chosen = retry(project, project['scenes'], units, pools, chosen, landscape, progress) if chosen else chosen
    picked = time.monotonic()

    selected, used, assigned = [], set(), []
    for i, scene in sorted(by_index.items()):
        if i in errors:
            selected.append({'scene_id': scene['id'], 'error': errors[i]})
            continue
        shots = sorted(k for (s, k) in units if s == i)
        judged = any((i, k) in chosen for k in shots)

        def options(k):  # Gemini's picks, or the ranking when Gemini could not judge this shot
            return chosen[(i, k)] if (i, k) in chosen else [(h, None) for h in pools.get((i, k), [])]
        clips = []  # (hit, score, shot number) in spoken order
        if len(shots) > 1:
            # One clip per planned shot, each the best one not used elsewhere; a shot without one is left out.
            for k in shots:
                taken = used | {key(c[0]) for c in clips}
                hit = next(((h, s) for h, s in options(k) if key(h) not in taken), None)
                if hit:
                    clips.append((*hit, k))
        else:
            fresh = [(h, s) for h, s in options(1) if key(h) not in used]
            clips = [(h, s, 1) for h, s in fresh[:shots_needed(scene, shot_seconds)]]
        if not clips:
            selected.append({'scene_id': scene['id'], 'error': NO_MATCH if judged else
                             'No suitable stock result. Upload footage or choose a graphic.'})
            continue
        used.update(key(h) for h, _, _ in clips)
        assigned.append((i, scene, clips))
        selected.append(None)  # filled in after the downloads, keeping scene order

    def fetch(hit):
        try:
            return providers.download_stock(hit['id'], hit.get('provider', 'pixabay'), landscape=landscape), None
        except ValueError as exc:
            return None, str(exc)
        finally:
            close_db()
    wanted = {key(h): h for _, _, clips in assigned for h, _, _ in clips}
    files = {}
    with ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
        futures = {pool.submit(fetch, hit): k for k, hit in wanted.items()}
        for n, future in enumerate(as_completed(futures), 1):
            files[futures[future]] = future.result()
            progress(f'Downloading footage {n}/{len(wanted)} clips')
    slots = iter(k for k, s in enumerate(selected) if s is None)
    for i, scene, clips in assigned:
        got = [(files[key(h)][0], score, k) for h, score, k in clips if files[key(h)][0]]
        failed = [files[key(h)][1] for h, _, _ in clips]
        slot = next(slots)
        if not got:
            selected[slot] = {'scene_id': scene['id'], 'error': next((e for e in failed if e), 'Stock download failed')}
            continue
        (asset, score, first), extras = got[0], got[1:]
        unit = units.get((i, first), {})
        # Each clip's share of the line: the length of its words (equal shares without a split).
        shares = [len(units.get((i, k), {}).get('words') or '') for _, _, k in got]
        planned_split = len({k for _, _, k in got}) == len(got) > 1 and all(shares)
        selection = {'scene_id': scene['id'], 'asset': asset, 'extra_assets': [a for a, _, _ in extras],
                     'match': (f'AI-checked match ({score:g}/10)' if score is not None else 'Best keyword match')
                              + (f' + {len(extras)} more shot(s)' if extras else '')
                              + (' after a second search' if unit.get('retried') else '')
                              + (f': {unit["shot"]}' if unit.get('shot') else '')}
        if planned_split:
            selection['shot_weights'] = [round(s / sum(shares), 4) for s in shares]
        selected[slot] = selection
    done = time.monotonic()
    log.info('footage for %d scenes: plan %.1fs, search %.1fs, AI check and retry %.1fs, download %.1fs (%d clips)', len(by_index),
             planned - started, searched - planned, picked - searched, done - picked, len(wanted))
    return selected
