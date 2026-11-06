"""Authoritative Timeline and Roman Telugu Caption Alignment Engine (Phase 5).

Provides a single timing representation for rendering, export, and preview.
Enforces 30 fps frame quantization, playback speed adjustments, cue validation,
and provenance-tracked Roman Telugu phrase alignment.
"""
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple


@dataclass
class SceneTimeline:
    scene_id: str
    raw_voice_duration: float
    effective_voice_duration: float
    hold_seconds: float
    scene_duration: float
    start_offset: float
    end_offset: float
    cues: List[Dict[str, Any]]
    alignment_provenance: str
    needs_review: bool


@dataclass
class ProjectTimeline:
    project_id: str
    duration_mode: str
    target_seconds: int
    speed: float
    total_duration: float
    scenes: List[SceneTimeline]
    all_cues: List[Dict[str, Any]]

    def to_manifest(self) -> Dict[str, Any]:
        return {
            'project_id': self.project_id,
            'duration_mode': self.duration_mode,
            'target_seconds': self.target_seconds,
            'speed': round(self.speed, 6),
            'total_duration': round(self.total_duration, 4),
            'frame_rate': 30,
            'scenes': [
                {
                    'scene_id': s.scene_id,
                    'raw_voice_duration': round(s.raw_voice_duration, 4),
                    'effective_voice_duration': round(s.effective_voice_duration, 4),
                    'hold_seconds': round(s.hold_seconds, 2),
                    'scene_duration': round(s.scene_duration, 4),
                    'start_offset': round(s.start_offset, 4),
                    'end_offset': round(s.end_offset, 4),
                    'cues': s.cues,
                    'alignment_provenance': s.alignment_provenance,
                    'needs_review': s.needs_review
                }
                for s in self.scenes
            ],
            'cue_count': len(self.all_cues)
        }


def stamp(seconds: float, ass: bool = False) -> str:
    """Format seconds into ASS timestamp (H:MM:SS.cs) or SRT timestamp (HH:MM:SS,mmm)."""
    units = round(max(0.0, seconds) * (100 if ass else 1000))
    base = 100 if ass else 1000
    hours, rem = divmod(units, 3600 * base)
    mins, rem = divmod(rem, 60 * base)
    secs, fraction = divmod(rem, base)
    if ass:
        return f"{hours}:{mins:02}:{secs:02}.{fraction:02}"
    return f"{hours:02}:{mins:02}:{secs:02},{fraction:03}"


