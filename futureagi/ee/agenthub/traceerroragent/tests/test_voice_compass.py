"""
Quick test for Voice Compass on the Fast Food project.
Run inside Docker: docker exec -it backend python agentic_eval/agenthub/traceerroragent/test_voice_compass.py
"""

import json
import os
import sys
import time

import pytest

pytestmark = pytest.mark.e2e

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")

import django

django.setup()

from ee.agenthub.traceerroragent.voice_compass import VoiceCompassAgent
from ee.agenthub.traceerroragent.voice_summary_builder import VoiceSummaryBuilder
from tracer.models.observation_span import ObservationSpan

# Fast Food project traces (ordered by turn count)
TRACE_IDS = [
    "d03b3da4-5de7-4e79-a716-3adbfbceaf0a",  # 10 turns
    "a70a65c2-952e-4c1d-9315-1c4a6c538283",  # 5 turns
]

SAVE_TO_DB = os.getenv("SAVE_TO_DB", "0") == "1"


def test_extraction(trace_id: str):
    """Test just the data extraction step."""
    print(f"\n{'='*60}")
    print(f"Testing extraction for trace: {trace_id}")
    print(f"{'='*60}")

    span = ObservationSpan.objects.filter(
        trace_id=trace_id, observation_type="conversation"
    ).first()

    if not span:
        print(f"  ERROR: No conversation span found for trace {trace_id}")
        return None

    data = VoiceSummaryBuilder.extract(span)

    print(f"  Transcript turns: {len(data.transcript)}")
    print(f"  Provider transcript turns: {len(data.provider_transcript)}")
    print(f"  Has audio: {data.has_audio}")
    print(f"  Has tool calls: {data.has_tool_calls}")
    print(f"  Turn latencies: {len(data.turn_latencies)}")
    print(f"  Avg agent latency: {data.avg_agent_latency_ms}")
    print(f"  User interruptions: {data.user_interruption_count}")
    print(f"  AI interruptions: {data.ai_interruption_count}")
    print(f"  Ended reason: {data.ended_reason}")
    print(f"  LLM model: {data.llm_model_name}")
    print(f"  Audio URL: {VoiceSummaryBuilder.get_best_audio_url(data)}")

    print(f"\n--- Formatted transcript (first 500 chars) ---")
    transcript = VoiceSummaryBuilder.format_transcript_for_prompt(data)
    print(transcript[:500])

    print(f"\n--- Formatted metrics ---")
    print(VoiceSummaryBuilder.format_metrics_for_prompt(data))

    print(f"\n--- Formatted metadata ---")
    print(VoiceSummaryBuilder.format_call_metadata_for_prompt(data))

    return data


def test_full_pipeline(trace_id: str):
    """Test the full Voice Compass pipeline."""
    print(f"\n{'='*60}")
    print(f"Running full Voice Compass for trace: {trace_id}")
    print(f"Save to DB: {SAVE_TO_DB}")
    print(f"{'='*60}")

    start = time.time()
    agent = VoiceCompassAgent(trace_id=trace_id, save_to_db=SAVE_TO_DB)
    result = agent.summarize()
    elapsed = time.time() - start

    print(f"\n--- Results ({elapsed:.1f}s) ---")
    print(f"Overall score: {result.get('summary', {}).get('overall_score')}")
    print(f"Error count: {result.get('summary', {}).get('error_count')}")
    print(f"Priority: {result.get('summary', {}).get('recommended_priority')}")
    print(f"Insights: {result.get('summary', {}).get('insights', '')[:300]}")

    scores = result.get("scores", {})
    for dim in ["factual_grounding", "privacy_and_safety", "instruction_adherence", "optimal_plan_execution"]:
        s = scores.get(dim, {})
        print(f"  {dim}: {s.get('score')} — {s.get('reason', '')[:100]}")

    errors = result.get("errors", [])
    print(f"\nErrors ({len(errors)}):")
    for err in errors:
        print(f"  [{err.get('impact')}] {err.get('category')}")
        print(f"    {err.get('description', '')[:120]}")
        if err.get("evidence_snippets"):
            print(f"    Evidence: {err['evidence_snippets'][0][:100]}")

    # Token usage
    token_usage = result.get("token_usage", {})
    if token_usage:
        print(f"\n--- Token Usage ---")
        grand_total = 0
        for stage, usage in token_usage.items():
            total = usage.get("total_tokens", 0)
            prompt = usage.get("prompt_tokens", 0)
            completion = usage.get("completion_tokens", 0)
            cached = usage.get("cache_read_input_tokens", 0)
            print(f"  {stage}: {total} tokens (prompt={prompt}, completion={completion}, cached={cached})")
            grand_total += total
        print(f"  GRAND TOTAL: {grand_total} tokens")

    return result


if __name__ == "__main__":
    trace_id = sys.argv[1] if len(sys.argv) > 1 else TRACE_IDS[0]

    # Step 1: Test extraction only
    data = test_extraction(trace_id)

    if data and "--extract-only" not in sys.argv:
        # Step 2: Full pipeline
        result = test_full_pipeline(trace_id)

        # Save result to file for inspection
        output_path = f"/tmp/voice_compass_{trace_id[:8]}.json"
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\nFull result saved to: {output_path}")
