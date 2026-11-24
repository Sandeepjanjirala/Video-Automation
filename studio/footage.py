"""Footage matching for "Find footage for all scenes" and the one-click draft.

The scenes are matched in a plan -> search -> check -> retry loop (the shape LangGraph agents use,
written directly so it needs no extra framework and works with the backup Gemini models):

1. Plan: Gemini reads the whole script and plans, for each scene, one shot per part of its line in
   spoken order (a long line gets several), each with search phrases from specific to broad.
2. Search wide: every phrase is searched; candidates are ranked by tag match, real film over
   animation, resolution, a shape that suits the video format, popularity and length.
3. Check: Gemini looks at preview frames of the best candidates for every shot and scores the clips
   that show it; in a vertical reel it also locates the subject, and the clip is framed on it.
4. Retry: shots with no clip, or only a weak one, are planned again from what was rejected (a
   different, findable view of the same idea), searched (on Pexels too when both sources are set up)
   and checked again; the better clip wins. Clips that already scored well download meanwhile.

Each scene's clips play in the order of its words, each for the share of the line it covers. Without
Gemini the scene keywords and the ranking alone are used, and the result says so. Works for vertical
reels and landscape YouTube videos (studio/formats.py).
"""
import json
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from . import formats, providers
from .framing import SHOT_SECONDS
from .stock import best_file, safe_preview_url, second_source

