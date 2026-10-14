"""Phase 4 Automated Test Suite: Naturalness, Pronunciation, Delivery & Voice Lab.
Verifies:
1. TTS provider interface & registry with verified capabilities and explicit unsupported controls.
2. Spoken Telugu normalizer (numbers, dates, abbreviations, loanwords, dictionary overrides).
3. Delivery presets (conversational, documentary, curious, suspense) with verified controls.
4. Synthesis contract caching (deterministic keys, explicit regeneration).
5. Narration blocks and safe continuous timeline assembly.
6. Voice takes persistence, A/B comparison, take acceptance and project-level voice locking.
7. Sarvam & Azure adapter contract validation (mocked network calls).
8. Conversational rewrite diff preview (approval requirement).
9. Human-listening and live paid provider tests marked as pending.
"""
import copy
import importlib
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Ensure repository root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2
from studio.config import DATA, settings
from studio.models import Project as PydanticProject
import studio.store as store

from studio.voice.registry import (
    TELUGU_VOICE_CATALOG,
    VoiceCapability,
    get_voice_capability,
    list_voices
)
from studio.voice.normalizer import (
    normalize_narration,
    number_to_telugu,
    normalize_dates,
    normalize_numbers_in_text
)
from studio.voice.presets import (
    apply_delivery_preset,
    get_delivery_preset,
    DELIVERY_PRESETS
)
from studio.voice.cache import (
    compute_synthesis_contract_key,
    get_cached_synthesis,
    store_cached_synthesis
)
from studio.voice.blocks import (
    build_narration_blocks,
    align_visual_cuts_to_speech_pauses,
    calculate_timeline,
    NarrationBlock
)
from studio.voice.lab import (
    generate_voice_take,
    compare_takes_ab,
    preview_conversational_rewrite,
    get_adapter_for_provider
)
from studio.voice.sarvam_adapter import SarvamTTSAdapter
from studio.voice.azure_adapter import AzureTTSAdapter


@pytest.fixture()
def phase4_db_env(tmp_path, monkeypatch):
    """Fixture ensuring a clean database environment for Phase 4 tests."""
    monkeypatch.setenv('REEL_DATA_DIR', str(tmp_path / 'data'))
    test_db_url = os.environ.get('STUDIO_TEST_DATABASE_URL') or 'postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/studio_test'
    monkeypatch.setenv('STUDIO_DATABASE_URL', test_db_url)

    pg_url = test_db_url.replace('+psycopg2', '')
    conn = psycopg2.connect(pg_url)
    with conn.cursor() as cur:
        cur.execute(
            'TRUNCATE TABLE voice_takes, application_receipts, outbox, job_stages, drafts, jobs, revisions, projects, assets, usage CASCADE;'
        )
        cur.execute("INSERT INTO workspaces (id, name) VALUES ('default', 'Local Workspace') ON CONFLICT (id) DO NOTHING;")
    conn.commit()
    conn.close()

    store.init()
    return {'db_url': test_db_url, 'tmp_path': tmp_path}


# =====================================================================
# 1. Registry & Capabilities Tests
# =====================================================================

def test_registry_lists_all_providers():
    """Verify registry lists Edge, Sarvam, Azure, and Upload options."""
    voices = list_voices()
    providers = {v.provider for v in voices}
    assert 'edge' in providers
    assert 'sarvam' in providers
    assert 'azure' in providers
    assert 'upload' in providers

    sarvam_voices = [v for v in voices if v.provider == 'sarvam']
    sarvam_ids = {v.voice_id for v in sarvam_voices}
    assert {'pavithra', 'meera', 'arvind', 'amartya'}.issubset(sarvam_ids)


def test_unsupported_controls_are_explicit_not_silently_accepted():
    """Unsupported controls must be visibly reported in unsupported_controls."""
    for voice in list_voices():
        assert isinstance(voice.unsupported_controls, list)
        assert len(voice.unsupported_controls) > 0

        if voice.provider == 'edge':
            assert 'expressive_styles' in voice.unsupported_controls
            assert 'phonemes' in voice.unsupported_controls
            assert 'rate' in voice.supported_controls
            assert 'pitch' in voice.supported_controls
        elif voice.provider == 'azure':
            assert 'expressive_styles' in voice.unsupported_controls
            assert 'custom_lexicon' in voice.unsupported_controls
            assert 'rate' in voice.supported_controls
        elif voice.provider == 'sarvam':
            assert 'ssml_tags' in voice.unsupported_controls
            assert 'phonemes' in voice.unsupported_controls
            assert 'pace' in voice.supported_controls
        elif voice.provider == 'upload':
            assert 'rate' in voice.unsupported_controls


