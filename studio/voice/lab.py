"""Voice Lab engine for Telugu Reel Studio (Phase 4).
Manages baseline comparisons, candidate previews, identical-script A/B playback,
pronunciation dictionaries, take histories, and conversational rewrites.
"""
import difflib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from ..config import DATA, settings
from django.db import transaction
from django.conf import settings as django_settings
from core.models import Asset, Project, VoiceTake

VOICE_DB = 'voice_writer' if 'voice_writer' in django_settings.DATABASES else 'default'
MEDIA_DB = 'media_writer' if 'media_writer' in django_settings.DATABASES else 'default'
from .cache import compute_synthesis_contract_key, get_cached_synthesis, store_cached_synthesis
from .edge_adapter import EdgeTTSAdapter
from .sarvam_adapter import SarvamTTSAdapter
from .azure_adapter import AzureTTSAdapter
from .upload_adapter import UploadNarrationAdapter
from .normalizer import normalize_narration
from .presets import apply_delivery_preset, get_delivery_preset
from .registry import get_voice_capability, list_voices

logger = logging.getLogger("studio.voice.lab")


def get_adapter_for_provider(provider: str):
    """Factory returning the appropriate TTS adapter for the provider."""
    p = (provider or "").strip().lower()
    if p == "sarvam":
        return SarvamTTSAdapter()
    elif p == "azure":
        return AzureTTSAdapter()
    elif p == "upload":
        return UploadNarrationAdapter()
    return EdgeTTSAdapter()


def generate_voice_take(
    project_id: str,
    scene_id: str,
    text: str,
    provider: str = "edge",
    voice: str = "te-IN-ShrutiNeural",
    delivery_preset: str = "conversational",
    rate: int = 0,
    pitch: int = 0,
    pronunciation_dict: Optional[Dict[str, str]] = None,
    explicit_regenerate: bool = False
) -> Dict[str, Any]:
    """Synthesize speech and record a versioned Voice Take."""
    cap = get_voice_capability(voice, provider=provider)

    # 1. Normalize text into spoken Telugu Unicode
    norm = normalize_narration(text, pronunciation_dict=pronunciation_dict)
    spoken_text = norm.spoken_text

    # 2. Apply delivery preset
    preset_settings = apply_delivery_preset(spoken_text, delivery_preset, cap, base_rate=rate, base_pitch=pitch)
    final_text = preset_settings["adjusted_text"]
    eff_rate = preset_settings["effective_rate"]
    eff_pitch = preset_settings["effective_pitch"]

    # 3. Check contract cache
    contract_key = compute_synthesis_contract_key(
        provider=cap.provider,
        voice=cap.voice_id,
        model=cap.model,
        spoken_text=final_text,
        delivery_preset=delivery_preset,
        rate=eff_rate,
        pitch=eff_pitch,
        dictionary=pronunciation_dict,
        audio_format="mp3"
    )

    cached = None if explicit_regenerate else get_cached_synthesis(contract_key)
    if cached:
        audio_file, boundaries, metadata = cached
    else:
        adapter = get_adapter_for_provider(cap.provider)
        audio_file, boundaries, metadata = adapter.synthesize(
            text=final_text,
            voice=cap.voice_id,
            rate=eff_rate,
            pitch=eff_pitch
        )
        # Store in contract cache
        audio_file, _ = store_cached_synthesis(contract_key, audio_file, boundaries, metadata)

    # 4. Copy to asset library for project playback
    take_id = f"take_{uuid.uuid4().hex[:12]}"
    asset_id = uuid.uuid4().hex
    asset_filename = f"{asset_id}.mp3"
    dest_path = DATA / "assets" / asset_filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    import shutil
    shutil.copy2(audio_file, dest_path)

    # Register in PostgreSQL Asset table and VoiceTake table
    with transaction.atomic(using=VOICE_DB):
        Asset.objects.using(MEDIA_DB).create(
            id=asset_id,
            workspace_id="default",
            filename=asset_filename,
            name=f"Voice Take ({cap.display_name})",
            kind="audio",
            duration=metadata.get("duration", 0.0),
            provider=cap.provider,
            meta={
                "take_id": take_id,
                "project_id": project_id,
                "scene_id": scene_id,
                "voice": cap.voice_id,
                "provider": cap.provider,
                "model": cap.model,
                "preset": delivery_preset,
                "contract_key": contract_key,
                "boundaries": boundaries
            }
        )

        # Persist VoiceTake if project exists
        proj = Project.objects.using(VOICE_DB).filter(id=project_id).first() if project_id else None
        if proj:
            VoiceTake.objects.using(VOICE_DB).create(
                id=take_id,
                project_id=project_id,
                scene_id=scene_id or None,
                provider=cap.provider,
                voice_id=cap.voice_id,
                model=cap.model,
                preset=delivery_preset,
                spoken_text=final_text,
                roman_text=norm.roman_text,
                asset_id=asset_id,
                duration=metadata.get("duration", 0.0),
                timing_data=boundaries,
                accepted=False,
                contract_key=contract_key,
                meta={
                    "display_name": cap.display_name,
                    "source_text": norm.source_text,
                    "rate": eff_rate,
                    "pitch": eff_pitch,
                    "estimated_cost": (len(final_text) / 1000.0) * cap.estimated_cost_per_1k_chars,
                    "overrides_applied": norm.overrides_applied
                }
            )

    return {
        "take_id": take_id,
        "asset_id": asset_id,
        "audio_url": f"/api/assets/{asset_id}/media",
        "contract_key": contract_key,
        "provider": cap.provider,
        "voice": cap.voice_id,
        "display_name": cap.display_name,
        "model": cap.model,
        "preset": delivery_preset,
        "duration": metadata.get("duration", 0.0),
        "source_text": norm.source_text,
        "spoken_text": final_text,
        "roman_text": norm.roman_text,
        "boundaries": boundaries,
        "estimated_cost": (len(final_text) / 1000.0) * cap.estimated_cost_per_1k_chars,
        "overrides_applied": norm.overrides_applied,
        "created_at": datetime.now(timezone.utc).isoformat()
    }


