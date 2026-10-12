"""Voice package for Telugu Reel Studio (Phase 4)."""
from .registry import (
    VoiceCapability, TELUGU_VOICE_CATALOG, list_voices,
    get_voice_capability, validate_voice_controls
)
from .normalizer import (
    normalize_narration, number_to_telugu, normalize_dates,
    apply_pronunciation_dictionary, NormalizedNarration
)
from .presets import (
    DeliveryPreset, DELIVERY_PRESETS, get_delivery_preset,
    apply_delivery_preset
)
from .blocks import (
    NarrationBlock, build_narration_blocks,
    align_visual_cuts_to_speech_pauses, calculate_timeline
)
from .cache import (
    compute_synthesis_contract_key, get_cached_synthesis,
    store_cached_synthesis
)
from .lab import (
    generate_voice_take, compare_takes_ab,
    preview_conversational_rewrite, get_adapter_for_provider
)

__all__ = [
    "VoiceCapability", "TELUGU_VOICE_CATALOG", "list_voices",
    "get_voice_capability", "validate_voice_controls",
    "normalize_narration", "number_to_telugu", "normalize_dates",
    "apply_pronunciation_dictionary", "NormalizedNarration",
    "DeliveryPreset", "DELIVERY_PRESETS", "get_delivery_preset",
    "apply_delivery_preset",
    "NarrationBlock", "build_narration_blocks",
    "align_visual_cuts_to_speech_pauses", "calculate_timeline",
    "compute_synthesis_contract_key", "get_cached_synthesis",
    "store_cached_synthesis",
    "generate_voice_take", "compare_takes_ab",
    "preview_conversational_rewrite", "get_adapter_for_provider"
]
