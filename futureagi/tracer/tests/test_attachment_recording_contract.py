"""Segment planning, manifests and F01 recording-import conformance."""

import copy
import io
import json
import os
import struct
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tracer.services.attachments import capacity, imports, recordings, sandbox
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


def valid_recording_request(request):
    try:
        return imports.parse_request(request)
    except AttachmentError as error:
        raise AssertionError("fixture failed before the gate under test") from error


def seed_headroom(v, importer, scenario, monkeypatch):
    """Use a second admitted job for nonempty global, org and project ledgers."""
    from django.db import transaction

    from tracer.services.attachments import capacity_db, provider_recordings
    from tracer.services.attachments.interfaces import ResourceVector
    from tracer.tests.test_attachment_simplify_combined_review import (
        make_envelope,
        make_provider,
    )

    other = copy.copy(v)
    other.envelope = make_envelope(v, v.envelope.source_bytes, v.envelope.duration_ms)
    provider = make_provider(other, uuid.uuid4())
    response, created = imports.submit(
        importer,
        scope_id=v.scope.id,
        body=provider_recordings._dispatch_body(provider, v.session),
    )
    assert created
    floor = capacity.whole_import_floor(
        source_bytes=v.envelope.source_bytes,
        output_scratch_bytes=imports.server_limits().window,
    )
    caps = tuple(
        floor + ResourceVector(**scenario["headroom"][level])
        for level in ("global", "organization", "project")
    )
    monkeypatch.setattr(capacity_db, "limits_for", lambda **_: caps)

    def counters():
        with transaction.atomic():
            locks = capacity_db.lock_capacity(
                scope_id=v.scope.id, pool_keys=(capacity.IMPORT_POOL_KEY,)
            )
            return [
                capacity_db._budget_vector(row)
                for row in capacity_db._rows(locks, capacity.IMPORT_POOL_KEY)
            ]

    assert counters() == [floor] * 3
    return response["job_id"], floor, counters


def replay_range_responses(scenario):
    """Feed recorded HTTP replies to the production pinned range reader.

    The recording's declared size determines requests, never the recorded reply.
    Only the HTTP connection is synthetic; no network endpoint is contacted.
    """
    from tracer.services.attachments import safe_fetch

    recording, _ = valid_recording_request(scenario["request"])
    total = recording["declared_bytes"]
    window = recordings.Limits.from_wire(scenario["limits"]).window
    source = safe_fetch.RangeSource(
        safe_fetch.Destination("media.example.test", 443, "/audio"),
        total,
        window,
        30,
        8 * 1024**2,
        generation='"fixture-v1"',
    )
    observed = scenario["observation"]
    revision = '"fixture-v1"' if observed["generation_matches"] else '"fixture-v2"'
    reads = iter(observed["reads"])
    sink = Mock(spec=["write"])
    # A mock's call history would retain the full hour of PCM in memory.
    sink.write = lambda chunk: len(chunk)
    consumed = 0
    while consumed < total:
        length = min(window, total - consumed)
        part = next(reads, None)
        conn = Mock()
        if part is None:
            reply = SimpleNamespace(status=416)
        else:
            start, size = part["offset"], part["bytes"]
            headers = {
                "ETag": revision,
                "Content-Length": str(size),
                "Content-Range": f"bytes {start}-{start + size - 1}/{part['total_bytes']}",
            }
            # An ignored range has no bounded body to consume. read_range must
            # reject it before reading; successful responses need at most W.
            reply = SimpleNamespace(
                status=200 if size > window else 206,
                getheader=headers.get,
                read=io.BytesIO(bytes(min(size, window))).read,
            )
        conn.getresponse.return_value = reply
        with patch.object(source, "_connection", return_value=conn):
            consumed += source.read_range(consumed, length, sink)
    assert next(reads, None) is None
    return consumed


def test_f01_range_transcript_reads_the_declared_source():
    assert (
        replay_range_responses(TABLE["base"])
        == TABLE["base"]["request"]["recording"]["declared_bytes"]
    )


def uses_db(case):
    if case["stage"] == "completion":
        return set(case["overrides"].get("observation", {})) not in (
            {"reads"},
            {"generation_matches"},
        )
    return True


