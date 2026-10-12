"""Edge TTS adapter for Telugu Reel Studio (Phase 4).
Reliable, zero-cost conversational Telugu synthesis with verified WordBoundary timing.
"""
import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import edge_tts
from ..config import DATA, settings
from .. import media
from .registry import get_voice_capability

logger = logging.getLogger("studio.voice.edge")


def probe(path):
    return media.probe(path)


class EdgeTTSAdapter:
    """Adapter for Microsoft Edge Neural TTS voices (te-IN-ShrutiNeural, te-IN-MohanNeural)."""

    def __init__(self):
        self.provider = "edge"

    async def _stream_synthesis(
        self,
        text: str,
        voice: str,
        rate: int,
        pitch: int,
        temp_audio: Path
    ) -> List[Dict[str, Any]]:
        rate_str = f"{rate:+d}%"
        pitch_str = f"{pitch:+d}Hz" if pitch != 0 else "+0Hz"
        boundaries = []

        engine = edge_tts.Communicate(
            text,
            voice=voice,
            rate=rate_str,
            pitch=pitch_str,
            boundary="WordBoundary"
        )
        with temp_audio.open("wb") as output:
            async for chunk in engine.stream():
                if chunk["type"] == "audio":
                    output.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    boundaries.append({
                        "start": chunk["offset"] / 10_000_000,
                        "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                        "text": chunk["text"]
                    })
        return boundaries

    def synthesize(
        self,
        text: str,
        voice: str = "te-IN-ShrutiNeural",
        rate: int = 0,
        pitch: int = 0,
        target_audio: Optional[Path] = None,
        target_meta: Optional[Path] = None
    ) -> Tuple[Path, List[Dict[str, Any]], Dict[str, Any]]:
        """Synthesize Telugu speech and return (audio_path, word_boundaries, metadata)."""
        cap = get_voice_capability(voice, provider="edge")
        unique = uuid.uuid4().hex[:8]
        if target_audio:
            target_audio.parent.mkdir(parents=True, exist_ok=True)
            temp_audio = target_audio.with_suffix(f".{unique}.part.mp3")
            temp_meta = (target_meta or target_audio.with_suffix(".json")).with_suffix(f".{unique}.part.json")
        else:
            temp_dir = DATA / "tmp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_audio = temp_dir / f"edge_{unique}.mp3"
            temp_meta = temp_dir / f"edge_{unique}.json"

        try:
            boundaries = asyncio.run(
                asyncio.wait_for(
                    self._stream_synthesis(text, cap.voice_id, rate, pitch, temp_audio),
                    timeout=120
                )
            )

            # Probe audio to verify it's valid
            info = probe(temp_audio)
            dur = float(info.get("format", {}).get("duration", 0.0))
            if dur <= 0:
                raise ValueError("Edge TTS generated zero-duration audio")

            metadata = {
                "provider": "edge",
                "voice": cap.voice_id,
                "model": cap.model,
                "duration": dur,
                "rate": rate,
                "pitch": pitch,
                "timing_type": "word_boundary",
                "boundaries_count": len(boundaries)
            }

            if target_audio and target_meta:
                import shutil
                import time
                target_audio.parent.mkdir(parents=True, exist_ok=True)
                temp_meta.write_text(json.dumps(boundaries), "utf-8")
                for attempt in range(5):
                    try:
                        shutil.move(str(temp_meta), str(target_meta))
                        shutil.move(str(temp_audio), str(target_audio))
                        break
                    except (PermissionError, OSError):
                        if attempt == 4:
                            raise
                        time.sleep(0.05 * (attempt + 1))
                return target_audio, boundaries, metadata

            return temp_audio, boundaries, metadata
        except Exception as exc:
            temp_audio.unlink(missing_ok=True)
            temp_meta.unlink(missing_ok=True)
            logger.warning(f"Edge TTS synthesis error: {exc}")
            raise
