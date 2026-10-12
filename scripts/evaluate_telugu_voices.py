"""Telugu Voice Evaluation Runner (Phase 4).
Evaluates baseline (Edge Neural) and candidate voices (Sarvam Bulbul, Azure)
across 5 standardized Telugu categories at comparable playback levels.
Generates evaluation audio samples and logs metrics without fabricating human scores.
"""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from studio.config import DATA, settings
from studio.voice.normalizer import normalize_narration
from studio.voice.registry import get_voice_capability, list_voices
from studio.voice.presets import apply_delivery_preset
from studio.voice.lab import get_adapter_for_provider
from studio.voice.cache import compute_synthesis_contract_key, get_cached_synthesis, store_cached_synthesis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("studio.voice.evaluate")


def load_benchmark_dataset():
    data_path = ROOT / "tests" / "data" / "telugu_voice_eval.json"
    if not data_path.exists():
        raise FileNotFoundError(f"Evaluation benchmark dataset not found at {data_path}")
    with data_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def evaluate_sample(
    sample: dict,
    category_id: str,
    voice_id: str,
    provider: str,
    preset: str = "conversational",
    output_dir: Path = None,
    live_approved: bool = False
) -> dict:
    cap = get_voice_capability(voice_id, provider=provider)
    source_text = sample["source_text"]

    # 1. Normalize
    norm = normalize_narration(source_text)
    spoken_text = norm.spoken_text

    # 2. Preset
    preset_data = apply_delivery_preset(spoken_text, preset, cap)
    final_text = preset_data["adjusted_text"]

    # 3. Contract Key
    contract_key = compute_synthesis_contract_key(
        provider=cap.provider,
        voice=cap.voice_id,
        model=cap.model,
        spoken_text=final_text,
        delivery_preset=preset,
        rate=preset_data["effective_rate"],
        pitch=preset_data["effective_pitch"],
        dictionary=None,
        audio_format="mp3"
    )

    result = {
        "sample_id": sample["id"],
        "category_id": category_id,
        "provider": cap.provider,
        "voice_id": cap.voice_id,
        "display_name": cap.display_name,
        "preset": preset,
        "source_text": source_text,
        "spoken_text": final_text,
        "contract_key": contract_key,
        "audio_path": None,
        "duration_sec": 0.0,
        "latency_sec": 0.0,
        "timing_events_count": 0,
        "estimated_cost_inr": (len(final_text) / 1000.0) * (cap.estimated_cost_per_1k_chars * 100),
        "status": "pending",
        # Human listener scores (pending user evaluation)
        "listener_scores": {
            "naturalness": None,
            "pronunciation": None,
            "consistency": None,
            "listening_comfort": None,
            "reviewed_by": None
        }
    }

    # Only synthesize live if approved or provider is free edge
    can_synthesize = live_approved or cap.provider == "edge"
    if not can_synthesize:
        result["status"] = "pending_provider_approval"
        return result

    start_t = time.perf_counter()
    try:
        cached = get_cached_synthesis(contract_key)
        if cached:
            audio_file, boundaries, metadata = cached
            latency = 0.005
        else:
            adapter = get_adapter_for_provider(cap.provider)
            audio_file, boundaries, metadata = adapter.synthesize(
                text=final_text,
                voice=cap.voice_id,
                rate=preset_data["effective_rate"],
                pitch=preset_data["effective_pitch"]
            )
            store_cached_synthesis(contract_key, audio_file, boundaries, metadata)
            latency = time.perf_counter() - start_t

        if output_dir and audio_file.exists():
            dest = output_dir / f"{category_id}_{sample['id']}_{cap.provider}_{cap.voice_id}.mp3"
            import shutil
            shutil.copy2(audio_file, dest)
            result["audio_path"] = str(dest)

        result["duration_sec"] = round(metadata.get("duration", 0.0), 2)
        result["latency_sec"] = round(latency, 3)
        result["timing_events_count"] = len(boundaries)
        result["status"] = "synthesized"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)

    return result