@pytest.mark.parametrize(
    "case",
    [case for case in TABLE["cases"] if not uses_db(case)],
    ids=lambda case: case["id"],
)
def test_f01_recording_import_fixture(case):
    scenario = override(TABLE["base"], case["overrides"])
    assert not case["valid"]
    with pytest.raises(AttachmentError) as denied:
        replay_range_responses(scenario)
    assert (denied.value.code, denied.value.status) == ("validation_failed", 422)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "case",
    [case for case in TABLE["cases"] if uses_db(case)],
    ids=lambda case: case["id"],
)
def test_f01_database_import(case, project, user, settings, tmp_path, monkeypatch):
    from datetime import timedelta

    from django.utils import timezone

    from tracer.models.attachment_recording import AttachmentImportJob
    from tracer.services.attachments import storage
    from tracer.services.attachments.authority import LiveAuthority
    from tracer.tests.test_attachment_simplify_combined_review import (
        make_provider,
        make_values,
    )

    scenario = override(TABLE["base"], case["overrides"])
    body = scenario["request"]
    descriptor = body["recording"]
    v = make_values(
        project,
        user,
        settings,
        tmp_path,
        source_bytes=descriptor["declared_bytes"],
        duration_ms=descriptor["declared_duration_ms"],
    )
    complete = scenario["source"]["terminal_completed"]
    if "source" in case["overrides"] and complete:
        # Completion must observe a pinned generation before submit can use a
        # ProviderFile. Exercise the real metadata reader over fixture HTTP.
        from tracer.services.attachments import provider_recordings, safe_fetch

        headers = {
            "Content-Type": "audio/wav",
            "Content-Length": str(v.envelope.source_bytes),
            "Accept-Ranges": "bytes"
            if scenario["source"]["bounded_ranges"]
            else "none",
        }
        if scenario["source"]["immutable"]:
            headers["ETag"] = v.envelope.generation
        transport = Mock()
        transport.getresponse.return_value = SimpleNamespace(
            status=200, getheader=headers.get
        )
        monkeypatch.setattr(safe_fetch.RangeSource, "_connection", lambda _: transport)
        info = {
            "fileResults": [
                {
                    "location": "https://storage.invalid/combined.wav",
                    "size": v.envelope.source_bytes,
                    "duration": v.envelope.duration_ms * 1000000,
                }
            ]
        }
        try:
            complete = provider_recordings._completion_metadata(None, info) is not None
        except AttachmentError as error:
            assert error.code == "source_unavailable"
            complete = False
    provider = make_provider(
        v,
        uuid.UUID(descriptor["recording_id"]),
        complete=complete,
    )
    # Only substitute the identities allocated by the real database fixture.
    body["source"].update(
        connection_id=str(provider.connection_id),
        registered_output_id=str(provider.registered_output_id),
    )
    descriptor.update(
        policy_version=str(v.scope.policy_version),
        association={
            "capture_session_id": str(v.session.client_id),
            "correlation_nonce": str(v.session.correlation_nonce),
            "parent": {"trace_id": v.trace.hex, "span_id": v.span},
        },
    )
    settings.FI_ATTACHMENTS_RECORDING_LIMITS = scenario["limits"]
    monkeypatch.setattr(storage, "bind_for_admission", lambda **_: v.binding.id)
    monkeypatch.setattr(
        LiveAuthority,
        "source",
        lambda *_: SimpleNamespace(
            readable=True, expires_at=timezone.now() + timedelta(hours=2)
        ),
    )
    importer = imports.Importer("user", user.id)
    jobs = AttachmentImportJob.objects.filter(scope=v.scope)
    has_budget = "headroom" in case["overrides"]
    if has_budget:
        prior_id, floor, counters = seed_headroom(v, importer, scenario, monkeypatch)
        jobs = jobs.exclude(pk=prior_id)
    if case["stage"] == "admission" and not case["valid"]:
        with pytest.raises(AttachmentError) as denial:
            imports.submit(importer, scope_id=v.scope.id, body=body)
        assert not jobs.exists()
        if has_budget:
            assert (denial.value.code, denial.value.status) == ("throttled", 429)
            assert counters() == [floor] * 3
        else:
            expected = (
                ("source_unavailable", 403)
                if "source" in case["overrides"]
                else ("too_large", 413)
            )
            assert (denial.value.code, denial.value.status) == expected
        return
    if descriptor["direction"] != provider.direction:
        with pytest.raises(AttachmentError) as denial:
            imports.submit(importer, scope_id=v.scope.id, body=body)
        assert not case["valid"] and not jobs.exists()
        assert (denial.value.code, denial.value.status) == ("policy_denied", 403)
        return
    response, created = imports.submit(importer, scope_id=v.scope.id, body=body)
    assert created
    job = jobs.get(pk=response["job_id"])
    assert job.recording.client_id == provider.recording_client_id
    assert (job.source_byte_length, job.source_duration_ms) == (
        descriptor["declared_bytes"],
        descriptor["declared_duration_ms"],
    )
    if has_budget:
        from tracer.services.attachments import capacity_db

        assert capacity_db.owner_vector(job) == floor
        assert counters() == [floor + floor] * 3
    if case["stage"] == "completion":
        if case["valid"]:
            complete_vector(v, job, scenario, monkeypatch)
            job.refresh_from_db()
            assert job.state == "ready" and job.recording.manifest_revision == 1
        else:
            with pytest.raises(AttachmentError) as denial:
                complete_vector(v, job, scenario, monkeypatch)
            job.refresh_from_db()
            assert job.state != "ready" and job.recording.manifest_revision == 0
            # Exact refusals from the pre-consolidation F01 oracle, not a
            # list of whichever errors today's worker happens to produce.
            expected = (
                ("too_large", 413)
                if case["id"] == "segment-total-over-recording-budget"
                else ("validation_failed", 422)
            )
            assert (denial.value.code, denial.value.status) == expected


