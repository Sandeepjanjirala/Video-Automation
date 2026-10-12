"""Restrained Delivery Presets for Telugu Reel Studio (Phase 4).
Implements delivery styles strictly through verified provider controls (rate, pitch, pauses)
without inserting phantom style tags or fake vocal sounds.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional
from .registry import VoiceCapability


@dataclass
class DeliveryPreset:
    name: str
    display_name: str
    rate_percent: int  # e.g. 0, -5, +2, -12
    pitch_hz: int      # e.g. 0, -2, +2, -3
    pause_scale: float # Multiplier for natural pause spacing
    description: str


DELIVERY_PRESETS: Dict[str, DeliveryPreset] = {
    "conversational": DeliveryPreset(
        name="conversational",
        display_name="Conversational (Natural Daily Rhythm)",
        rate_percent=0,
        pitch_hz=0,
        pause_scale=1.0,
        description="Relaxed, everyday spoken Telugu rhythm suitable for stories, vlogs, and explainers."
    ),
    "documentary": DeliveryPreset(
        name="documentary",
        display_name="Documentary (Measured & Authoritative)",
        rate_percent=-6,
        pitch_hz=-2,
        pause_scale=1.25,
        description="Deliberate, grounded delivery with dignified pauses for history and investigative journalism."
    ),
    "curious": DeliveryPreset(
        name="curious",
        display_name="Curious (Engaged & Dynamic)",
        rate_percent=3,
        pitch_hz=2,
        pause_scale=1.05,
        description="Lively, question-driven prosody for intriguing trivia, science, and surprising revelations."
    ),
    "suspense": DeliveryPreset(
        name="suspense",
        display_name="Suspense (Tense & Dramatic)",
        rate_percent=-12,
        pitch_hz=-4,
        pause_scale=1.5,
        description="Slower, intense cadence with dramatic silence before plot reveals and mystery moments."
    ),
}


def get_delivery_preset(name: Optional[str]) -> DeliveryPreset:
    """Return requested delivery preset or fallback to conversational."""
    if not name or name not in DELIVERY_PRESETS:
        return DELIVERY_PRESETS["conversational"]
    return DELIVERY_PRESETS[name]


def apply_delivery_preset(
    text: str,
    preset_name: str,
    voice_cap: VoiceCapability,
    base_rate: int = 0,
    base_pitch: int = 0
) -> Dict[str, Any]:
    """Calculate effective rate, pitch, and phrasing pauses for the requested preset.
    Respects provider capabilities: unsupported controls are clamped or omitted.
    """
    preset = get_delivery_preset(preset_name)

    # Calculate effective rate & pitch within bounds
    eff_rate = max(-25, min(25, base_rate + preset.rate_percent))
    eff_pitch = max(-10, min(10, base_pitch + preset.pitch_hz))

    # For providers that support pitch/rate
    has_rate = "rate" in voice_cap.supported_controls or "pace" in voice_cap.supported_controls
    has_pitch = "pitch" in voice_cap.supported_controls

    final_rate = eff_rate if has_rate else 0
    final_pitch = eff_pitch if has_pitch else 0

    # Phrasing pauses: for suspense or documentary, ensure commas and ellipses
    # have adequate natural spacing for TTS breath cadence
    adjusted_text = text
    if preset.pause_scale > 1.2:
        # Subtle punctuation breathing room without synthetic noise
        adjusted_text = adjusted_text.replace("...", "... ").replace("! ", "!  ")

    return {
        "preset": preset.name,
        "effective_rate": final_rate,
        "effective_pitch": final_pitch,
        "adjusted_text": adjusted_text.strip(),
        "pause_scale": preset.pause_scale
    }