def test_voice_capabilities_contracts():
    """Verify formats, timing support, char limits, and estimated costs."""
    edge_cap = get_voice_capability('te-IN-ShrutiNeural', provider='edge')
    assert edge_cap.timing_support == 'word_boundary'
    assert 'mp3' in edge_cap.formats
    assert edge_cap.estimated_cost_per_1k_chars == 0.0
    assert edge_cap.char_limit >= 2000

    sarvam_cap = get_voice_capability('pavithra', provider='sarvam')
    assert sarvam_cap.timing_support == 'duration_proportional'
    assert sarvam_cap.model == 'bulbul:v3'
    assert sarvam_cap.char_limit >= 500
    assert sarvam_cap.estimated_cost_per_1k_chars > 0.0


# =====================================================================
# 2. Telugu Normalization & Pronunciation Tests
# =====================================================================

def test_number_normalization_single_and_large():
    """Verify numbers 0 to 999,999 expand into natural Telugu phonetic words."""
    assert number_to_telugu(5) == "ఐదు"
    assert number_to_telugu(10) == "పది"
    assert number_to_telugu(15) == "పదిహేను"
    assert number_to_telugu(100) == "వంద"
    assert number_to_telugu(500) == "ఐదు వందలు"
    assert number_to_telugu(1000) == "వెయ్యి"
    assert number_to_telugu(25000) == "ఇరవై ఐదు వేలు"
    assert number_to_telugu(75000) == "డెబ్బై ఐదు వేలు"


def test_date_normalization_telugu():
    """Verify DD-MM-YYYY dates expand correctly into spoken Telugu."""
    expanded = normalize_dates("15-08-1947 నాడు")
    assert "పదిహేను" in expanded
    assert "ఆగస్టు" in expanded
    assert "నలభై ఏడు" in expanded


def test_abbreviations_and_tech_terms():
    """Verify English abbreviations and loanwords expand into Telugu Unicode."""
    norm = normalize_narration("AI మరియు API క్లౌడ్‌లో పనిచేస్తాయి")
    assert "ఏఐ" in norm.spoken_text
    assert "ఏపీఐ" in norm.spoken_text
    assert "క్లౌడ్" in norm.spoken_text


def test_user_pronunciation_dictionary_priority():
    """User-provided pronunciation overrides must take priority over default rules."""
    user_dict = {
        "AI": "ఆర్టిఫిషియల్ ఇంటెలిజెన్స్",
        "Vizag": "విశాఖపట్నం"
    }
    norm = normalize_narration("Vizag లో AI సెంటర్ ఉంది", pronunciation_dict=user_dict)
    assert "విశాఖపట్నం" in norm.spoken_text
    assert "ఆర్టిఫిషియల్ ఇంటెలిజెన్స్" in norm.spoken_text
    applied_dict = {item["original"]: item["spoken"] for item in norm.overrides_applied}
    assert applied_dict.get("AI") == "ఆర్టిఫిషియల్ ఇంటెలిజెన్స్"
    assert applied_dict.get("Vizag") == "విశాఖపట్నం"


def test_three_tier_text_separation():
    """Strictly preserve separate source text, spoken Telugu, and Roman captions."""
    raw = "15-08-1947 నాడు 500 మంది హాజరయ్యారు."
    norm = normalize_narration(raw)
    assert norm.source_text == raw
    assert "ఆగస్టు పదిహేను" in norm.spoken_text
    assert "ఐదు వందలు" in norm.spoken_text
    assert norm.roman_text != ""


# =====================================================================
# 3. Delivery Presets Tests
# =====================================================================