def complete_vector(v, job, scenario, monkeypatch):
    """Replay remote HEAD, decoder and object-store observations, not verdicts."""
    from django.utils import timezone

    from tracer.services.attachments import safe_fetch
    from tracer.services.attachments.interfaces import (
        ObjectIdentity,
        StoredObject,
        VerifiedAssetFacts,
    )
    from tracer.tests.test_attachment_simplify_combined_review import claim

    observed = scenario["observation"]
    fence = claim(v, job)
    monkeypatch.setattr(
        safe_fetch,
        "inspect_metadata",
        lambda _: (
            job.source_generation,
            observed["source_bytes"],
            job.source_mime_type,
        ),
    )
    monkeypatch.setattr(
        safe_fetch.RangeSource,
        "read_range",
        lambda self, start, length, sink: sink.write(bytes(length)),
    )
    imports._remote_recheck(job, fence)
    manifest = observed["manifest"]
    rate, channels = manifest["sample_rate_hz"], manifest["channels"]
    frames = (job.source_byte_length - 44) // (2 * channels)
    limits = recordings.Limits.from_wire(scenario["limits"])
    plans = recordings.plan_segments(
        frame_count=frames, rate=rate, channels=channels, limits=limits
    )
    # The worker compares the decoder's observed duration and the aggregate
    # planned size before opening its PCM stream. Replay those two preflights.
    if (
        observed["source_duration_ms"] != job.source_duration_ms
        or sum(plan.byte_length for plan in plans) > job.recording_bytes
    ):
        monkeypatch.setattr(
            sandbox,
            "run",
            lambda *_: {
                "duration_ms": observed["source_duration_ms"],
                "mime_type": job.source_mime_type,
                "frame_count": frames,
                "sample_rate": rate,
                "channels": channels,
            },
        )
        with sandbox.import_execution(job.id, fence.epoch) as directory:
            imports._segment_source(
                job, fence, SimpleNamespace(name="synthetic-source"), directory, None
            )
        pytest.fail("worker preflight did not reject the observation")
    target = job.recording
    target.sample_rate, target.channels = rate, channels
    target.save(update_fields=["sample_rate", "channels"])
    rows = []
    # Wire occurrence IDs are opaque: translate the baseline IDs to the ones
    # allocated by this job without repairing missing or malformed entries.
    occurrence_ids = {}
    baseline = TABLE["base"]["observation"]["manifest"]["segments"]
    sizes = observed["segment_bytes"]
    with sandbox.import_execution(job.id, fence.epoch):
        for plan in plans:
            row = imports._allocate(job, fence, plan, "a" * 64)
            permit = imports.prepare_put(
                job_id=job.id,
                allocation_id=row.id,
                allocation_kind="segment",
                fence=fence,
                purpose="serving",
                now=timezone.now(),
            )
            identity = ObjectIdentity(
                v.binding.id, "serving", row.storage_key, "fixture-v1"
            )
            facts = (
                None
                if plan.index >= len(sizes)
                else VerifiedAssetFacts(
                    StoredObject(identity, sizes[plan.index], row.sha256),
                    "audio/wav",
                    timezone.now(),
                    rate,
                    channels,
                    plan.frame_count,
                )
            )
            assert (
                imports.record_put(
                    job_id=job.id,
                    allocation_id=row.id,
                    allocation_kind="segment",
                    permit=permit,
                    identity=identity,
                    facts=facts,
                )
                == "recorded"
            )
            rows.append(row.id)
            if plan.index < len(baseline):
                occurrence_ids[baseline[plan.index]["occurrence_id"]] = str(
                    row.occurrence_client_id
                )
    for segment in manifest["segments"]:
        original = segment["occurrence_id"]
        segment["occurrence_id"] = occurrence_ids.get(original, original)
    imports.publish(job_id=job.id, fence=fence, segment_ids=rows, manifest=manifest)


def test_fixture_table_is_the_pinned_contract():
    assert TABLE["contract"] == "fi.attachments.v1"
    assert len(TABLE["cases"]) == 46


def test_initial_reservation_is_source_plus_one_window():
    limits = recordings.Limits.from_wire(TABLE["base"]["limits"])
    assert (
        capacity.whole_import_floor(
            source_bytes=172_800_044, output_scratch_bytes=limits.window
        ).bytes
        == 181_188_652
    )


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
    # The shared wire validator preserves the descriptor; it no longer
    # manufactures a second flattened association with different field types.
    assert recording["association"]["parent"]["trace_id"] == (
        "0123456789abcdef0123456789abcdef"
    )
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
        with pytest.raises(AttachmentError, match="^invalid_request$") as error:
            imports.parse_request(broken)
        assert error.value.status == 400


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
