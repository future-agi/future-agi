"""Segment planning, manifests and F01 recording-import conformance (no DB)."""

import copy
import json
import os
import struct
import sys
import uuid
from pathlib import Path

import pytest

from tracer.services.attachments import imports, recordings, sandbox
from tracer.services.attachments.contract import AttachmentError

pytestmark = [pytest.mark.unit]

FIXTURES = Path(__file__).parent / "fixtures" / "attachments_f01"
TABLE = json.loads((FIXTURES / "recording-import-fixtures.json").read_text())


def override(base, changes):
    if isinstance(base, dict) and isinstance(changes, dict):
        result = copy.deepcopy(base)
        for key, value in changes.items():
            result[key] = override(result.get(key), value)
        return result
    return copy.deepcopy(changes)


def facts(source):
    return imports.SourceFacts(
        terminal_completed=source["terminal_completed"],
        immutable=source["immutable"],
        bounded_ranges=source["bounded_ranges"],
        total_bytes=TABLE["base"]["request"]["recording"]["declared_bytes"]
        if "declared_bytes" not in source
        else source["declared_bytes"],
        direction="mixed",
    )


def run_case(case):
    scenario = override(TABLE["base"], case["overrides"])
    recording = scenario["request"]["recording"]
    limits = recordings.Limits.from_wire(scenario["limits"])
    if case["stage"] == "admission":
        source = dict(scenario["source"], declared_bytes=recording["declared_bytes"])
        imports.admission_check(
            recording=recording,
            limits=limits,
            headroom=scenario["headroom"],
            source=facts(source),
        )
    else:
        imports.completion_check(
            recording=recording, limits=limits, observation=scenario["observation"]
        )


@pytest.mark.parametrize("case", TABLE["cases"], ids=lambda case: case["id"])
def test_f01_recording_import_fixture(case):
    if case["valid"]:
        run_case(case)
    else:
        with pytest.raises(AttachmentError):
            run_case(case)


def test_fixture_table_is_the_pinned_contract():
    assert TABLE["contract"] == "fi.attachments.v1"
    assert len(TABLE["cases"]) == 46


def test_initial_reservation_is_source_plus_one_window():
    limits = recordings.Limits.from_wire(TABLE["base"]["limits"])
    assert imports.initial_reservation(172_800_044, limits) == 181_188_652


LIMITS = recordings.Limits(
    max_upload_bytes=8 * 1024 * 1024,
    segment_bytes=8 * 1024 * 1024,
    segment_duration_ms=30000,
    recording_bytes=1024**3,
    recording_duration_ms=7_200_000,
    reserved_bytes=1024**3,
)


def test_one_hour_plan_matches_contract_example():
    plans = recordings.plan_segments(
        frame_count=24000 * 3600, rate=24000, channels=1, limits=LIMITS
    )
    assert len(plans) == 120
    assert {plan.byte_length for plan in plans} == {1_440_044}
    assert {plan.duration_ms for plan in plans} == {30000}
    assert plans[-1].start_ms == 3_570_000


@pytest.mark.parametrize("rate", [8000, 11025, 16000, 22050, 44100, 48000, 96000])
@pytest.mark.parametrize("channels", [1, 2])
def test_plans_are_contiguous_and_bounded(rate, channels):
    frames = rate * 95 + 7
    plans = recordings.plan_segments(
        frame_count=frames, rate=rate, channels=channels, limits=LIMITS
    )
    end_frame, end_ms = 0, 0
    for plan in plans:
        assert plan.start_frame == end_frame and plan.start_ms == end_ms
        assert plan.byte_length <= LIMITS.window
        assert 1 <= plan.duration_ms <= LIMITS.segment_duration_ms
        end_frame += plan.frame_count
        end_ms += plan.duration_ms
    assert end_frame == frames
    assert end_ms == recordings.ceil_ms(frames, rate)


def test_byte_bound_wins_over_duration_bound():
    small = recordings.Limits(**{**LIMITS.__dict__, "segment_bytes": 1_000_044})
    plans = recordings.plan_segments(
        frame_count=48000 * 60, rate=48000, channels=2, limits=small
    )
    assert max(plan.byte_length for plan in plans) <= 1_000_044


def test_frame_that_cannot_fit_is_rejected_not_overrun():
    tiny = recordings.Limits(**{**LIMITS.__dict__, "segment_bytes": 45})
    with pytest.raises(AttachmentError, match="too_large"):
        recordings.plan_segments(frame_count=10, rate=8000, channels=2, limits=tiny)


