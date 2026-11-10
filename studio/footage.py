"""Footage matching for "Find footage for all scenes" and the one-click draft.

1. Plan: Gemini reads the whole script and decides what the viewer should see in each scene, with
   search phrases from specific to broad (one request per 40 scenes).
2. Search wide: every phrase is searched; candidates are ranked by tag match, real film over
   animation, resolution, a shape that suits the video format and popularity.
3. Pick: Gemini looks at preview frames of the best candidates for every scene and scores the
   clips that show the intended shot (one request per batch of scenes).

Without Gemini the scene keywords and the ranking alone are used. Works for vertical reels and
landscape YouTube videos (studio/formats.py).
"""
import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import formats, providers
from .framing import SHOT_SECONDS
from .stock import safe_preview_url

log = logging.getLogger('studio.footage')
MIN_SCORE = 5      # Gemini's 0-10 fit score below which a scene gets no clip
MAX_SHOTS = 4      # clips per scene: its own and up to three extra shots
PLAN_BATCH = 40    # scenes planned per Gemini request (the whole script is always sent as context)
# Candidates per scene that Gemini sees (a preview costs ~260 tokens) and scenes per pick request.
# A long YouTube video has hundreds of scenes, so it shows fewer candidates in bigger batches.
SHOWN = {'reel': 10, 'youtube': 6}
PICK_BATCH = {'reel': 12, 'youtube': 16}
STOP = set('a an and the of in on at to for with from into by is are was be this that it its his her their '
           'video footage clip stock shot scene view close closeup up'.split())
# Editing material rather than footage: green screens and subscribe or logo overlays.
UNUSABLE = re.compile(r'green ?screen|chroma|subscribe|lower third|logo reveal|\bintro\b', re.I)

PLAN_PROMPT = '''You are the editor of a professional {orientation} with Telugu narration.
Read the whole script first and work out its topic, the story it tells and its mood. Then, for each
scene index listed in NEED, decide what the viewer should SEE while hearing that line, as stock footage.
Rules:
- Show the specific thing the line talks about, literally: "Octopus ki moodu hearts untayi" (an octopus
  has three hearts) -> an octopus, not a heart or a heartbeat line.
- For lines without a filmable subject (the title, hooks, "did you know", questions, "comment below"),
  show the video's main subject or a fitting human reaction, e.g. "amazed young woman looking at phone".
  Never use abstract words such as facts, information, shocking, concept, question or engagement.
- Real camera footage, sharp and well lit, like a documentary or a premium brand film. No text, titles,
  logos, charts, infographics, cartoons or slideshows, unless the subject can only be shown that way
  (planets, blood cells, atoms: realistic 3D animation is fine).
- Plan shots that stock libraries really have. When a detail cannot be filmed (inside a body, a
  number, a duration, a slow process), show the subject itself; a line that continues the previous
  one shows the same subject in a different shot (another angle, a close-up, a wider view).
- Keep a consistent look across the video and do not plan the same shot twice.
For each NEED scene return: index; shot (one sentence describing the ideal frame); queries (3 English
search phrases for a stock library such as Pixabay, 1 to 3 words each, using words such clips are
tagged with, most specific first, the last broad enough to always return results, e.g. "octopus
tentacles", "octopus", "ocean underwater"; no adjectives such as cinematic, beautiful or 4k; no
numbers; no people's names).
Return JSON {{"scenes": [{{"index": 0, "shot": "...", "queries": ["...", "...", "..."]}}]}}.
'''