log = logging.getLogger('studio.footage')
MIN_SCORE = 5      # Gemini's 0-10 fit score below which a shot gets no clip
GOOD_SCORE = 7     # a shot whose best clip scores lower is planned and searched again once
MAX_SHOTS = 4      # clips per scene: its own and up to three extra shots
PLAN_BATCH = 40    # scenes planned per Gemini request (the whole script is always sent as context)
# Work done at the same time: searches (within the provider's rate-limit window, stock.paced), Gemini
# requests for long videos, and clip downloads.
SEARCH_WORKERS, GEMINI_WORKERS, DOWNLOAD_WORKERS = 8, 2, 6
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
while its own words are said (one shot: the whole line). A scene's shots cover only its own line: never
continue into the next scene's line (that scene has its own shots); a short line gets fewer shots.
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
(an empty list when none fits). The same clip may be listed for several shots.{position}
Return JSON {{"scenes": [{{"index": 0, "shot": 1, "picks": [{{"candidate": 2, "score": 8}}, {{"candidate": 5, "score": 6}}]}}]}}
with index = the scene number written in the shot's label (8 for "Scene 8", even when it is the only scene
shown; never its place in this list) and shot = the shot number shown (1 when the scene has one shot).
Answer every shot shown, with an empty picks list when no clip fits.
Script for context: {script}
'''

# Vertical reels show only a narrow upright slice of a landscape clip, so Gemini also says where the subject is.
PICK_POSITION = '''
The reel is vertical: a landscape clip is cropped to an upright slice about a third of its width. For
every pick of a landscape clip also give "x", the horizontal centre of the shot's subject in the frame
from 0 (left edge) to 1 (right edge), e.g. {"candidate": 2, "score": 8, "x": 0.3}, judged from where
the subject really is (0.5 only when it is in the middle); the slice is placed there. Score lower a clip whose subject cannot fit in such a slice (a wide panorama, two subjects far apart).'''

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


def reason(exc):
    """Why a Gemini step gave no answer, in plain words (a quota or limit message is kept as it is)."""
    return 'its answer could not be read' if isinstance(exc, (KeyError, TypeError, json.JSONDecodeError)) else str(exc)


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


def plan(project, need, progress=lambda text: None, problems=None):
    """{scene index: {'shot', 'queries', 'shots': [{'words', 'shot', 'queries'}, ...]}} from Gemini,
    PLAN_BATCH scenes per request, shots in spoken order; scenes of a batch Gemini could not plan are
    left out (their keywords are searched instead) and the reason is added to `problems`."""
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
            if problems is not None:
                problems.append(reason(exc))
            return group, []
        finally:
            close_db()
    answers = parallel(ask, groups, GEMINI_WORKERS)
    for k, (group, rows) in enumerate(answers, 1):
        if len(groups) > 1:
            progress(f'Reading the script and planning shots ({k}/{len(groups)})')
        found = []  # (scene the answer put it under, shot)
        for row in rows if isinstance(rows, list) else []:
            try:
                i = int(row['index'])
                listed = row.get('shots') if isinstance(row.get('shots'), list) else [row]
                found += [(i, {'words': str(s.get('words') or '').strip()[:400],
                               'shot': providers.clean_caption(str(s.get('shot') or ''))[:300],
                               'queries': clean_queries(s.get('queries'))}) for s in listed if isinstance(s, dict)]
            except (KeyError, TypeError, ValueError):
                continue
        for i, shots in placed([(i, s) for i, s in found if s['queries']], group, project['scenes']).items():
            plans[i] = {'shot': shots[0]['shot'], 'queries': shots[0]['queries'], 'shots': shots}
    return plans


def tokens(text):
    return re.findall(r'[a-z0-9]+', (text or '').lower())


def placed(found, group, scenes):
    """{scene: shots in spoken order, at most MAX_SHOTS} with every planned shot under the scene whose line
    holds its words. Asked for more shots than a short line has parts, Gemini continues into the next
    lines ("Oka kilo thene..." planned as scene 1's third shot), which showed clips under the wrong words;
    such a shot moves to its own scene, and words found in no line of the batch are dropped."""
    lines = {i: tokens(line(scenes[i])) for i in group}
    out = {}
    for n, (i, shot) in enumerate(found):
        said = tokens(shot['words'])
        if said and any(lines.values()):
            share = {j: sum(w in lines[j] for w in said) / len(said) for j in group}
            best = max(group, key=lambda j: (share[j], j == i))
            if share[best] < 0.6:
                continue
            i = best
        if i not in lines:
            continue
        # Order by where the words start in the line (Telugu-only lines keep the answer's order).
        start = next((p for p, w in enumerate(lines[i]) if said and w == said[0]), len(lines[i]))
        if all(shot['words'] != other['words'] for _, _, other in out.get(i, [])):
            out.setdefault(i, []).append((start, n, shot))
    return {i: [shot for _, _, shot in sorted(rows, key=lambda r: r[:2])][:MAX_SHOTS] for i, rows in out.items()}


def weights(queries):
    """Each word weighs as many phrases as use it, so the subject ("octopus" in "octopus tentacles"
    and "octopus eye") outweighs a detail ("eye")."""
    out = {}
    for query in queries:
        for word in words(query):
            out[word] = out.get(word, 0) + 1
    return out


def rank(hit, weight, position, landscape=False, seconds=SHOT_SECONDS):
    """Higher is better: the provider's own relevance order (it also finds clips tagged
    "cephalopod" for "octopus"), tag match, real film, sharpness, a shape that fills the frame
    without heavy cropping, popularity, and a clip long enough for a shot of `seconds` (a shorter
    one visibly loops)."""
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
    score -= 0.5 if (hit.get('duration') or 10) < seconds else 0
    return score


def candidates(scene, queries, landscape=False, wide=False, seconds=SHOT_SECONDS):
    """Search every phrase and return candidates, best first, taking turns between the phrases
    so every idea of the plan is among the clips Gemini sees. `wide` (the second search) also asks
    Pexels for the most specific phrase when both sources are set up."""
    lists, error, weight = [], None, weights(queries)
    # The subject on its own ("octopus") gets the cleanest results; providers match longer phrases loosely.
    subject = max(weight, key=lambda word: (weight[word], -len(word)), default='')
    if weight.get(subject, 0) > 1 and subject not in queries:
        queries = [*queries, next((w for w in re.findall(r'[a-z]+', ' '.join(queries).lower()) if words(w) == {subject}), subject)]
    source = scene.get('stock_provider', 'auto')
    searches = [(query, source) for query in queries]
    if wide and queries and (other := second_source(source)):
        searches.insert(1, (queries[0], other))
    for query, provider in searches:
        try:
            hits = providers.stock_search(query, provider=provider)
        except ValueError as exc:
            error = exc
            continue
        lists.append([hit for _, _, hit in sorted(((rank(hit, weight, p, landscape, seconds), p, hit) for p, hit in enumerate(hits)),
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


_previews = None  # one connection pool for every preview frame (a reel's check fetches about a hundred)
_previews_lock = threading.Lock()


def preview_client():
    global _previews
    with _previews_lock:
        if _previews is None:
            _previews = httpx.Client(timeout=20, follow_redirects=False, limits=httpx.Limits(max_connections=16))
        return _previews


def preview(hit):
    """(mime type, bytes) of the clip's preview frame, or None."""
    url, provider = hit.get('preview') or '', hit.get('provider', 'pixabay')
    if not safe_preview_url(url, provider):
        return None
    try:
        response = preview_client().get(url)
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


def sharpness(hit, landscape):
    """Picture rows of the file the studio would download for this format, up to what the frame can show
    (1920 for a reel's upright crop, 1080 for a YouTube frame)."""
    files = hit.get('files') or [hit.get('video') or {}]
    chosen = best_file(files, landscape) if files else {}
    return min(chosen.get('width', 0), chosen.get('height', 0), 1080 if landscape else 1920)


def pick(project, scenes, units, pools, progress=lambda text: None, message='Checking candidate clips with AI', problems=None):
    """{(scene, shot): [(hit, score), ...] best first} judged by Gemini from preview frames (an empty
    list when no clip fits); shots Gemini could not judge are left out (the ranking is used) and the
    reason is added to `problems`. `units` maps (scene, shot) to {'words', 'shot', 'count'} (count =
    shots planned for the scene). In a vertical reel each picked hit gains 'subject_x' (crop_for)."""
    kind, landscape = project.get('format') or 'reel', formats.landscape(project)
    shown, shot_seconds = SHOWN.get(kind, 10), formats.of(project)['shot_seconds']
    script = json.dumps([line(s) for s in project['scenes']], ensure_ascii=False)
    prompt = PICK_PROMPT.format(orientation=formats.of(project)['orientation'], shown=shown, minimum=MIN_SCORE, script=script,
                                shape='; a landscape clip that fills a 16:9 frame' if formats.landscape(project) else '',
                                position='' if formats.landscape(project) else PICK_POSITION)
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
            return found, []
        try:
            rows = parse(providers.gemini(prompt, images=parts, fast=True)[0])  # thinking off: about twice as fast
        except (KeyError, TypeError, ValueError) as exc:
            log.warning('footage pick unavailable: %s', exc)
            if problems is not None:
                problems.append(reason(exc))
            return found, []
        answers = []
        for row in rows if isinstance(rows, list) else []:
            try:
                unit = (int(row['index']), int(row.get('shot') or 1))
                picks = [(int(p['candidate']), float(p['score']), p.get('x')) for p in row.get('picks') or []]
            except (KeyError, TypeError, ValueError):
                continue
            answers.append((unit, picks))
        # Gemini sometimes numbers the shots by their place in this request ("index": 0 for Scene 8): when
        # every answer it numbered so is in the order of the shots it left unanswered, it belongs to them.
        stray = [a for a in answers if a[0] not in offered]
        unanswered = [u for u in offered if u not in {unit for unit, _ in answers}]
        if stray and len(stray) == len(unanswered):
            moved = dict(zip(map(id, stray), unanswered))
            answers = [(moved.get(id(a), a[0]), a[1]) for a in answers]
        for unit, picks in answers:
            if unit in offered:
                found[unit] = [(offered[unit][n], score) for n, score, _ in picks if n in offered[unit] and score >= MIN_SCORE]
                # Equal scores are common (several clips at 8): the sharper download goes first, in Gemini's order otherwise.
                found[unit].sort(key=lambda pair: (-pair[1], -sharpness(pair[0], landscape)))
                for n, _, x in picks:
                    if n in offered[unit] and isinstance(x, (int, float)) and 0 <= x <= 1:
                        offered[unit][n]['subject_x'] = float(x)
        return found, [u for u in offered if u not in found]
    skipped = []
    for found, missing in parallel(judge, groups, GEMINI_WORKERS):
        chosen.update(found)
        skipped += missing
    if skipped:  # shots the answer left out get a second look rather than the ranking alone
        for found, _ in parallel(judge, batches(skipped, PICK_BATCH.get(kind, 12)), GEMINI_WORKERS):
            chosen.update(found)
    return chosen


def retry(project, scenes, units, pools, chosen, landscape, progress, problems=None):
    """Plan, search and check again the shots whose best clip is missing or weak (with Pexels too when
    both sources are set up); keep the better clips. Returns the updated `chosen` (pools of retried
    shots gain the new candidates)."""
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

    seconds = formats.of(project)['shot_seconds']

    def search(unit):
        try:
            seen = {key(h) for h in pools.get(unit, [])}
            return unit, [h for h in candidates(scenes[unit[0]], again[unit]['queries'], landscape, wide=True, seconds=seconds)
                          if key(h) not in seen]
        except ValueError:
            return unit, []
        finally:
            close_db()
    fresh = {u: hits for u, hits in parallel(search, list(again), SEARCH_WORKERS) if hits}
    if not fresh:
        return chosen
    second = pick(project, scenes, {**units, **again}, fresh, progress, 'Checking the new candidates with AI', problems)
    for unit, picks in second.items():
        pools[unit] = pools.get(unit, []) + fresh[unit]
        before = chosen.get(unit, [])
        chosen[unit] = sorted(before + picks, key=lambda pair: -pair[1])
        if picks and (not before or picks[0][1] > before[0][1]):  # the new plan found the better clip
            units[unit] = {**again[unit], 'retried': True}
    return chosen


def crop_for(hit, project):
    """The horizontal framing (a scene's crop_x) that keeps the subject Gemini located in a landscape clip
    (pick's 'subject_x') inside a vertical reel's frame; 0.5, centred, otherwise."""
    x, video = hit.get('subject_x'), hit.get('video') or {}
    width, height = video.get('width') or 0, video.get('height') or 0
    frame_w, frame_h = formats.size(project)
    if x is None or not height or width * frame_h <= height * frame_w:
        return 0.5
    ratio = (width / height) / (frame_w / frame_h)  # the clip, scaled to the frame's height, is this many frames wide
    position = min(1.0, max(0.0, (x * ratio - 0.5) / (ratio - 1)))
    return 0.5 if abs(position - 0.5) < 0.05 else round(position, 3)


def find(project, scenes, progress=lambda text: None, notes=None):
    """Download the best clips for each scene. Returns stock_all selections: asset (plus extra shots in
    spoken order, their share of the line and, in a reel, where each is framed) or error per scene.
    `notes` (a list) receives plain-language notes for the result, e.g. that Gemini was unavailable."""
    index = {s['id']: i for i, s in enumerate(project['scenes'])}
    by_index = {index[s['id']]: s for s in scenes}
    landscape, shot_seconds = formats.landscape(project), formats.of(project)['shot_seconds']
    started, problems = time.monotonic(), []
    progress('Reading the script and planning shots')
    plans = plan(project, sorted(by_index), progress, problems) if by_index else {}
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
            return unit, candidates(by_index[unit[0]], units[unit]['queries'], landscape, seconds=shot_seconds), None
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

    def fetch(hit):
        try:
            return providers.download_stock(hit['id'], hit.get('provider', 'pixabay'), landscape=landscape), None
        except ValueError as exc:
            return None, str(exc)
        finally:
            close_db()
    # Clips Gemini scored well are not searched again, so they download while the weak shots are searched
    # and checked again; the rest download once every scene has its clips.
    downloads, fetching = ThreadPoolExecutor(DOWNLOAD_WORKERS), {}

    def start(hit):
        if key(hit) not in fetching:
            fetching[key(hit)] = downloads.submit(fetch, hit)
    try:
        chosen = pick(project, project['scenes'], units, pools, progress, problems=problems) if pools else {}
        claimed = set()  # in scene order, as the clips are given out below
        for (i, k), picks in sorted(chosen.items()):
            if picks and picks[0][1] >= GOOD_SCORE:
                count = shots_needed(by_index[i], shot_seconds) if units[(i, k)]['count'] == 1 else 1
                for hit, _ in [p for p in picks if key(p[0]) not in claimed][:count]:
                    claimed.add(key(hit))
                    start(hit)
        chosen = retry(project, project['scenes'], units, pools, chosen, landscape, progress, problems) if chosen else chosen
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

        wanted = {key(h): h for _, _, clips in assigned for h, _, _ in clips}
        for hit in wanted.values():
            start(hit)
        files, waiting = {}, {fetching[k]: k for k in wanted}
        for n, future in enumerate(as_completed(waiting), 1):
            files[waiting[future]] = future.result()
            progress(f'Downloading footage {n}/{len(wanted)} clips')
    finally:
        downloads.shutdown(wait=True, cancel_futures=True)  # a cancelled job starts no queued download
    slots = iter(k for k, s in enumerate(selected) if s is None)
    unchecked = 0
    for i, scene, clips in assigned:
        got = [(files[key(h)][0], score, k, h) for h, score, k in clips if files[key(h)][0]]
        failed = [files[key(h)][1] for h, _, _ in clips]
        slot = next(slots)
        if not got:
            selected[slot] = {'scene_id': scene['id'], 'error': next((e for e in failed if e), 'Stock download failed')}
            continue
        (asset, score, first, _), extras = got[0], got[1:]
        unit = units.get((i, first), {})
        unchecked += score is None
        # Each clip's share of the line: the length of its words (equal shares without a split).
        shares = [len(units.get((i, k), {}).get('words') or '') for _, _, k, _ in got]
        planned_split = len({k for _, _, k, _ in got}) == len(got) > 1 and all(shares)
        selection = {'scene_id': scene['id'], 'asset': asset, 'extra_assets': [a for a, _, _, _ in extras],
                     'match': (f'AI-checked match ({score:g}/10)' if score is not None else 'Best keyword match')
                              + (f' + {len(extras)} more shot(s)' if extras else '')
                              + (' after a second search' if unit.get('retried') else '')
                              + (f': {unit["shot"]}' if unit.get('shot') else '')}
        if planned_split:
            selection['shot_weights'] = [round(s / sum(shares), 4) for s in shares]
        # In a reel each landscape clip is framed on its subject rather than on its centre.
        crops = [crop_for(h, project) for _, _, _, h in got]
        if crops[0] != 0.5:
            selection['crop_x'] = crops[0]
        if any(c != 0.5 for c in crops[1:]):
            selection['shot_crop_x'] = crops[1:]
        selected[slot] = selection
    if problems and notes is not None:
        notes.append(f'Gemini was unavailable for part of the footage search ({problems[0].rstrip(".")})'
                     + (f'; {unchecked} scene(s) were matched by search words only' if unchecked else '')
                     + '. Review them, or run Find footage for all scenes again later.')
    done = time.monotonic()
    log.info('footage for %d scenes: plan %.1fs, search %.1fs, AI check and retry %.1fs, download %.1fs (%d clips)', len(by_index),
             planned - started, searched - planned, picked - searched, done - picked, len(wanted))
    return selected