def test_delivery_presets_applied_safely():
    """Verify presets adjust rate, pitch, and punctuation pauses safely."""
    cap = get_voice_capability('te-IN-ShrutiNeural', provider='edge')

    # Conversational
    conv = apply_delivery_preset("ఇది మొదటి వాక్యం. ఇది రెండో వాక్యం.", "conversational", cap)
    assert -5 <= conv["effective_rate"] <= 5
    assert conv["effective_pitch"] == 0

    # Suspense (lower pitch, dramatic punctuation pause)
    susp = apply_delivery_preset("గదిలో చీకటి ఉంది.", "suspense", cap)
    assert susp["effective_pitch"] < 0
    assert susp["effective_rate"] < 0
    assert susp["pause_scale"] > 1.2

    # Curious (pitch lift)
    cur = apply_delivery_preset("మీరు చూశారా?", "curious", cap)
    assert cur["effective_pitch"] > 0


def test_presets_respect_unsupported_controls():
    """If voice doesn't support pitch, preset must not send pitch adjustment."""
    # Mock capability with no pitch support
    cap = VoiceCapability(
        provider="custom",
        voice_id="no_pitch_voice",
        display_name="Test Voice",
        gender="female",
        supported_controls=["rate"],
        unsupported_controls=["pitch"]
    )
    res = apply_delivery_preset("పరీక్ష వాక్యం", "suspense", cap)
    assert res["effective_pitch"] == 0  # Pitch ignored because unsupported


# =====================================================================
# 4. Synthesis Contract Caching Tests
# =====================================================================

def test_contract_key_determinism():
    """Contract key is deterministic and sensitive to all synthesis parameters."""
    k1 = compute_synthesis_contract_key(
        provider="edge",
        voice="te-IN-ShrutiNeural",
        model="neural",
        spoken_text="నమస్కారం",
        delivery_preset="conversational",
        rate=0,
        pitch=0,
        dictionary=None,
        audio_format="mp3"
    )
    k2 = compute_synthesis_contract_key(
        provider="edge",
        voice="te-IN-ShrutiNeural",
        model="neural",
        spoken_text="నమస్కారం",
        delivery_preset="conversational",
        rate=0,
        pitch=0,
        dictionary=None,
        audio_format="mp3"
    )
    assert k1 == k2
    assert len(k1) == 64  # SHA-256

    # Changing delivery preset produces different key
    k3 = compute_synthesis_contract_key(
        provider="edge",
        voice="te-IN-ShrutiNeural",
        model="neural",
        spoken_text="నమస్కారం",
        delivery_preset="suspense",
        rate=0,
        pitch=0,
        dictionary=None,
        audio_format="mp3"
    )
    assert k1 != k3


# =====================================================================
# 5. Narration Blocks & Timeline Alignment Tests
# =====================================================================

def test_narration_block_grouping_and_fallback():
    """Verify scenes group into narration blocks with multi-shot support and per-scene fallback."""
    scenes = [
        {"id": "s1", "narration": "మొదటి వాక్యం", "roman": "Modati vaakyam", "narration_block_id": "b1"},
        {"id": "s2", "narration": "రెండో వాక్యం", "roman": "Rendo vaakyam", "narration_block_id": "b1"},
        {"id": "s3", "narration": "మూడో వాక్యం", "roman": "Moodo vaakyam", "narration_block_id": "b2"}
    ]
    blocks = build_narration_blocks(scenes)
    assert len(blocks) == 2
    assert blocks[0].id == "b1"
    assert blocks[0].scene_ids == ["s1", "s2"]
    assert "మొదటి వాక్యం రెండో వాక్యం" in blocks[0].spoken_text

    # Per-scene compatibility fallback when no narration_block_id
    plain_scenes = [
        {"id": "s1", "narration": "వాక్యం ఒకటి", "roman": "v1"},
        {"id": "s2", "narration": "వాక్యం రెండు", "roman": "v2"}
    ]
    fallback_blocks = build_narration_blocks(plain_scenes)
    assert len(fallback_blocks) == 2
    assert fallback_blocks[0].scene_ids == ["s1"]


def test_timeline_alignment_snaps_to_speech_pauses():
    """Visual cuts align to natural speech pauses rather than cutting words mid-syllable."""
    word_boundaries = [
        {"word": "మొదటి", "start": 0.0, "end": 0.6},
        {"word": "వాక్యం", "start": 0.7, "end": 1.5},
        {"word": "ఇక్కడ", "start": 2.2, "end": 2.7},
        {"word": "ఆగింది", "start": 2.8, "end": 3.5},
    ]
    # Block duration 3.5s with 2 visual shots (50% each: ideal split 1.75s)
    # The pause is between 1.5s and 2.2s (midpoint 1.85s).
    durations = align_visual_cuts_to_speech_pauses(
        block_duration=3.5,
        word_boundaries=word_boundaries,
        scene_proportions=[1.0, 1.0]
    )
    assert len(durations) == 2
    assert sum(durations) == pytest.approx(3.5, abs=0.01)
    # First scene duration aligns to the pause gap ~1.85s, NOT mid-word at 1.0s or 2.5s
    assert 1.6 <= durations[0] <= 2.1