def manifest(**changes):
    plans = recordings.plan_segments(
        frame_count=24000 * 61, rate=24000, channels=2, limits=LIMITS
    )
    value = recordings.build_manifest(
        recording_id=uuid.uuid4(),
        rate=24000,
        channels=2,
        direction="mixed",
        track_ids=[uuid.uuid4(), uuid.uuid4()],
        segments=[(uuid.uuid4(), plan) for plan in plans],
        duration_ms=61000,
        capture_complete=True,
    )
    value.update(changes)
    return value


def test_stereo_import_never_asserts_speaker_roles():
    value = manifest()
    assert value["layout"] == "stereo"
    assert [track["role"] for track in value["tracks"]] == ["unknown", "unknown"]
    assert [track["channels"] for track in value["tracks"]] == [[0], [1]]
    assert {track["direction"] for track in value["tracks"]} == {"mixed"}


def test_single_direction_mono_is_one_isolated_track():
    plans = recordings.plan_segments(
        frame_count=16000 * 40, rate=16000, channels=1, limits=LIMITS
    )
    value = recordings.build_manifest(
        recording_id=uuid.uuid4(),
        rate=16000,
        channels=1,
        direction="output",
        track_ids=[uuid.uuid4()],
        segments=[(uuid.uuid4(), plan) for plan in plans],
        duration_ms=40000,
        capture_complete=True,
    )
    assert value["layout"] == "separate_tracks"
    assert all("track_id" in segment for segment in value["segments"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m["segments"].pop(1),  # missing segment = unmarked gap
        lambda m: m["segments"].reverse(),  # out of order
        lambda m: m["segments"][1].update(
            occurrence_id=m["segments"][0]["occurrence_id"]
        ),  # duplicate
        lambda m: m["segments"][0].update(duration_ms=30001),  # over duration
        lambda m: (
            m.update(capture_complete=True)
            or m["gaps"].append(
                {"start_ms": 0, "duration_ms": 1, "reason": "truncated"}
            )
        ),
        lambda m: m["tracks"][1].update(channels=[0]),  # channel twice
        lambda m: m.update(duration_ms=m["duration_ms"] + 1),  # coverage short
        lambda m: m.update(extra=1),  # unknown member
        lambda m: m["tracks"][0].update(role="customer"),
    ],
)
def test_manifest_invariants_fail_closed(mutate):
    value = manifest()
    mutate(value)
    with pytest.raises(AttachmentError, match="validation_failed"):
        recordings.validate_manifest(value)


def test_truncated_recording_is_incomplete_with_explicit_gap():
    value = recordings.with_truncation(manifest(), captured_ms=61000, limit_ms=90000)
    assert value["capture_complete"] is False
    assert value["gaps"] == [
        {"start_ms": 61000, "duration_ms": 29000, "reason": "truncated"}
    ]


def test_manifest_size_bound():
    value = manifest()
    value["playout"] = [
        {"start_ms": 0, "duration_ms": 1, "evidence": "unknown"}
    ] * 60000
    with pytest.raises(AttachmentError):
        recordings.validate_manifest(value)


def test_strict_import_request_parsing():
    body = copy.deepcopy(TABLE["base"]["request"])
    recording, source = imports.parse_request(body)
    assert recording["trace_id"] == uuid.UUID("0123456789abcdef0123456789abcdef")
    assert source["kind"] == "ProviderFile"
    for broken in (
        {**body, "url": "https://example.test/a"},
        {**body, "source": {**body["source"], "headers": {"x": "y"}}},
        {
            **body,
            "source": {"kind": "RemoteUrlEnvelope", "envelope_id": str(uuid.uuid4())},
        },  # source_kind mismatch
        {**body, "recording": {**body["recording"], "declared_bytes": 0}},
    ):
        with pytest.raises(AttachmentError, match="validation_failed"):
            imports.parse_request(broken)


def test_every_internal_code_maps_to_an_f01_reason():
    schema_reasons = {
        "queued", "disabled", "bootstrap_pending", "policy_denied",
        "credential_scope_unsupported", "source_unavailable", "unsampled",
        "too_large", "queue_full", "unsupported_version", "endpoint_unconfigured",
        "scope_unresolved", "correlation_unavailable", "redaction_unavailable",
        "recording_disabled", "binding_pending", "not_found", "conflict",
        "throttled", "validation_failed", "expired", "deleted", "incomplete",
        "ready",
    }  # fmt: skip
    assert set(imports.PUBLIC_REASONS.values()) <= schema_reasons
    assert imports.public_reason("anything-new") == "validation_failed"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="seccomp")
