"""Synthesis Contract Caching for Telugu Reel Studio (Phase 4).
Caches synthesis artifacts by the complete contract:
provider + model + voice + normalized spoken text + delivery preset + rate + pitch + dict_hash + format.
"""
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from ..config import DATA


def compute_synthesis_contract_key(
    provider: str,
    voice: str,
    model: str,
    spoken_text: str,
    delivery_preset: str = "conversational",
    rate: int = 0,
    pitch: int = 0,
    dictionary: Optional[Dict[str, str]] = None,
    audio_format: str = "mp3"
) -> str:
    """Generate SHA-256 digest capturing the complete synthesis contract."""
    contract = {
        "version": 4,
        "provider": provider.strip().lower(),
        "voice": voice.strip(),
        "model": (model or "").strip(),
        "spoken_text": spoken_text.strip(),
        "delivery_preset": delivery_preset.strip().lower(),
        "rate": rate,
        "pitch": pitch,
        "dictionary": dict(sorted((dictionary or {}).items())),
        "format": audio_format.strip().lower(),
    }
    raw = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def get_cached_synthesis(
    contract_key: str,
    audio_format: str = "mp3"
) -> Optional[Tuple[Path, List[Dict[str, Any]], Dict[str, Any]]]:
    """Retrieve audio, word boundaries, and metadata if contract cache exists and is valid."""
    cache_dir = DATA / "cache"
    audio_file = cache_dir / f"{contract_key}.{audio_format}"
    timing_file = cache_dir / f"{contract_key}.timing.json"
    meta_file = cache_dir / f"{contract_key}.meta.json"

    if audio_file.exists() and timing_file.exists() and audio_file.stat().st_size > 0:
        try:
            boundaries = json.loads(timing_file.read_text("utf-8"))
            metadata = json.loads(meta_file.read_text("utf-8")) if meta_file.exists() else {}
            if isinstance(boundaries, list):
                return audio_file, boundaries, metadata
        except Exception:
            pass
    return None


def store_cached_synthesis(
    contract_key: str,
    src_audio: Path,
    boundaries: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    audio_format: str = "mp3"
) -> Tuple[Path, Path]:
    """Atomically store synthesis artifacts in contract cache."""
    cache_dir = DATA / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    dest_audio = cache_dir / f"{contract_key}.{audio_format}"
    dest_timing = cache_dir / f"{contract_key}.timing.json"
    dest_meta = cache_dir / f"{contract_key}.meta.json"

    # If already cached by a concurrent request, reuse immediately
    if dest_audio.exists() and dest_timing.exists() and dest_audio.stat().st_size > 0:
        return dest_audio, dest_timing

    unique = uuid.uuid4().hex[:8]
    temp_timing = cache_dir / f"{contract_key}.{unique}.timing.part"
    temp_meta = cache_dir / f"{contract_key}.{unique}.meta.part"
    temp_audio = cache_dir / f"{contract_key}.{unique}.audio.part"

    temp_timing.write_text(json.dumps(boundaries, indent=2), "utf-8")
    temp_meta.write_text(json.dumps(metadata, indent=2), "utf-8")

    import shutil
    import time
    if src_audio.resolve() != dest_audio.resolve():
        shutil.copy2(src_audio, temp_audio)
    else:
        return dest_audio, dest_timing

    # Atomic replace with retry for Windows file locks
    for attempt in range(5):
        try:
            if temp_audio.exists():
                temp_audio.replace(dest_audio)
            if temp_timing.exists():
                temp_timing.replace(dest_timing)
            if temp_meta.exists():
                temp_meta.replace(dest_meta)
            break
        except (PermissionError, OSError):
            if dest_audio.exists() and dest_timing.exists() and dest_audio.stat().st_size > 0:
                temp_audio.unlink(missing_ok=True)
                temp_timing.unlink(missing_ok=True)
                temp_meta.unlink(missing_ok=True)
                break
            if attempt == 4:
                if not (dest_audio.exists() and dest_audio.stat().st_size > 0):
                    raise
            time.sleep(0.05 * (attempt + 1))

    return dest_audio, dest_timing
