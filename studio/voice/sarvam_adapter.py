"""Sarvam AI TTS adapter for Telugu Reel Studio (Phase 4).
Connects to Sarvam Bulbul neural TTS models with Telugu speakers (meera, pavithra, arvind, amartya).
"""
import base64
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import httpx
from ..config import DATA, settings
from .. import media
from .registry import get_voice_capability

logger = logging.getLogger("studio.voice.sarvam")

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"


def probe(path):
    return media.probe(path)


class SarvamTTSAdapter:
    """Adapter for Sarvam AI Bulbul TTS API with Telugu native speaker personas."""

    def __init__(self):
        self.provider = "sarvam"

    def synthesize(
        self,
        text: str,
        voice: str = "meera",
        rate: int = 0,
        pitch: int = 0,
        target_audio: Optional[Path] = None,
        target_meta: Optional[Path] = None
    ) -> Tuple[Path, List[Dict[str, Any]], Dict[str, Any]]:
        """Synthesize Telugu speech using Sarvam API and return (audio_path, boundaries, metadata)."""
        cfg = settings()
        api_key = cfg.get("sarvam_key") or os.environ.get("SARVAM_API_KEY", "")
        if not api_key:
            raise ValueError("Configure your Sarvam API key in Settings & providers before synthesizing with Sarvam.")

        cap = get_voice_capability(voice, provider="sarvam")
        speaker = cap.voice_id if cap.voice_id in ("meera", "pavithra", "arvind", "amartya") else "meera"
        model = cap.model or "bulbul:v3"

        # Map rate (-25 to +25) to pace (0.75 to 1.25)
        pace = round(1.0 + (rate / 100.0), 2)
        pace = max(0.5, min(2.0, pace))

        # Sarvam API request payload
        payload = {
            "text": text,
            "model": model,
            "language_code": "te-IN",
            "speaker": speaker,
            "pitch": pitch,
            "pace": pace,
            "enable_preprocessing": True
        }

        headers = {
            "api-subscription-key": api_key,
            "Content-Type": "application/json"
        }

        try:
            with httpx.Client(timeout=60.0) as client:
                res = client.post(SARVAM_TTS_URL, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise ValueError(f"Sarvam API connection failed ({type(exc).__name__}). Check internet connectivity.") from None

        if res.status_code >= 400:
            detail = ""
            try:
                err_data = res.json()
                detail = err_data.get("message") or err_data.get("error", "")
            except Exception:
                detail = res.text[:200]
            raise ValueError(f"Sarvam API returned HTTP {res.status_code}: {detail or 'Check credentials and quota.'}")

        data = res.json()
        audio_b64 = ""
        if "audios" in data and isinstance(data["audios"], list) and len(data["audios"]) > 0:
            audio_b64 = data["audios"][0]
        elif "audio" in data and isinstance(data["audio"], str):
            audio_b64 = data["audio"]

        if not audio_b64:
            raise ValueError("Sarvam API returned response without audio payload.")

        raw_audio_bytes = base64.b64decode(audio_b64)

        unique = uuid.uuid4().hex[:8]
        if target_audio:
            target_audio.parent.mkdir(parents=True, exist_ok=True)
            temp_audio = target_audio.with_suffix(f".{unique}.part.wav")
            temp_meta = (target_meta or target_audio.with_suffix(".json")).with_suffix(f".{unique}.part.json")
        else:
            temp_dir = DATA / "tmp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_audio = temp_dir / f"sarvam_{unique}.wav"
            temp_meta = temp_dir / f"sarvam_{unique}.json"

        temp_audio.write_bytes(raw_audio_bytes)

        # Probe audio duration
        info = probe(temp_audio)
        dur = float(info.get("format", {}).get("duration", 0.0))
        if dur <= 0:
            temp_audio.unlink(missing_ok=True)
            raise ValueError("Sarvam API generated audio with invalid duration")

        # Generate text-proportional word boundaries for caption alignment
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
            "provider": "sarvam",
            "voice": speaker,
            "model": model,
            "duration": dur,
            "rate": rate,
            "pitch": pitch,
            "timing_type": "duration_proportional",
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
