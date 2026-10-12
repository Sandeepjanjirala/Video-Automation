"""Uploaded narration adapter for Telugu Reel Studio (Phase 4).
Supports human vocal recordings uploaded by users, preserving authentic speech.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from ..config import DATA
from ..media import probe


class UploadNarrationAdapter:
    """Adapter for user-uploaded narration audio files."""

    def __init__(self):
        self.provider = "upload"

    def synthesize(
        self,
        text: str,
        voice: str = "uploaded",
        rate: int = 0,
        pitch: int = 0,
        asset_path: Optional[Path] = None,
        target_audio: Optional[Path] = None,
        target_meta: Optional[Path] = None
    ) -> Tuple[Path, List[Dict[str, Any]], Dict[str, Any]]:
        """Validate uploaded audio and return (audio_path, boundaries, metadata)."""
        if not asset_path or not asset_path.exists():
            raise ValueError("Uploaded audio asset not found or not specified.")

        info = probe(asset_path)
        dur = float(info.get("format", {}).get("duration", 0.0))
        if dur <= 0:
            raise ValueError("Uploaded audio file has zero duration.")

        words = text.split()
        total_chars = max(1, sum(len(w) for w in words))
        boundaries = []
        curr = 0.0
        for w in words:
            w_dur = (len(w) / total_chars) * dur
            boundaries.append({
                "start": round(curr, 3),
                "end": round(curr + w_dur, 3),
                "text": w
            })
            curr += w_dur

        metadata = {
            "provider": "upload",
            "voice": "human_uploaded",
            "model": "User-Supplied",
            "duration": dur,
            "rate": 0,
            "pitch": 0,
            "timing_type": "duration_proportional",
            "boundaries_count": len(boundaries)
        }

        if target_audio and target_meta:
            import shutil, time
            target_audio.parent.mkdir(parents=True, exist_ok=True)
            target_meta.write_text(json.dumps(boundaries), "utf-8")
            shutil.copy2(asset_path, target_audio)
            return target_audio, boundaries, metadata

        return asset_path, boundaries, metadata