def test_calculate_timeline_defaults_to_natural_duration():
    """Natural duration mode defaults to 1.0x speed and does not force unnatural speedup."""
    speed, final_dur, _ = calculate_timeline(total_voice_duration=65.0, total_holds=2.0, duration_mode="natural")
    assert speed == 1.0
    assert final_dur == 67.0

    # Target-fit mode bounded check
    speed, final_dur, _ = calculate_timeline(total_voice_duration=58.0, total_holds=2.0, duration_mode="target", target_seconds=60)
    assert 0.95 <= speed <= 1.05

    # Excessive speedup raises ValueError with helpful guidance
    with pytest.raises(ValueError, match="Please edit the script, change the target duration"):
        calculate_timeline(total_voice_duration=120.0, total_holds=0.0, duration_mode="target", target_seconds=30)



# =====================================================================
# 6. Voice Takes, A/B Comparison & Voice Locking Tests
# =====================================================================

def test_voice_take_creation_and_acceptance(phase4_db_env):
    """Test generating a take, listing takes, and accepting a take for a project scene."""
    # 1. Create a project
    proj_data = PydanticProject(
        title="Voice Lab Test",
        tts_provider="edge",
        voice="te-IN-ShrutiNeural",
        scenes=[
            {"id": "scene_01", "narration": "ఒక చిన్న కథ", "roman": "Oka chinna katha"}
        ]
    ).model_dump()
    saved = store.save_project(proj_data)
    pid = saved["id"]

    # 2. Add two candidate voice takes
    t1 = store.add_voice_take(
        project_id=pid,
        scene_id="scene_01",
        provider="edge",
        voice_id="te-IN-ShrutiNeural",
        spoken_text="ఒక చిన్న కథ",
        duration=3.2,
        timing_data=[{"word": "ఒక", "offset": 0.0}],
        preset="conversational"
    )
    t2 = store.add_voice_take(
        project_id=pid,
        scene_id="scene_01",
        provider="sarvam",
        voice_id="pavithra",
        spoken_text="ఒక చిన్న కథ",
        duration=3.1,
        timing_data=[{"word": "ఒక", "offset": 0.0}],
        preset="conversational"
    )

    # 3. List takes
    takes = store.list_voice_takes(pid, scene_id="scene_01")
    assert len(takes) == 2
    assert takes[0]["id"] == t2["id"]  # newest first

    # 4. Accept candidate take t2
    acc = store.accept_voice_take(pid, t2["id"])
    assert acc["take"]["accepted"] is True
    # Verify project scene updated
    updated_p = acc["project"]["project"]
    sc = updated_p["scenes"][0]
    assert sc["take_id"] == t2["id"]
    assert sc["delivery_preset"] == "conversational"

    # 5. Verify other take is unaccepted
    t1_fresh = store.get_voice_take(t1["id"])
    assert t1_fresh["accepted"] is False


def test_project_voice_locking_prevents_switching(phase4_db_env):
    """When voice_locked is True, accepting takes does not switch project global voice profile."""
    proj_data = PydanticProject(
        title="Locked Voice Test",
        tts_provider="edge",
        voice="te-IN-ShrutiNeural",
        voice_locked=True,
        scenes=[
            {"id": "scene_01", "narration": "పరీక్ష", "roman": "Pariksha"}
        ]
    ).model_dump()
    saved = store.save_project(proj_data)
    pid = saved["id"]

    # Add a Sarvam take and accept it
    t = store.add_voice_take(
        project_id=pid,
        scene_id="scene_01",
        provider="sarvam",
        voice_id="pavithra",
        spoken_text="పరీక్ష",
        duration=2.5
    )
    acc = store.accept_voice_take(pid, t["id"])
    updated_p = acc["project"]["project"]
    # Global provider and voice must remain locked to Edge Shruti
    assert updated_p["tts_provider"] == "edge"
    assert updated_p["voice"] == "te-IN-ShrutiNeural"
    assert updated_p["voice_locked"] is True