def compare_takes_ab(take_a: Dict[str, Any], take_b: Dict[str, Any]) -> Dict[str, Any]:
    """Generate side-by-side A/B comparison metrics between baseline and candidate take."""
    return {
        "baseline": {
            "take_id": take_a.get("take_id"),
            "display_name": take_a.get("display_name"),
            "provider": take_a.get("provider"),
            "audio_url": take_a.get("audio_url"),
            "duration": take_a.get("duration"),
            "preset": take_a.get("preset"),
            "estimated_cost": take_a.get("estimated_cost", 0.0)
        },
        "candidate": {
            "take_id": take_b.get("take_id"),
            "display_name": take_b.get("display_name"),
            "provider": take_b.get("provider"),
            "audio_url": take_b.get("audio_url"),
            "duration": take_b.get("duration"),
            "preset": take_b.get("preset"),
            "estimated_cost": take_b.get("estimated_cost", 0.0)
        },
        "duration_difference": round(abs(take_a.get("duration", 0.0) - take_b.get("duration", 0.0)), 2),
        "script_identical": take_a.get("spoken_text") == take_b.get("spoken_text")
    }


def preview_conversational_rewrite(source_text: str) -> Dict[str, Any]:
    """Optional conversational-rewrite preview that improves natural Telugu rhythm.
    Preserves exact facts, names, numbers and uncertainty. Generates visual diff for approval.
    """
    text = source_text.strip()
    if not text:
        return {"original": "", "proposed": "", "diff": [], "changed": False}

    # Conversational phrasing polish:
    # 1. Natural spoken connectives (అయితే, కానీ, అప్పుడు, చూడండి)
    # 2. Smooth clause flow without bookish literary endings
    proposed = text

    # Bookish to colloquial spoken Telugu endings
    replacements = [
        ("ఉన్నది", "ఉంది"),
        ("వచ్చినది", "వచ్చింది"),
        ("పోయినది", "పోయింది"),
        ("అయినది", "అయింది"),
        ("జరిగినది", "జరిగింది"),
        ("చేసినది", "చేశారు"),
        ("చేయబడినది", "చేశారు"),
        ("చూడండి.", "చూడండి!"),
    ]
    for old, new in replacements:
        proposed = proposed.replace(old, new)

    # Compute word diff
    orig_words = text.split()
    prop_words = proposed.split()
    matcher = difflib.SequenceMatcher(None, orig_words, prop_words)
    diff = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            diff.append({"type": "equal", "text": " ".join(orig_words[i1:i2])})
        elif tag == "replace":
            diff.append({"type": "removed", "text": " ".join(orig_words[i1:i2])})
            diff.append({"type": "added", "text": " ".join(prop_words[j1:j2])})
        elif tag == "delete":
            diff.append({"type": "removed", "text": " ".join(orig_words[i1:i2])})
        elif tag == "insert":
            diff.append({"type": "added", "text": " ".join(prop_words[j1:j2])})

    return {
        "original": text,
        "proposed": proposed,
        "diff": diff,
        "changed": text != proposed,
        "note": "Human approval required. Original narration will not be changed without approval."
    }
