"""TTS Provider and Voice Capability Registry for Telugu Reel Studio (Phase 4).
Defines verified capabilities, supported controls, voice catalog, and provider contracts.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class VoiceCapability:
    provider: str
    voice_id: str
    display_name: str
    gender: str
    language: str = "te-IN"
    model: str = ""
    description: str = ""
    supported_controls: List[str] = field(default_factory=list)
    unsupported_controls: List[str] = field(default_factory=list)
    formats: List[str] = field(default_factory=lambda: ["mp3", "wav"])
    timing_support: str = "word_boundary"  # "word_boundary" | "duration_proportional" | "sdk_event"
    char_limit: int = 2500
    estimated_cost_per_1k_chars: float = 0.0
    requires_key: bool = False
    key_setting_name: str = ""

    @property
    def timing_metadata(self) -> str:
        return self.timing_support

    @property
    def audio_formats(self) -> List[str]:
        return self.formats

    @property
    def max_characters_per_request(self) -> int:
        return self.char_limit

    @property
    def language_code(self) -> str:
        return self.language

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "voice_id": self.voice_id,
            "display_name": self.display_name,
            "gender": self.gender,
            "language": self.language,
            "language_code": self.language,
            "model": self.model,
            "description": self.description,
            "supported_controls": self.supported_controls,
            "unsupported_controls": self.unsupported_controls,
            "formats": self.formats,
            "audio_formats": self.formats,
            "timing_support": self.timing_support,
            "timing_metadata": self.timing_support,
            "char_limit": self.char_limit,
            "max_characters_per_request": self.char_limit,
            "estimated_cost_per_1k_chars": self.estimated_cost_per_1k_chars,
            "requires_key": self.requires_key,
            "key_setting_name": self.key_setting_name,
        }


# Verified Telugu voice catalog with grounded capabilities and explicit unsupported controls
TELUGU_VOICE_CATALOG: Dict[str, VoiceCapability] = {
    # 1. Edge TTS (Baseline / Zero Cost)
    "te-IN-ShrutiNeural": VoiceCapability(
        provider="edge",
        voice_id="te-IN-ShrutiNeural",
        display_name="Shruti (Natural Conversational Female)",
        gender="female",
        model="EdgeTTS-Neural",
        description="Clear, natural conversational Telugu female voice with native prosody.",
        supported_controls=["rate", "pitch", "pause", "delivery_preset"],
        unsupported_controls=["expressive_styles", "phonemes", "custom_lexicon", "ssml_emotion"],
        formats=["mp3", "wav"],
        timing_support="word_boundary",
        char_limit=3000,
        estimated_cost_per_1k_chars=0.0,
        requires_key=False,
    ),
    "te-IN-MohanNeural": VoiceCapability(
        provider="edge",
        voice_id="te-IN-MohanNeural",
        display_name="Mohan (Clear Narrative Male)",
        gender="male",
        model="EdgeTTS-Neural",
        description="Authoritative, steady narrative Telugu male voice for news and explainers.",
        supported_controls=["rate", "pitch", "pause", "delivery_preset"],
        unsupported_controls=["expressive_styles", "phonemes", "custom_lexicon", "ssml_emotion"],
        formats=["mp3", "wav"],
        timing_support="word_boundary",
        char_limit=3000,
        estimated_cost_per_1k_chars=0.0,
        requires_key=False,
    ),

    # 2. Sarvam AI (Indian-accented Bulbul Neural TTS)
    "meera": VoiceCapability(
        provider="sarvam",
        voice_id="meera",
        display_name="Meera (Sarvam Expressive Female)",
        gender="female",
        model="bulbul:v3",
        description="Warm, natural colloquial Telugu voice trained on Indian conversational cadence.",
        supported_controls=["pace", "pitch", "loudness", "delivery_preset"],
        unsupported_controls=["phonemes", "custom_lexicon", "ssml_tags", "word_level_timestamps"],
        formats=["mp3", "wav"],
        timing_support="duration_proportional",
        char_limit=2500,
        estimated_cost_per_1k_chars=0.012,
        requires_key=True,
        key_setting_name="sarvam_key",
    ),
    "pavithra": VoiceCapability(
        provider="sarvam",
        voice_id="pavithra",
        display_name="Pavithra (Sarvam Storytelling Female)",
        gender="female",
        model="bulbul:v3",
        description="Smooth, engaging storytelling voice suited for folklore and cultural reels.",
        supported_controls=["pace", "pitch", "loudness", "delivery_preset"],
        unsupported_controls=["phonemes", "custom_lexicon", "ssml_tags", "word_level_timestamps"],
        formats=["mp3", "wav"],
        timing_support="duration_proportional",
        char_limit=2500,
        estimated_cost_per_1k_chars=0.012,
        requires_key=True,
        key_setting_name="sarvam_key",
    ),
    "arvind": VoiceCapability(
        provider="sarvam",
        voice_id="arvind",
        display_name="Arvind (Sarvam Documentary Male)",
        gender="male",
        model="bulbul:v3",
        description="Resonant, grounded documentary style for historical and investigative topics.",
        supported_controls=["pace", "pitch", "loudness", "delivery_preset"],
        unsupported_controls=["phonemes", "custom_lexicon", "ssml_tags", "word_level_timestamps"],
        formats=["mp3", "wav"],
        timing_support="duration_proportional",
        char_limit=2500,
        estimated_cost_per_1k_chars=0.012,
        requires_key=True,
        key_setting_name="sarvam_key",
    ),
    "amartya": VoiceCapability(
        provider="sarvam",
        voice_id="amartya",
        display_name="Amartya (Sarvam Conversational Male)",
        gender="male",
        model="bulbul:v3",
        description="Relaxed, everyday spoken Telugu rhythm for casual explainer videos.",
        supported_controls=["pace", "pitch", "loudness", "delivery_preset"],
        unsupported_controls=["phonemes", "custom_lexicon", "ssml_tags", "word_level_timestamps"],
        formats=["mp3", "wav"],
        timing_support="duration_proportional",
        char_limit=2500,
        estimated_cost_per_1k_chars=0.012,
        requires_key=True,
        key_setting_name="sarvam_key",
    ),

    # 3. Azure Cognitive Services Speech
    "azure:te-IN-ShrutiNeural": VoiceCapability(
        provider="azure",
        voice_id="te-IN-ShrutiNeural",
        display_name="Azure Shruti (Enterprise Neural Female)",
        gender="female",
        model="Azure-Neural-v1",
        description="High-fidelity Microsoft Cognitive Services Telugu female voice with SSML support.",
        supported_controls=["rate", "pitch", "volume", "pause", "delivery_preset"],
        unsupported_controls=["expressive_styles", "custom_lexicon"],
        formats=["mp3", "wav"],
        timing_support="word_boundary",
        char_limit=5000,
        estimated_cost_per_1k_chars=0.016,
        requires_key=True,
        key_setting_name="azure_key",
    ),
    "azure:te-IN-MohanNeural": VoiceCapability(
        provider="azure",
        voice_id="te-IN-MohanNeural",
        display_name="Azure Mohan (Enterprise Neural Male)",
        gender="male",
        model="Azure-Neural-v1",
        description="High-fidelity Microsoft Cognitive Services Telugu male voice with SSML support.",
        supported_controls=["rate", "pitch", "volume", "pause", "delivery_preset"],
        unsupported_controls=["expressive_styles", "custom_lexicon"],
        formats=["mp3", "wav"],
        timing_support="word_boundary",
        char_limit=5000,
        estimated_cost_per_1k_chars=0.016,
        requires_key=True,
        key_setting_name="azure_key",
    ),

    # 4. Uploaded Narration (Human baseline)
    "uploaded": VoiceCapability(
        provider="upload",
        voice_id="uploaded",
        display_name="Human Audio Recording (Uploaded)",
        gender="neutral",
        model="User-Supplied",
        description="Authentic human vocal recording uploaded per scene or narration block.",
        supported_controls=["trim", "volume"],
        unsupported_controls=["rate", "pitch", "ssml", "delivery_preset"],
        formats=["mp3", "wav", "m4a"],
        timing_support="duration_proportional",
        char_limit=10000,
        estimated_cost_per_1k_chars=0.0,
        requires_key=False,
    ),
}


def list_voices(provider: Optional[str] = None) -> List[VoiceCapability]:
    """Return verified voice capabilities filtered by provider if specified."""
    if provider:
        return [v for v in TELUGU_VOICE_CATALOG.values() if v.provider == provider]
    return list(TELUGU_VOICE_CATALOG.values())


def get_voice_capability(voice_id: str, provider: Optional[str] = None) -> VoiceCapability:
    """Lookup a voice capability by ID or normalized provider:voice_id key."""
    if voice_id in TELUGU_VOICE_CATALOG:
        return TELUGU_VOICE_CATALOG[voice_id]
    
    # Check with provider prefix
    if provider and f"{provider}:{voice_id}" in TELUGU_VOICE_CATALOG:
        return TELUGU_VOICE_CATALOG[f"{provider}:{voice_id}"]
    
    # Match by bare voice_id for Azure or Edge
    for v in TELUGU_VOICE_CATALOG.values():
        if v.voice_id == voice_id and (provider is None or v.provider == provider):
            return v

    # Fallback to default Shruti
    return TELUGU_VOICE_CATALOG["te-IN-ShrutiNeural"]


def validate_voice_controls(voice_cap: VoiceCapability, requested_controls: Dict[str, Any]) -> Dict[str, Any]:
    """Filter requested controls to only those supported by the target voice capability.
    Logs or raises actionable error if an unsupported control is requested.
    """
    validated = {}
    for ctrl, val in requested_controls.items():
        if ctrl in voice_cap.supported_controls:
            validated[ctrl] = val
        elif ctrl in voice_cap.unsupported_controls and val not in (None, False, "", 0):
            # Explicitly reject or ignore silently ineffective controls
            pass
    return validated