def to_srt(cues: List[Dict[str, Any]]) -> str:
    """Format global cues into a standard SRT subtitle string."""
    blocks = []
    for i, cue in enumerate(cues, 1):
        start_str = stamp(cue['start'])
        end_str = stamp(cue['end'])
        text = cue.get('text', '').strip()
        blocks.append(f"{i}\n{start_str} --> {end_str}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def detect_text_mismatches(narration_text: str, roman_text: str) -> List[str]:
    """Detect discrepancies such as digits, abbreviations, or token count mismatches."""
    notices = []
    num_digits = re.findall(r'\b\d+\b', roman_text)
    if num_digits:
        notices.append(f"Numbers detected in Roman text ({', '.join(num_digits)}). Telugu speech spells numbers as words; phrase alignment used.")

    abbrs = re.findall(r'\b[A-Z]{2,6}\b', roman_text)
    if abbrs:
        notices.append(f"Abbreviations detected ({', '.join(abbrs)}). Ensure transliteration matches spoken syllables.")

    roman_tokens = roman_text.split()
    narration_tokens = narration_text.split()
    if roman_tokens and narration_tokens:
        ratio = len(roman_tokens) / max(len(narration_tokens), 1)
        if ratio < 0.6 or ratio > 1.8:
            notices.append(f"Token count differs significantly (Roman: {len(roman_tokens)} words vs Telugu: {len(narration_tokens)} words). Phrase-level timing recommended.")

    return notices


def segment_into_phrases(roman_text: str, max_words: int = 4, max_chars: int = 34) -> List[str]:
    """Segment Roman Telugu text into readable subtitle phrases respecting clause boundaries."""
    text = roman_text.strip()
    if not text:
        return []

    # First split on explicit punctuation pauses if appropriate
    raw_clauses = re.split(r'([.,!?;:\n]+)', text)
    clauses = []
    current_clause = ""
    for piece in raw_clauses:
        if re.match(r'^[.,!?;:\n]+$', piece):
            current_clause += piece
            clauses.append(current_clause.strip())
            current_clause = ""
        else:
            if current_clause:
                clauses.append(current_clause.strip())
            current_clause = piece
    if current_clause.strip():
        clauses.append(current_clause.strip())

    phrases = []
    for clause in clauses:
        words = clause.split()
        if not words:
            continue
        accum = []
        for word in words:
            accum.append(word)
            accum_text = " ".join(accum)
            if len(accum) >= max_words or len(accum_text) >= max_chars:
                phrases.append(accum_text)
                accum = []
        if accum:
            phrases.append(" ".join(accum))

    return [p for p in phrases if p.strip()]


def estimate_phrase_weights(phrases: List[str]) -> List[float]:
    """Approximate Telugu syllable/speech duration weights for Roman phrases."""
    weights = []
    for p in phrases:
        vowels = len(re.findall(r'[aeiouAEIOU]', p))
        words = len(p.split())
        chars = len(p)
        # Telugu phonetic weight: vowel centers count most, with character length baseline
        weight = max(1.0, (vowels * 1.5) + (chars * 0.4) + (words * 0.5))
        weights.append(weight)
    return weights


def validate_cues(cues: List[Dict[str, Any]], audio_duration: float) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate cue bounds, ordering, non-overlap, and display duration."""
    errors = []
    sanitized = []
    last_end = 0.0

    for i, c in enumerate(cues):
        start = float(c.get('start', 0.0))
        end = float(c.get('end', 0.0))
        text = str(c.get('text', '')).strip()

        if re.search(r'[\u0c00-\u0c7f]', text):
            errors.append(f"Cue {i + 1} contains Telugu script. Captions must use Roman letters.")
        if end <= start:
            errors.append(f"Cue {i + 1} end time ({end:.2f}s) must follow start time ({start:.2f}s).")
        if start < last_end - 0.05:
            errors.append(f"Cue {i + 1} start ({start:.2f}s) overlaps previous cue end ({last_end:.2f}s).")
        if end > audio_duration + 0.35:
            errors.append(f"Cue {i + 1} end ({end:.2f}s) extends beyond narration audio ({audio_duration:.2f}s).")

        display_duration = end - start
        if display_duration < 0.2:
            # Enforce minimum readable display duration
            end = max(end, start + 0.25)

        sanitized.append({
            'start': max(0.0, round(start, 3)),
            'end': min(audio_duration, round(end, 3)),
            'text': text,
            'provenance': c.get('provenance', 'manual'),
            'needs_review': bool(c.get('needs_review', False))
        })
        last_end = end

    return sanitized, errors


def align_captions(
    scene: Dict[str, Any],
    audio_duration: float,
    boundaries: Optional[List[Dict[str, Any]]] = None,
    narration_modified: bool = False
) -> Tuple[List[Dict[str, Any]], str]:
    """Align captions following strict Phase 5 priority:
    A. Valid user-edited manual cues.
    B. Existing provider timing metadata mapped to Roman phrases.
    C. Installed Telugu aligner (deferred if none installed).
    D. Clearly labeled estimated phrase timings.
    """
    existing_cues = scene.get('cues', [])
    if existing_cues:
        sanitized, errors = validate_cues(existing_cues, audio_duration)
        if not errors:
            provenance = 'manual'
            if narration_modified or scene.get('cues_needs_review'):
                provenance = 'manual (needs review)'
                for c in sanitized:
                    c['needs_review'] = True
                    c['provenance'] = 'manual'
            else:
                for c in sanitized:
                    c['provenance'] = 'manual'
            return sanitized, provenance

    roman_text = scene.get('roman', '').strip()
    if not roman_text:
        return [], 'empty'

    phrases = segment_into_phrases(roman_text)
    if not phrases:
        return [], 'empty'

    # Priority B: Provider timing metadata (e.g., Edge TTS WordBoundary)
    valid_boundaries = [b for b in (boundaries or []) if 0 <= b.get('start', 0) < b.get('end', 0) <= audio_duration + 0.5]
    if valid_boundaries and len(valid_boundaries) >= len(phrases):
        total_bounds = len(valid_boundaries)
        phrase_count = len(phrases)
        mapped_cues = []
        bounds_per_phrase = total_bounds / phrase_count

        for i, phrase in enumerate(phrases):
            start_idx = int(math.floor(i * bounds_per_phrase))
            end_idx = min(total_bounds - 1, int(math.floor((i + 1) * bounds_per_phrase) - 1))
            end_idx = max(start_idx, end_idx)

            b_start = valid_boundaries[start_idx]['start']
            b_end = valid_boundaries[end_idx]['end']

            # Ensure bounds fit audio duration
            cue_start = max(0.0, round(b_start, 3))
            cue_end = min(audio_duration, round(b_end, 3))
            if cue_end <= cue_start:
                cue_end = min(audio_duration, cue_start + 0.3)

            mapped_cues.append({
                'start': cue_start,
                'end': cue_end,
                'text': phrase,
                'provenance': 'provider-mapped',
                'needs_review': True  # Provider mapping is approximate across transliterated phrases
            })

        # Ensure monotonic ordering
        for k in range(1, len(mapped_cues)):
            if mapped_cues[k]['start'] < mapped_cues[k - 1]['end']:
                mapped_cues[k]['start'] = mapped_cues[k - 1]['end']
            if mapped_cues[k]['end'] <= mapped_cues[k]['start']:
                mapped_cues[k]['end'] = min(audio_duration, mapped_cues[k]['start'] + 0.25)

        return mapped_cues, 'provider-mapped (review in preview)'

    # Priority D: Clearly labeled estimated phrase timings
    weights = estimate_phrase_weights(phrases)
    total_weight = sum(weights) or 1.0
    cues = []
    current_time = 0.0

    for i, (phrase, w) in enumerate(zip(phrases, weights)):
        span = audio_duration * (w / total_weight)
        cue_start = current_time
        cue_end = min(audio_duration, current_time + span)
        if i == len(phrases) - 1:
            cue_end = audio_duration

        cues.append({
            'start': round(cue_start, 3),
            'end': round(max(cue_start + 0.25, cue_end), 3),
            'text': phrase,
            'provenance': 'estimated',
            'needs_review': True
        })
        current_time = cue_end

    return cues, 'estimated phrase timing (review in preview)'


def split_cue(cues: List[Dict[str, Any]], cue_index: int, split_ratio: float = 0.5) -> List[Dict[str, Any]]:
    """Split a caption cue into two sub-phrases at proportional time and word boundary."""
    if not (0 <= cue_index < len(cues)):
        raise ValueError(f"Invalid cue index {cue_index}")

    target = cues[cue_index]
    words = target['text'].split()
    if len(words) < 2:
        raise ValueError("Cannot split a cue with fewer than two words")

    mid_word_idx = max(1, len(words) // 2)
    text1 = " ".join(words[:mid_word_idx])
    text2 = " ".join(words[mid_word_idx:])

    dur = target['end'] - target['start']
    split_t = target['start'] + (dur * split_ratio)

    cue1 = {
        'start': target['start'],
        'end': round(split_t, 3),
        'text': text1,
        'provenance': 'manual',
        'needs_review': False
    }
    cue2 = {
        'start': round(split_t, 3),
        'end': target['end'],
        'text': text2,
        'provenance': 'manual',
        'needs_review': False
    }

    new_cues = list(cues)
    new_cues[cue_index:cue_index + 1] = [cue1, cue2]
    return new_cues


def merge_cues(cues: List[Dict[str, Any]], cue_index: int) -> List[Dict[str, Any]]:
    """Merge cue at cue_index with the subsequent cue."""
    if not (0 <= cue_index < len(cues) - 1):
        raise ValueError(f"Cannot merge cue {cue_index}: next cue does not exist")

    c1 = cues[cue_index]
    c2 = cues[cue_index + 1]

    merged = {
        'start': c1['start'],
        'end': c2['end'],
        'text': f"{c1['text']} {c2['text']}".strip(),
        'provenance': 'manual',
        'needs_review': False
    }

    new_cues = list(cues)
    new_cues[cue_index:cue_index + 2] = [merged]
    return new_cues


def reset_scene_cues(scene: Dict[str, Any], audio_duration: float, boundaries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Reset automatic phrase timings for a scene, discarding manual overrides."""
    temp_scene = dict(scene)
    temp_scene['cues'] = []
    cues, _ = align_captions(temp_scene, audio_duration, boundaries=boundaries)
    return cues


def compute_timeline(project: Dict[str, Any], prepared_voices: List[Dict[str, Any]]) -> ProjectTimeline:
    """Calculate the single authoritative timeline representation for a project."""
    scenes = project.get('scenes', [])
    if len(scenes) != len(prepared_voices):
        raise ValueError(f"Mismatch between project scenes ({len(scenes)}) and prepared voices ({len(prepared_voices)})")

    voice_total = sum(v['duration'] for v in prepared_voices)
    holds = sum(float(s.get('hold_seconds', 0.0)) for s in scenes)
    duration_mode = project.get('duration_mode', 'natural')
    target_seconds = int(project.get('target_seconds', 60))

    speed = 1.0
    if duration_mode == 'target':
        if target_seconds <= holds:
            raise ValueError("Target duration is shorter than or equal to scene holds")
        target_voice = target_seconds - holds
        speed = voice_total / target_voice
        if not (0.80 <= speed <= 1.25):
            raise ValueError(
                f"Narration plus holds is {voice_total + holds:.1f}s. Target ({target_seconds}s) requires excessive speed adjustment ({speed:.2f}x). "
                "Adjust script length, target seconds, or use natural duration mode."
            )

    natural_total = (voice_total / speed) + holds
    if natural_total > 180.5:
        raise ValueError("Total project duration exceeds maximum limit of 180 seconds")

    scene_timelines: List[SceneTimeline] = []
    all_global_cues: List[Dict[str, Any]] = []
    cumulative_offset = 0.0

    for scene, voice in zip(scenes, prepared_voices):
        raw_voice_dur = float(voice['duration'])
        effective_voice_dur = raw_voice_dur / speed
        hold_sec = float(scene.get('hold_seconds', 0.0))

        # Authoritative 30 fps frame quantization
        scene_dur = math.ceil((effective_voice_dur + hold_sec) * 30.0) / 30.0
        start_offset = cumulative_offset
        end_offset = cumulative_offset + scene_dur

        # Adjust cues for playback speed
        adjusted_cues = []
        for c in voice.get('cues', []):
            adj_start = round(float(c['start']) / speed, 3)
            adj_end = round(float(c['end']) / speed, 3)
            adjusted_cues.append({
                **c,
                'start': adj_start,
                'end': adj_end
            })

            # Record global timeline cues for whole-reel export
            all_global_cues.append({
                'start': round(start_offset + adj_start, 3),
                'end': round(start_offset + adj_end, 3),
                'text': c['text'],
                'scene_id': scene['id'],
                'provenance': c.get('provenance', 'estimated'),
                'needs_review': c.get('needs_review', False)
            })

        scene_timelines.append(SceneTimeline(
            scene_id=scene['id'],
            raw_voice_duration=raw_voice_dur,
            effective_voice_duration=effective_voice_dur,
            hold_seconds=hold_sec,
            scene_duration=scene_dur,
            start_offset=start_offset,
            end_offset=end_offset,
            cues=adjusted_cues,
            alignment_provenance=voice.get('alignment', 'estimated'),
            needs_review=any(c.get('needs_review') for c in adjusted_cues)
        ))

        cumulative_offset += scene_dur

    return ProjectTimeline(
        project_id=project.get('id', ''),
        duration_mode=duration_mode,
        target_seconds=target_seconds,
        speed=speed,
        total_duration=cumulative_offset,
        scenes=scene_timelines,
        all_cues=all_global_cues
    )