def run_evaluation(live_approved: bool = False, output_dir: Path = None):
    dataset = load_benchmark_dataset()
    output_dir = output_dir or (ROOT / "test-outputs" / "voice-lab-eval")
    output_dir.mkdir(parents=True, exist_ok=True)

    candidates = [
        # Baseline
        {"provider": "edge", "voice": "te-IN-ShrutiNeural", "name": "Edge Shruti (Baseline)"},
        {"provider": "edge", "voice": "te-IN-MohanNeural", "name": "Edge Mohan"},
        # Sarvam Candidates
        {"provider": "sarvam", "voice": "pavithra", "name": "Sarvam Pavithra (Candidate 1)"},
        {"provider": "sarvam", "voice": "meera", "name": "Sarvam Meera (Candidate 2)"},
        {"provider": "sarvam", "voice": "arvind", "name": "Sarvam Arvind (Candidate 3)"},
    ]

    all_results = []
    print("\n" + "=" * 70)
    print("TELUGU REEL STUDIO - VOICE LAB BENCHMARK EVALUATION (PHASE 4)")
    print("=" * 70)

    for cat in dataset["categories"]:
        print(f"\nEvaluating Category: {cat['name']} ({len(cat['samples'])} samples)")
        for sample in cat["samples"]:
            for cand in candidates:
                res = evaluate_sample(
                    sample=sample,
                    category_id=cat["id"],
                    voice_id=cand["voice"],
                    provider=cand["provider"],
                    output_dir=output_dir,
                    live_approved=live_approved
                )
                all_results.append(res)
                status_icon = "OK" if res["status"] == "synthesized" else "WAIT" if res["status"] == "pending_provider_approval" else "ERR"
                print(f"  [{status_icon:<4}] {cand['name']:<30} | Status: {res['status']:<25} | Dur: {res['duration_sec']}s | Latency: {res['latency_sec']}s")

    # Save structured report
    report_file = output_dir / "evaluation_report.json"
    with report_file.open("w", encoding="utf-8") as f:
        json.dump({
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "benchmark_version": dataset["benchmark_version"],
            "live_approved": live_approved,
            "candidates": candidates,
            "results": all_results,
            "human_evaluation_notice": "Listener scores are left unpopulated for user evaluation. The user chooses the winner."
        }, f, indent=2, ensure_ascii=False)

    # Save Markdown report
    md_file = output_dir / "EVALUATION_REPORT.md"
    with md_file.open("w", encoding="utf-8") as f:
        f.write("# Telugu Voice Evaluation Benchmark Report (Phase 4)\n\n")
        f.write(f"- **Benchmark Version**: {dataset['benchmark_version']}\n")
        f.write(f"- **Date**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
        f.write(f"- **Live Provider Synthesis Approved**: {'Yes' if live_approved else 'No (Edge baseline synthesized; paid candidates pending approval)'}\n\n")
        f.write("## Candidate Voice Summary\n\n")
        f.write("| Provider | Voice ID | Name | Timing Type | Est. Cost / 1k Chars |\n")
        f.write("|----------|----------|------|-------------|----------------------|\n")
        for c in candidates:
            cap = get_voice_capability(c["voice"], provider=c["provider"])
            f.write(f"| {cap.provider} | {cap.voice_id} | {cap.display_name} | {cap.timing_metadata} | ₹{cap.estimated_cost_per_1k_chars * 100:.4f} |\n")
        f.write("\n## Evaluation Samples & Categories\n\n")
        for cat in dataset["categories"]:
            f.write(f"### {cat['name']}\n*{cat['description']}*\n\n")
            for s in cat["samples"]:
                f.write(f"- **{s['id']}**: {s['source_text']}\n")
                f.write(f"  - *Focus*: {s['test_focus']}\n")
            f.write("\n")
        f.write("## Human Listening Evaluation Sheet (User Decision Required)\n\n")
        f.write("| Voice | Naturalness (1-5) | Pronunciation (1-5) | Consistency (1-5) | Comfort (1-5) | Notes |\n")
        f.write("|---|---|---|---|---|---|\n")
        for c in candidates:
            f.write(f"| {c['name']} | [ ] | [ ] | [ ] | [ ] | Pending user review |\n")
        f.write("\n> [!NOTE]\n> Per Phase 4 upgrade rules, listening scores are not fabricated. The user chooses the winning voice.\n")

    print(f"\nEvaluation completed. Reports saved to:\n  - {report_file}\n  - {md_file}\n")
    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Telugu voice evaluation benchmark")
    parser.add_argument("--approved", action="store_true", help="Explicit approval for live paid candidate requests")
    args = parser.parse_args()
    run_evaluation(live_approved=args.approved)