def test_one_hour_wav_produces_120_valid_segments_in_the_sandbox(tmp_path):
    """The F01 example, run for real: 172,800,044 bytes -> 120 x 1,440,044."""
    source = tmp_path / "hour.wav"
    frames = 24000 * 3600
    with open(source, "wb") as output:
        output.write(sandbox_header(1, 24000, frames))
        block = struct.pack("<24000h", *range(-12000, 12000))
        for _ in range(3600):
            output.write(block)
    assert os.path.getsize(source) == 172_800_044
    facts = sandbox.run("source", source)
    assert facts["duration_ms"] == 3_600_000
    plans = recordings.plan_segments(
        frame_count=facts["frame_count"], rate=24000, channels=1, limits=LIMITS
    )
    sizes = []
    for plan in plans:
        part = tmp_path / "part.wav"
        with open(part, "wb") as output:
            result = sandbox.run(
                "segment",
                source,
                plan.start_frame,
                plan.frame_count,
                output=output,
                output_limit=plan.byte_length,
            )
        assert sandbox.probe(part)["frame_count"] == plan.frame_count
        sizes.append(result["byte_length"])
    assert sizes == [1_440_044] * 120


def sandbox_header(channels, rate, frames):
    from tracer.services.attachments.media_worker import wav_header

    return wav_header(channels=channels, rate=rate, frames=frames)


@pytest.mark.skipif(
    os.environ.get("FI_ATTACHMENTS_LONG_FIXTURE") != "1",
    reason="two-hour cloud fixture; set FI_ATTACHMENTS_LONG_FIXTURE=1",
)
def test_two_hour_stereo_fixture_derives_isolated_channels_and_peaks(tmp_path):
    """B11 qualification: 2 h, 24 kHz stereo, per-segment bounded work only.

    Left = +(frame % 1000), right = -(frame % 1000) - 1, so any swapped or
    mixed channel mapping is detected at every sampled position.
    """
    import time

    from tracer.services.attachments import derivatives

    rate, seconds = 24000, 7200
    frames = rate * seconds
    source = tmp_path / "two-hours.wav"
    # frame % 1000 repeats every second because the rate is a multiple of 1000.
    values = []
    for frame in range(rate):
        values += [frame % 1000, -(frame % 1000) - 1]
    block = struct.pack(f"<{len(values)}h", *values)
    with open(source, "wb") as output:
        output.write(sandbox_header(2, rate, frames))
        for _ in range(seconds):
            output.write(block)
    assert os.path.getsize(source) == 691_200_044
    started = time.monotonic()
    facts = sandbox.run("source", source)
    plans = recordings.plan_segments(
        frame_count=facts["frame_count"], rate=rate, channels=2, limits=LIMITS
    )
    assert len(plans) == 240 and facts["duration_ms"] == 7_200_000
    peak_bytes = 0
    for plan in plans:
        part = tmp_path / "part.wav"
        with open(part, "wb") as output:
            sandbox.run(
                "segment",
                source,
                plan.start_frame,
                plan.frame_count,
                output=output,
                output_limit=plan.byte_length,
            )
        for channel in (0, 1):
            mono = tmp_path / f"c{channel}.wav"
            with open(mono, "wb") as output:
                sandbox.run(
                    "split",
                    part,
                    channel,
                    output=output,
                    output_limit=44 + plan.frame_count * 2,
                )
            raw = mono.read_bytes()
            assert len(raw) == 44 + plan.frame_count * 2
            for offset in (0, plan.frame_count // 2, plan.frame_count - 1):
                (value,) = struct.unpack_from("<h", raw, 44 + offset * 2)
                frame = plan.start_frame + offset
                expected = frame % 1000 if channel == 0 else -(frame % 1000) - 1
                assert value == expected
        limit = derivatives.peaks_bytes(
            frame_count=plan.frame_count, rate=rate, channels=2
        )
        with open(tmp_path / "peaks", "wb") as output:
            sandbox.run("peaks", part, output=output, output_limit=limit)
        assert os.path.getsize(tmp_path / "peaks") == limit
        peak_bytes += limit
    elapsed = time.monotonic() - started
    print(f"two-hour fixture: 240 segments, peaks {peak_bytes} bytes, {elapsed:.1f}s")
    # Linear in duration with a fixed per-segment ceiling (~26 KiB per 30 s of
    # 24 kHz stereo); never a whole-recording decode.
    per_segment = derivatives.peaks_bytes(frame_count=rate * 30, rate=rate, channels=2)
    assert peak_bytes == 240 * per_segment == 6_393_600