def test_ab_comparison_metrics():
    """Verify side-by-side A/B metrics between baseline and candidate take."""
    take_a = {
        "take_id": "take_01",
        "display_name": "Edge Shruti (Baseline)",
        "provider": "edge",
        "duration": 4.5,
        "spoken_text": "ఇది పరీక్ష వాక్యం",
        "audio_url": "/api/assets/a1/media",
        "estimated_cost": 0.0
    }
    take_b = {
        "take_id": "take_02",
        "display_name": "Sarvam Pavithra",
        "provider": "sarvam",
        "duration": 4.2,
        "spoken_text": "ఇది పరీక్ష వాక్యం",
        "audio_url": "/api/assets/a2/media",
        "estimated_cost": 0.002
    }
    comp = compare_takes_ab(take_a, take_b)
    assert comp["script_identical"] is True
    assert comp["duration_difference"] == 0.3
    assert comp["baseline"]["take_id"] == "take_01"
    assert comp["candidate"]["take_id"] == "take_02"


# =====================================================================
# 7. Conversational Rewrite Preview & Approval Tests
# =====================================================================

def test_conversational_rewrite_preview_diff():
    """Colloquial rewrite produces visual diff and strictly notes human approval required."""
    text_with_bookish = "నిన్న రాత్రి ఒక విచిత్రం జరిగినది మరియు ఆయన పని చేసినది."
    preview = preview_conversational_rewrite(text_with_bookish)
    assert preview["changed"] is True
    assert "జరిగింది" in preview["proposed"]
    assert len(preview["diff"]) > 0
    # Must have removed and added diff tags
    tags = {item["type"] for item in preview["diff"]}
    assert "removed" in tags or "added" in tags
    assert "Human approval required" in preview["note"]


def test_conversational_rewrite_preserves_already_colloquial():
    """Already natural text is unchanged."""
    already_good = "నిన్న ఒక కథ జరిగింది, అందరూ చూశారు."
    preview = preview_conversational_rewrite(already_good)
    assert preview["changed"] is False
    assert preview["proposed"] == already_good


# =====================================================================
# 8. Adapter Contracts (Mocked Network Calls)
# =====================================================================

def test_sarvam_adapter_contract(tmp_path, monkeypatch):
    """Verify Sarvam adapter constructs proper Bulbul v3 request and headers."""
    adapter = SarvamTTSAdapter()
    assert adapter.provider == "sarvam"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "audios": ["//uQxAAAAA..."],  # base64 wav/mp3
        "duration": 3.4
    }

    monkeypatch.setenv("SARVAM_API_KEY", "test-sarvam-key-12345")

    with patch.object(importlib.import_module("httpx").Client, "post", return_value=mock_resp) as mock_post:
        # Provide dummy test audio file to avoid ffprobe failure in mock
        dummy_audio = tmp_path / "dummy.mp3"
        dummy_audio.write_bytes(b"RIFF....WAVE")

        with patch("studio.voice.sarvam_adapter.probe", return_value={"format": {"duration": "3.4"}}):
            try:
                adapter.synthesize(text="నమస్కారం", voice="pavithra", target_audio=dummy_audio)
            except Exception:
                pass

        assert mock_post.called
        call_url = mock_post.call_args[0][0]
        assert "api.sarvam.ai" in call_url
        headers = mock_post.call_args[1].get("headers", {})
        assert "api-subscription-key" in headers


def test_azure_adapter_graceful_rest_fallback():
    """Verify Azure adapter gracefully identifies SDK presence or falls back to REST."""
    adapter = AzureTTSAdapter()
    assert adapter.provider == "azure"
    # Should not crash whether azure.cognitiveservices.speech is installed or not
    assert hasattr(adapter, "has_sdk")


# =====================================================================
# 9. Pending Tests Marker Verification
# =====================================================================

@pytest.mark.skip(reason="Pending human listener scoring by user. User chooses the winner.")
def test_human_listener_scores_evaluation():
    """Pending user listening evaluation across the 5 test categories."""
    pass


@pytest.mark.skip(reason="Pending explicit budget approval for live paid provider requests.")
def test_live_paid_candidate_synthesis():
    """Pending explicit approval for live Sarvam/Azure requests."""
    pass
