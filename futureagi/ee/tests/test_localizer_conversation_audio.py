"""Simulation utterance boundaries and non-bypassable speaker attribution."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ee.evals.localizer.agentcc_transport import MediaRegistry
from ee.evals.localizer.claude_harness import (
    SYSTEM_PROMPT,
    LocalizationCase,
    create_localizer,
)
from ee.evals.localizer.conversation_audio import (
    create_utterance_segments,
    simulation_audio_snapshot,
)
from ee.tests.test_localizer_claude_harness import finding, localizer, submission

pytestmark = pytest.mark.unit


def test_audio_prompt_requires_evidence_and_does_not_penalize_backchannels():
    assert "instead of paraphrasing the evaluation" in SYSTEM_PROMPT
    assert "brief\nbackchannels" in SYSTEM_PROMPT
    assert "ordinary overlap alone is not evidence" in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "provider,direction,agent_role",
    [
        ("vapi", "inbound", "user"),
        ("vapi", "outbound", "assistant"),
        ("livekit", "inbound", "assistant"),
        ("livekit", "outbound", "assistant"),
    ],
)
def test_snapshot_uses_ui_role_resolution_and_recording_offset(
    provider, direction, agent_role
):
    from simulate.models.test_execution import CallTranscript

    rows = [
        CallTranscript(
            speaker_role=role,
            content=role,
            start_time_ms=i * 1000,
            end_time_ms=i * 1000 + 500,
        )
        for i, role in enumerate(["assistant", "user"])
    ]
    call = SimpleNamespace(
        simulation_call_type="voice",
        transcripts=Mock(),
        call_metadata={"call_direction": direction, "recording_offset_ms": 250},
        provider_call_data={provider: {"id": "test"}},
    )
    call.transcripts.exclude.return_value = rows
    config = SimpleNamespace(
        mapping={
            "output": "call.voice_recording",
            "agent": "assistant_recording",
            "sim": "customer_recording",
            "other": "unrelated_audio",
        }
    )
    snapshot = simulation_audio_snapshot(
        call, config, dict.fromkeys(config.mapping, "audio")
    )
    turns = snapshot["output"]
    assert [(t["start_time"], t["end_time"]) for t in turns] == [
        (0.25, 0.75),
        (1.25, 1.75),
    ]
    assert (
        next(t for t in turns if t["speaker_role"] == "assistant")["content"]
        == agent_role
    )
    # Raw recording slots follow the provider, not normalized display roles.
    assert snapshot["agent"] == [t for t in turns if t["content"] == "assistant"]
    assert snapshot["sim"] == [t for t in turns if t["content"] == "user"]
    assert snapshot["other"] == []


@pytest.fixture
def conversation(monkeypatch):
    from pydub import AudioSegment

    audio = BytesIO()
    AudioSegment.silent(duration=3000).export(audio, format="wav")
    monkeypatch.setattr(
        "ee.evals.localizer.conversation_audio.upload_audio_to_s3",
        lambda *a, **k: "https://example.test/utterance.mp3",
    )
    turns = [
        {
            "utterance_id": "sim-1",
            "speaker_role": "user",
            "content": "Question",
            "start_time": 0,
            "end_time": 0.75,
        },
        {
            "utterance_id": "agent-1",
            "speaker_role": "assistant",
            "content": "Wrong answer",
            "start_time": 0.65,
            "end_time": 1.85,
        },
        {
            "utterance_id": "agent-2",
            "speaker_role": "assistant",
            "content": "More",
            "start_time": 2,
            "end_time": 2.5,
        },
    ]
    return audio.getvalue(), turns


def test_cuts_exact_utterances_preserving_overlap_and_metadata(conversation):
    raw, turns = conversation
    units, _, _ = create_utterance_segments(raw, turns)
    assert len(units) == 3
    assert units["segment_1"]["eligible_for_findings"] is False
    assert units["segment_2"]["eligible_for_findings"] is True
    assert units["segment_2"]["duration"] == pytest.approx(1.2)
    import base64

    from pydub import AudioSegment

    clip = AudioSegment.from_file(
        BytesIO(base64.b64decode(units["segment_2"]["audio_bytes"]))
    )
    assert len(clip) == 1200
    assert units["segment_2"]["utterance_id"] == "agent-1"


def test_duplicate_partial_and_final_utterance_become_one_finding_unit(conversation):
    raw, turns = conversation
    duplicated = [
        turns[0],
        {
            "utterance_id": "agent-partial",
            "speaker_role": "assistant",
            "content": "Wrong",
            "start_time": 0.65,
            "end_time": 1.85,
        },
        turns[1],
        turns[2],
    ]

    units, _, _ = create_utterance_segments(raw, duplicated)

    assert list(units) == ["segment_1", "segment_2", "segment_3"]
    assert units["segment_2"]["utterance_id"] == "agent-1"
    assert units["segment_2"]["content"] == "Wrong answer"


def test_snapshot_deduplicates_provider_partial_and_final_rows():
    from simulate.models.test_execution import CallTranscript

    partial = CallTranscript(
        speaker_role="assistant",
        content="I'm transferring",
        start_time_ms=15866,
        end_time_ms=30918,
    )
    final = CallTranscript(
        speaker_role="assistant",
        content="I'm transferring you to a human agent now.",
        start_time_ms=15866,
        end_time_ms=30918,
    )
    call = SimpleNamespace(
        simulation_call_type="voice",
        transcripts=Mock(),
        call_metadata={},
        provider_call_data={"livekit": {"id": "test"}},
    )
    call.transcripts.exclude.return_value = [partial, final]
    config = SimpleNamespace(mapping={"conversation": "voice_recording"})

    snapshot = simulation_audio_snapshot(call, config, {"conversation": "audio"})

    assert len(snapshot["conversation"]) == 1
    assert (
        snapshot["conversation"][0]["content"]
        == "I'm transferring you to a human agent now."
    )


@pytest.mark.parametrize(
    "start,end", [(None, 1), (0, None), (0, 0), (-1, 1), (1, 9), (float("nan"), 1)]
)
def test_invalid_timing_never_falls_back_to_fixed_chunks(conversation, start, end):
    raw, turns = conversation
    with pytest.raises(ValueError, match="No timed tested-agent"):
        create_utterance_segments(
            raw, [{**turns[1], "start_time": start, "end_time": end}]
        )


async def test_agent_only_findings_and_ranked_utterance_metadata(conversation):
    raw, turns = conversation
    loc = localizer(
        {"output": raw, "context": "Simulator made a mistake."},
        {"output": "audio", "context": "text"},
    )
    loc.simulation_audio = {"output": turns}
    case = LocalizationCase(loc, MediaRegistry())
    await case.inspect_input({"key": "output"})
    await case.inspect_units(
        {"key": "output", "unit_keys": ["segment_1", "segment_2", "segment_3"]}
    )
    # Even inspected simulator speech cannot be blamed.
    with pytest.raises(ValueError, match="context only"):
        await case.submit_findings(submission(entries=[finding("segment_1")]))
    with pytest.raises(ValueError, match="tested-agent audio"):
        await case.submit_findings(
            submission(entries=[finding("whole_audio")], outcome="whole_input")
        )
    await case.inspect_input({"key": "context"})
    with pytest.raises(ValueError, match="tested-agent audio"):
        await case.submit_findings(
            submission(key="context", entries=[finding("sentence_1")])
        )
    await case.submit_findings(
        submission(entries=[finding("segment_3", "2"), finding("segment_2", "1")])
    )
    first, second = case.result.analysis["input_1"]
    assert first["orgSegment"]["utterance_id"] == "agent-1"
    assert first["orgSegment"]["speaker_role"] == "assistant"
    assert first["orgSegment"]["start_time"] == 0.65
    assert second["orgSegment"]["utterance_id"] == "agent-2"


async def test_rejects_two_findings_for_the_same_audio_interval(conversation):
    raw, _ = conversation
    loc = localizer({"output": raw}, {"output": "audio"})
    loc.simulation_audio = {"output": []}
    case = LocalizationCase(loc, MediaRegistry())
    duplicate_units = {
        "segment_1": {
            "utterance_id": "partial",
            "speaker_role": "assistant",
            "content": "I'm transferring",
            "start_time": 0.65,
            "end_time": 1.85,
            "duration": 1.2,
            "audio_bytes": "unused",
            "url": "https://example.test/partial.mp3",
            "eligible_for_findings": True,
        },
        "segment_2": {
            "utterance_id": "final",
            "speaker_role": "assistant",
            "content": "I'm transferring you now.",
            "start_time": 0.65,
            "end_time": 1.85,
            "duration": 1.2,
            "audio_bytes": "unused",
            "url": "https://example.test/final.mp3",
            "eligible_for_findings": True,
        },
    }
    case.inputs["output"] = ("audio", raw, duplicate_units, [], None)
    case.inspected["output"] = set(duplicate_units)

    with pytest.raises(ValueError, match="one finding per unique audio"):
        await case.submit_findings(
            submission(entries=[finding("segment_1", "1"), finding("segment_2", "2")])
        )
    assert case.result is None


async def test_missing_snapshot_cannot_use_legacy_chunks(conversation):
    raw, _ = conversation
    loc = localizer({"output": raw}, {"output": "audio"})
    loc.simulation_audio = {}
    case = LocalizationCase(loc, MediaRegistry())
    with pytest.raises(ValueError, match="No conversation"):
        await case.inspect_input({"key": "output"})
    await case.submit_findings(submission(entries=[], outcome="unlocalizable"))
    assert case.result.skip_reason


def test_legacy_backend_cannot_bypass_simulator_guard(monkeypatch):
    monkeypatch.setenv("ERROR_LOCALIZER_BACKEND", "legacy")
    with pytest.raises(ValueError, match="requires claude_agent_sdk"):
        create_localizer(simulation_audio={})