PICK_PROMPT = '''You are the editor of a professional {orientation} with Telugu narration.
For each scene below you see up to {shown} candidate stock clips, one preview frame each. Judge
each scene on its own: which clips show the scene's intended shot, as a viewer would expect while
hearing the line? Judge what is actually visible in the frame, not the clip name. Prefer real, sharp,
well-lit, professional footage{shape}. Reject clips that are off-topic, low quality, watermarked, green
screen, or covered in text, buttons or logos (such as "subscribe" graphics).
Score each acceptable clip from 0 to 10, honestly: 8 or more when the frame clearly shows the subject
of the intended shot; 6 or 7 for the right subject in a different kind of shot, or, for a line
without a concrete subject (hook, question, "comment below"), a good-looking clip of the planned
kind; 4 or less for a loosely related clip.
A scene that "needs N clips" is long: the video cuts between N different clips during it to stay
engaging, so list at least N acceptable clips when they exist, preferably different angles or
moments of the subject rather than near-identical frames.
For every scene return its best clips, best first, at most 5, leaving out any scoring below {minimum}
(an empty list when none fits). The same clip may be listed for several scenes.
Return JSON {{"scenes": [{{"index": 0, "picks": [{{"candidate": 2, "score": 8}}, {{"candidate": 5, "score": 6}}]}}]}}.
Script for context: {script}
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


def plan(project, need, progress=lambda text: None):
    """{scene index: {'shot', 'queries'}} from Gemini, PLAN_BATCH scenes per request; scenes of a
    batch Gemini could not plan are left out (their keywords are searched instead)."""
    script = [{'index': i, 'line': line(s), 'keywords': s.get('keywords', '')} for i, s in enumerate(project['scenes'])]
    head = (PLAN_PROMPT.format(orientation=formats.of(project)['orientation'])
            + f"Topic: {project.get('topic') or project.get('title') or ''}\n"
            + f"SCRIPT: {json.dumps(script, ensure_ascii=False)}\n")
    plans, groups = {}, batches(need, PLAN_BATCH)
    for k, group in enumerate(groups, 1):
        if len(groups) > 1:
            progress(f'Reading the script and planning shots ({k}/{len(groups)})')
        try:
            rows = parse(providers.gemini(head + f'NEED: {json.dumps(group)}')[0])
        except (KeyError, TypeError, ValueError) as exc:
            log.warning('footage plan unavailable: %s', exc)
            continue
        for row in rows if isinstance(rows, list) else []:
            try:
                i = int(row['index'])
                queries = [providers.clean_caption(str(q))[:100] for q in row.get('queries') or [] if str(q).strip()][:3]
            except (KeyError, TypeError, ValueError):
                continue
            if i in group and queries:
                plans[i] = {'shot': providers.clean_caption(str(row.get('shot') or ''))[:300], 'queries': queries}
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


def pick(project, scenes, plans, pools, progress=lambda text: None):
    """{scene index: [(hit, score), ...] best first} judged by Gemini from preview frames (an empty
    list when no clip fits); scenes Gemini could not judge are left out (the ranking is used)."""
    kind = project.get('format') or 'reel'
    shown, shot_seconds = SHOWN.get(kind, 10), formats.of(project)['shot_seconds']
    script = json.dumps([line(s) for s in project['scenes']], ensure_ascii=False)
    prompt = PICK_PROMPT.format(orientation=formats.of(project)['orientation'], shown=shown, minimum=MIN_SCORE, script=script,
                                shape='; a landscape clip that fills a 16:9 frame' if formats.landscape(project) else '')
    chosen, groups = {}, batches(sorted(i for i in pools if pools[i]), PICK_BATCH.get(kind, 12))
    for k, group in enumerate(groups, 1):
        progress('Checking candidate clips with AI' + (f' ({k}/{len(groups)})' if len(groups) > 1 else ''))
        flat = [(i, n, hit) for i in group for n, hit in enumerate(pools[i][:shown], 1)]
        with ThreadPoolExecutor(8) as pool:
            frames = list(pool.map(lambda item: preview(item[2]), flat))
        parts, offered = [], {}
        for (i, n, hit), frame in zip(flat, frames):
            if frame is None:
                continue
            label = f'Scene {i} candidate {n} (clip {hit.get("provider", "pixabay")}-{hit["id"]}):'
            if i not in offered:  # the first frame of a scene carries its line, intended shot and clip count
                shot = plans.get(i, {}).get('shot') or scenes[i].get('keywords', '')
                need = shots_needed(scenes[i], shot_seconds)
                label = (f'\nScene {i}' + (f' (needs {need} clips)' if need > 1 else '') + ': line '
                         f'{json.dumps(line(scenes[i]), ensure_ascii=False)}; intended shot: {shot}\n' + label)
            offered.setdefault(i, {})[n] = hit
            parts.append((label, *frame))
        if not offered:
            continue
        try:
            rows = parse(providers.gemini(prompt, images=parts)[0])
        except (KeyError, TypeError, ValueError) as exc:
            log.warning('footage pick unavailable: %s', exc)
            continue
        for row in rows if isinstance(rows, list) else []:
            try:
                i, picks = int(row['index']), [(int(p['candidate']), float(p['score'])) for p in row.get('picks') or []]
            except (KeyError, TypeError, ValueError):
                continue
            if i in offered:
                chosen[i] = [(offered[i][n], score) for n, score in picks if n in offered[i] and score >= MIN_SCORE]
    return chosen


def find(project, scenes, progress=lambda text: None):
    """Download the best clip for each scene. Returns stock_all selections: asset or error per scene."""
    index = {s['id']: i for i, s in enumerate(project['scenes'])}
    by_index = {index[s['id']]: s for s in scenes}
    landscape, shot_seconds = formats.landscape(project), formats.of(project)['shot_seconds']
    progress('Reading the script and planning shots')
    plans = plan(project, sorted(by_index), progress) if by_index else {}
    pools, errors = {}, {}
    for n, (i, scene) in enumerate(sorted(by_index.items()), 1):
        progress(f'Searching footage {n}/{len(by_index)}')
        keywords = (scene.get('keywords') or '').strip()
        queries = list(dict.fromkeys(plans.get(i, {}).get('queries', []) + ([keywords] if keywords else [])))
        if not queries:
            errors[i] = 'Add English search keywords'
            continue
        try:
            pools[i] = candidates(scene, queries, landscape)
        except ValueError as exc:
            errors[i] = str(exc)
    chosen = pick(project, project['scenes'], plans, pools, progress) if pools else {}
    selected, used = [], set()
    for i, scene in sorted(by_index.items()):
        if i in errors:
            selected.append({'scene_id': scene['id'], 'error': errors[i]})
            continue
        fresh = [(h, None) for h in pools[i] if (h.get('provider', 'pixabay'), h['id']) not in used]
        if i in chosen:  # Gemini's acceptable clips, best first; a clip is used by one scene only
            fresh = [(h, score) for h, score in chosen[i] if (h.get('provider', 'pixabay'), h['id']) not in used]
            if not fresh:
                selected.append({'scene_id': scene['id'], 'error': 'No stock clip matched this scene well. Upload footage or use a title card.'})
                continue
        if not fresh:
            selected.append({'scene_id': scene['id'], 'error': 'No suitable stock result. Upload footage or choose a graphic.'})
            continue
        # The best clip, then extra shots for a long scene (also from the accepted clips).
        progress(f'Downloading footage {len(selected) + 1}/{len(by_index)}')
        clips = []
        for hit, score in fresh[:shots_needed(scene, shot_seconds)]:
            try:
                clips.append((providers.download_stock(hit['id'], hit.get('provider', 'pixabay'), landscape=landscape), score))
            except ValueError as exc:
                if not clips:
                    error = str(exc)
                continue
            used.add((hit.get('provider', 'pixabay'), hit['id']))
        if not clips:
            selected.append({'scene_id': scene['id'], 'error': error})
            continue
        (asset, score), extras = clips[0], [a for a, _ in clips[1:]]
        shot = plans.get(i, {}).get('shot', '')
        selected.append({'scene_id': scene['id'], 'asset': asset, 'extra_assets': extras,
                         'match': (f'AI-checked match ({score:g}/10)' if score is not None else 'Best keyword match')
                                  + (f' + {len(extras)} more shot(s)' if extras else '') + (f': {shot}' if shot else '')})
    return selected
