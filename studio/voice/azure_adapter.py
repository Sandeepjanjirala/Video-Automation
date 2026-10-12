"""Azure Speech TTS adapter for Telugu Reel Studio (Phase 4).
Supports Microsoft Cognitive Services Neural Telugu voices with Speech SDK event timing
when available, with graceful fallback to REST SSML endpoint.
"""
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape
import httpx
from ..config import DATA, settings
from .. import media
from .registry import get_voice_capability

logger = logging.getLogger("studio.voice.azure")

try:
    import azure.cognitiveservices.speech as speechsdk
    _HAS_AZURE_SDK = True
except ImportError:
    _HAS_AZURE_SDK = False


def probe(path):
    return media.probe(path)


class AzureTTSAdapter:
    """Adapter for Microsoft Azure Cognitive Services Speech."""

    def __init__(self):
        self.provider = "azure"
        self.has_sdk = _HAS_AZURE_SDK

    def _synthesize_via_sdk(
        self,
        text: str,
        voice: str,
        rate: int,
        pitch: int,
        api_key: str,
        region: str,
        temp_audio: Path
    ) -> List[Dict[str, Any]]:
        """Synthesize using Azure Speech SDK with word boundary events."""
        speech_config = speechsdk.SpeechConfig(subscription=api_key, region=region)
        speech_config.speech_synthesis_voice_name = voice
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Audio24Khz96KBitRateMonoMp3
        )
        audio_config = speechsdk.audio.AudioOutputConfig(filename=str(temp_audio))
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)

        boundaries = []

        def word_boundary_handler(evt):
            # Audio offset in ticks (1 tick = 100 nanoseconds = 1e-7 s)
            boundaries.append({
                "start": evt.audio_offset / 10_000_000,
                "end": (evt.audio_offset + evt.duration.total_seconds() * 10_000_000) / 10_000_000,
                "text": evt.text
            })

        synthesizer.synthesis_word_boundary.connect(word_boundary_handler)

        ssml = (
            f'<speak version="1.0" xml:lang="te-IN">'
            f'<voice name="{voice}">'
            f'<prosody rate="{rate:+d}%" pitch="{pitch:+d}Hz">{escape(text)}</prosody>'
            f'</voice></speak>'
        )

        result = synthesizer.speak_ssml_async(ssml).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            cancellation = speechsdk.SpeechSynthesisCancellationDetails.from_result(result)
            raise ValueError(f"Azure Speech SDK synthesis failed: {cancellation.error_details}")

        return boundaries

    def _synthesize_via_rest(
        self,
        text: str,
        voice: str,
        rate: int,
        pitch: int,
        api_key: str,
        region: str,
        temp_audio: Path
    ) -> List[Dict[str, Any]]:
        """Synthesize using Azure REST SSML endpoint."""
        url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        ssml = (
            f'<speak version="1.0" xml:lang="te-IN">'
            f'<voice name="{voice}">'
            f'<prosody rate="{rate:+d}%" pitch="{pitch:+d}Hz">{escape(text)}</prosody>'
            f'</voice></speak>'
        )

        headers = {
            "Ocp-Apim-Subscription-Key": api_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
            "User-Agent": "TeluguReelStudio"
        }

        try:
            with httpx.Client(timeout=60.0) as client:
                res = client.post(url, headers=headers, content=ssml.encode("utf-8"))
        except httpx.HTTPError as exc:
            raise ValueError(f"Azure Speech connection failed ({type(exc).__name__}).") from None

        if res.status_code >= 400:
            raise ValueError(f"Azure Speech returned HTTP {res.status_code}. Check key and region.")

        temp_audio.write_bytes(res.content)
        info = probe(temp_audio)
        dur = float(info.get("format", {}).get("duration", 0.0))

        # Generate estimated word boundaries from audio duration
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
        """Synthesize Telugu speech and return (audio_path, boundaries, metadata)."""
        cfg = settings()
        api_key = cfg.get("azure_key", "").strip()
        region = cfg.get("azure_region", "").strip()
        if not api_key or not re.fullmatch(r"[a-z0-9-]{2,40}", region):
            raise ValueError("Configure Azure Speech key and valid region in Settings before using Azure.")

        cap = get_voice_capability(voice, provider="azure")
        voice_id = cap.voice_id.replace("azure:", "")

        unique = uuid.uuid4().hex[:8]
        if target_audio:
            target_audio.parent.mkdir(parents=True, exist_ok=True)
            temp_audio = target_audio.with_suffix(f".{unique}.part.mp3")
            temp_meta = (target_meta or target_audio.with_suffix(".json")).with_suffix(f".{unique}.part.json")
        else:
            temp_dir = DATA / "tmp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_audio = temp_dir / f"azure_{unique}.mp3"
            temp_meta = temp_dir / f"azure_{unique}.json"

        timing_type = "word_boundary" if self.has_sdk else "duration_proportional"
        try:
            if self.has_sdk:
                try:
                    boundaries = self._synthesize_via_sdk(text, voice_id, rate, pitch, api_key, region, temp_audio)
                except Exception as sdk_err:
                    logger.warning(f"Azure SDK error, falling back to REST: {sdk_err}")
                    boundaries = self._synthesize_via_rest(text, voice_id, rate, pitch, api_key, region, temp_audio)
                    timing_type = "duration_proportional"
            else:
                boundaries = self._synthesize_via_rest(text, voice_id, rate, pitch, api_key, region, temp_audio)

            info = probe(temp_audio)
            dur = float(info.get("format", {}).get("duration", 0.0))
            if dur <= 0:
                raise ValueError("Azure Speech returned invalid or zero-length audio")

            metadata = {
                "provider": "azure",
                "voice": voice_id,
                "model": "Azure-Neural-v1",
                "duration": dur,
                "rate": rate,
                "pitch": pitch,
                "timing_type": timing_type,
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
            raise
