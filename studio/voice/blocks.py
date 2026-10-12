"""Narration Blocks and Continuous Timeline Assembly for Telugu Reel Studio (Phase 4).
Synthesizes coherent sentences/paragraphs spanning multiple visual shots to prevent
arbitrary scene audio chopping, clipping syllables, or deleting meaningful pauses.
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class NarrationBlock:
    id: str
    scene_ids: List[str]
    spoken_text: str
    roman_text: str
    take_id: str = ""
    audio_path: str = ""
    duration: float = 0.0
    cues: List[Dict[str, Any]] = field(default_factory=list)
    word_boundaries: List[Dict[str, Any]] = field(default_factory=list)
    scene_durations: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "scene_ids": self.scene_ids,
            "spoken_text": self.spoken_text,
            "roman_text": self.roman_text,
            "take_id": self.take_id,
            "audio_path": self.audio_path,
            "duration": self.duration,
            "cues": self.cues,
            "word_boundaries": self.word_boundaries,
            "scene_durations": self.scene_durations
        }


def build_narration_blocks(scenes: List[Dict[str, Any]]) -> List[NarrationBlock]:
    """Group scenes into coherent narration blocks.
    If scenes have explicit narration_block_id, group by block ID.
    Otherwise, if scenes are short fragments, group sentences together.
    Maintains 1:1 per-scene compatibility when scenes already have distinct complete thoughts.
    """
    if not scenes:
        return []

    # Check for explicit block IDs
    has_explicit = any(s.get("narration_block_id") for s in scenes)
    if has_explicit:
        blocks_map: Dict[str, List[Dict[str, Any]]] = {}
        for s in scenes:
            bid = s.get("narration_block_id") or s["id"]
            blocks_map.setdefault(bid, []).append(s)

        result = []
        for bid, b_scenes in blocks_map.items():
            spoken = " ".join((s.get("spoken_text") or s.get("narration", "")).strip() for s in b_scenes)
            roman = " ".join(s.get("roman", "").strip() for s in b_scenes)
            result.append(NarrationBlock(
                id=bid,
                scene_ids=[s["id"] for s in b_scenes],
                spoken_text=spoken.strip(),
                roman_text=roman.strip()
            ))
        return result

    # Default per-scene compatibility path
    result = []
    for s in scenes:
        spoken = (s.get("spoken_text") or s.get("narration", "")).strip()
        roman = s.get("roman", "").strip()
        result.append(NarrationBlock(
            id=f"block_{s['id']}",
            scene_ids=[s["id"]],
            spoken_text=spoken,
            roman_text=roman
        ))
    return result


def align_visual_cuts_to_speech_pauses(
    block_duration: float,
    word_boundaries: List[Dict[str, Any]],
    scene_proportions: List[float]
) -> List[float]:
    """Align scene visual cuts to natural speech pauses or word gaps rather than
    chopping speech mid-word.
    `scene_proportions`: relative duration weights for each visual scene in the block.
    Returns: list of durations for each scene in the block summing to block_duration.
    """
    num_scenes = len(scene_proportions)
    if num_scenes <= 1:
        return [block_duration]

    total_weight = sum(scene_proportions)
    ideal_split_points = []
    accum = 0.0
    for w in scene_proportions[:-1]:
        accum += (w / total_weight) * block_duration
        ideal_split_points.append(accum)

    actual_splits = []
    for ideal in ideal_split_points:
        best_split = ideal
        min_distance = float("inf")

        # Search for closest word boundary gap
        if word_boundaries:
            for i, b in enumerate(word_boundaries):
                end_time = b.get("end", 0.0)
                dist = abs(end_time - ideal)
                if dist < min_distance and dist < 1.2:  # Allow up to 1.2s shift to hit natural pause
                    # Check gap to next word
                    next_start = word_boundaries[i + 1]["start"] if i + 1 < len(word_boundaries) else block_duration
                    gap_mid = (end_time + next_start) / 2.0
                    min_distance = dist
                    best_split = gap_mid

        actual_splits.append(best_split)

    # Compute individual scene durations from split points
    durations = []
    prev = 0.0
    for s in actual_splits:
        d = max(0.5, s - prev)
        durations.append(round(d, 3))
        prev = s
    durations.append(round(max(0.5, block_duration - prev), 3))

    # Adjust rounding discrepancy on final segment
    diff = block_duration - sum(durations)
    durations[-1] = round(durations[-1] + diff, 3)

    return durations


def calculate_timeline(
    total_voice_duration: float,
    total_holds: float,
    duration_mode: str = "natural",
    target_seconds: int = 60
) -> Tuple[float, float, Optional[str]]:
    """Determine timeline speed and natural duration.
    Defaults to natural duration. Avoids speeding up voice just to hit requested length.
    Returns: (speed_factor, final_duration, warning_or_guidance)
    """
    natural_duration = total_voice_duration + total_holds

    if duration_mode == "natural":
        return 1.0, natural_duration, None

    # Target-fit mode
    if target_seconds <= total_holds:
        raise ValueError("Target duration must be greater than total scene holds.")

    speed = total_voice_duration / (target_seconds - total_holds)

    # Bounded speed check
    if not (0.80 <= speed <= 1.25):
        guidance = (
            f"Narration plus holds is {natural_duration:.1f}s. "
            f"Target {target_seconds}s requires {speed:.2f}x speed change. "
            f"Please edit the script, change the target duration, or switch to natural narration pace."
        )
        raise ValueError(guidance)

    final_dur = (total_voice_duration / speed) + total_holds
    return speed, final_dur, None
