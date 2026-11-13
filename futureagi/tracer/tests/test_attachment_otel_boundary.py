"""Exercise the actual legacy-media and trace/Voice response boundaries."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tracer.services.attachments.binding import Parent, Scope
from tracer.services.attachments.reads import AuthorizedStatus, ReadTarget
from tracer.services.attachments.references import NONCE, REFS, SCHEMA, SESSION
from tracer.services.attachments.view_enrichment import (
    enrich_observations,
    enrich_trace_detail,
    enrich_voice_detail,
    enrich_voice_page,
    trace_observations,
)
from tracer.utils.otel import _convert_single_span, process_attributes

A = "12345678-1234-1234-1234-123456789abc"
B = "87654321-4321-4321-4321-cba987654321"
C = "11111111-1111-1111-1111-111111111111"
REF = {
    "attachment_id": A,
    "occurrence_id": B,
    "direction": "mixed",
    "modality": "audio",
    "mime_type": "audio/wav",
}
MEDIA_KEY = "gen_ai.input.messages.0.message_content.audio"


def test_new_refs_cannot_enter_legacy_upload_or_network():
    with (
        patch(
            "tracer.utils.otel.upload_content_to_s3",
            side_effect=AssertionError("legacy upload called"),
        ) as upload,
        patch(
            "socket.socket.connect", side_effect=AssertionError("media network called")
        ),
    ):
        for content in (
            "fi-attachment:" + B,
            "fi-attachment:invalid",
            {"type": "uri", "uri": "fi-attachment:" + B},
            json.dumps({"type": "uri", "uri": "fi-attachment:" + B}),
        ):
            attributes = {
                SCHEMA: 1,
                REFS: json.dumps([REF]),
                SESSION: A,
                NONCE: B,
                MEDIA_KEY: content,
            }
            result = process_attributes(attributes, org_id=A)
            assert json.loads(result[REFS]) == [REF]
            assert result[MEDIA_KEY] == content
            assert SESSION not in result and NONCE not in result
        upload.assert_not_called()


def test_legacy_audio_still_uses_existing_managed_upload_contract():
    with patch(
        "tracer.utils.otel.upload_content_to_s3",
        return_value="https://example.invalid/managed.wav",
    ) as upload:
        assert process_attributes({MEDIA_KEY: "synthetic-legacy-bytes"}, org_id=A) == {
            MEDIA_KEY: "https://example.invalid/managed.wav"
        }
        upload.assert_called_once_with("synthetic-legacy-bytes", "audio", org_id=A)


def test_invalid_reference_secrets_are_not_retained():
    with patch("tracer.utils.otel.upload_content_to_s3") as upload:
        result = process_attributes(
            {
                SCHEMA: 1,
                REFS: json.dumps([dict(REF, source_url="SYNTHETIC_CANARY")]),
                "normal": "retained",
            },
            org_id=A,
        )
        assert result == {"normal": "retained"}
        upload.assert_not_called()


def test_real_trace_detail_and_voice_use_identical_authorized_projection():
    from tracer.models.project import Project
    from tracer.services.clickhouse.v2.query_builders.trace_detail import (
        retrieve_trace_detail_ch,
    )

    request = SimpleNamespace(
        organization=SimpleNamespace(id=A), workspace=SimpleNamespace(id=B)
    )
    target = ReadTarget(Scope(A, B, C), Parent(A, "1234567890abcdef"))
    # Storage doubles feed the REAL CH response assembler. Its wire shape must
    # not be recreated by a fixture that silently invents a trace_id field.
    storage_row = {
        "id": target.parent.span_id,
        "trace_id": A,
        "project_id": C,
        "name": "conversation",
        "observation_type": "conversation",
        "parent_span_id": None,
        "start_time": datetime.now(UTC),
        "end_time": datetime.now(UTC),
        "status": "OK",
        "provider": "pipecat",
    }
    detail = SimpleNamespace(
        project_id=C,
        spans=[storage_row],
        eval_config_ids=(),
        evals=(),
        annotations=(),
        eval_unavailable=False,
    )
    manager = getattr(Project, "no_workspace_objects", Project.objects)
    with (
        patch.object(manager, "filter") as projects,
        patch("tracer.models.trace.Trace.objects.filter") as traces,
        patch(
            "tracer.services.clickhouse.v2.trace_detail_reads.read_trace_detail",
            return_value=detail,
        ),
    ):
        projects.return_value.filter.return_value.values_list.return_value = [C]
        traces.return_value.first.return_value = None
        result = retrieve_trace_detail_ch(Mock(), request, A, Mock(), project_id=C)
    observation = result["observation_spans"][0]["observation_span"]
    assert observation["trace"] == A and "trace_id" not in observation
    reader = Mock()
    reader.list_authorized.return_value = [
        AuthorizedStatus(
            target, REF, "ready", True, True, datetime.now(UTC) + timedelta(hours=1)
        )
    ]
    enrich_trace_detail(request=request, result=result, reader=reader)
    reader.list_authorized.assert_called_once_with(
        request, (target,), per_parent_limit=64
    )
    assert "trace_id" not in observation  # Do not change the public row schema.
    voice = [{"id": target.parent.span_id, "trace_id": A}]
    enrich_observations(
        request=request, project_id=C, observations=voice, reader=reader
    )
    assert observation["attachments"] == voice[0]["attachments"]
    assert voice[0]["attachments"][0]["status"] == "ready"
    assert reader.list_authorized.call_count == 2
    reader.list_authorized.return_value = []
    observation.pop("attachments")
    enrich_trace_detail(request=request, result=result, reader=reader)
    assert "attachments" not in observation


def test_trace_parent_is_not_guessed_from_voice_shape():
    request = SimpleNamespace(
        organization=SimpleNamespace(id=A), workspace=SimpleNamespace(id=B)
    )
    reader = Mock()
    # Standard rows require their own canonical trace parent; a missing or
    # invalid trace must not fall back to a different trace_id or the envelope.
    for row in (
        {"id": "1234567890abcdef", "trace_id": A},
        {"id": "1234567890abcdef", "trace": "invalid", "trace_id": A},
        {"id": "invalid", "trace": A},
    ):
        result = {
            "trace": {"project": C, "id": A},
            "observation_spans": [{"observation_span": row}],
        }
        enrich_trace_detail(request=request, result=result, reader=reader)
        assert "attachments" not in row
    reader.list_authorized.assert_not_called()


def test_missing_workspace_and_default_off_do_not_query():
    reader = Mock()
    row = {"id": "1234567890abcdef", "trace_id": A}
    request = SimpleNamespace(organization=SimpleNamespace(id=A))
    enrich_observations(
        request=request, project_id=C, observations=[row], reader=reader
    )
    enrich_observations(request=request, project_id=C, observations=[row])
    reader.list_authorized.assert_not_called()
    assert "attachments" not in row


def test_voice_list_enrichment_batches_roots_and_omits_denied_counts():
    request = SimpleNamespace(
        organization=SimpleNamespace(id=A), workspace=SimpleNamespace(id=B)
    )
    target = ReadTarget(Scope(A, B, C), Parent(A, "1234567890abcdef"))
    entries = [
        {
            "trace_id": A,
            "project_id": C,
            "observation_span": [{"id": target.parent.span_id}],
        }
        for _ in range(3)
    ]
    reader = Mock()
    reader.list_authorized.return_value = [
        AuthorizedStatus(
            target,
            REF,
            "validating",
            True,
            False,
            datetime.now(UTC) + timedelta(hours=1),
        )
    ]
    enrich_voice_page(request=request, project_id=C, entries=entries, reader=reader)
    reader.list_authorized.assert_called_once()
    assert all(
        entry["observation_span"][0]["attachments"][0]["status"] == "validating"
        for entry in entries
    )
    assert all("trace_id" not in entry["observation_span"][0] for entry in entries)


def test_explicit_framework_roots_preserve_real_provider_and_parent():
    project = SimpleNamespace(id=C)
    for provider in ("pipecat", "livekit"):
        for kind, parent in (("CONVERSATION", None), ("CHAIN", "fedcba9876543210")):
            span = {
                "span_id": "1234567890abcdef",
                "trace_id": A,
                "parent_id": parent,
                "project_name": "voice",
                "project_type": "observe",
                "attributes": {"fi.span.kind": kind, "gen_ai.provider.name": provider},
            }
            with patch("tracer.utils.otel.calculate_cost", return_value=0):
                observation = _convert_single_span(
                    span, {("voice", A, "observe"): project}, {}, A
                )["observation_span"]
            assert observation["observation_type"] == kind.lower()
            assert observation["provider"] == provider
            assert observation["parent_span_id"] == parent


def test_trace_walk_and_media_batch_are_bounded():
    nodes = [
        {"observation_span": {"id": "1234567890abcdef", "trace_id": A}}
        for _ in range(101)
    ]
    assert len(list(trace_observations({"observation_spans": nodes}))) == 100


ROOT = "1234567890abcdef"
CHILD = "fedcba9876543210"
RECORDING = {**REF, "purpose": "conversation_recording"}


def _voice_request():
    return SimpleNamespace(
        organization=SimpleNamespace(id=A), workspace=SimpleNamespace(id=B)
    )


def _status(span_id, reference, state="ready", bound=True, complete=True):
    return AuthorizedStatus(
        ReadTarget(Scope(A, B, C), Parent(A, span_id)),
        reference,
        state,
        bound,
        complete,
        datetime.now(UTC) + timedelta(hours=1),
    )


def _real_voice_detail(reader):
    """Drive the REAL ClickHouse Voice detail builder with storage doubles."""
    from tracer.views.trace import TraceView

    storage = [
        {
            "id": ROOT,
            "trace_id": A,
            "project_id": C,
            "name": "conversation",
            "observation_type": "conversation",
            "parent_span_id": None,
            "start_time": datetime.now(UTC),
            "end_time": datetime.now(UTC),
            "status": "OK",
            "provider": "pipecat",
            "attrs_string": {},
        },
        {
            "id": CHILD,
            "trace_id": A,
            "project_id": C,
            "name": "llm",
            "observation_type": "llm",
            "parent_span_id": ROOT,
            "start_time": datetime.now(UTC),
            "end_time": datetime.now(UTC),
            "status": "OK",
            "provider": "openai",
        },
    ]
    detail = SimpleNamespace(
        project_id=C,
        spans=storage,
        eval_config_ids=(),
        evals=(),
        annotations=(),
        eval_unavailable=False,
    )
    view = TraceView()
    view.attachment_reader = reader
    with (
        patch("tracer.views.trace._simulation_context_for_voice_call", return_value={}),
        patch.object(view._gm, "success_response", side_effect=lambda value: value),
    ):
        return view._voice_call_detail_clickhouse(_voice_request(), A, detail, [])


def test_real_voice_detail_projects_coarse_authorized_recording():
    reader = Mock()
    reader.list_authorized.return_value = [_status(ROOT, RECORDING)]
    # The per-occurrence re-authorization denies (deny wins over the batch).
    reader.standalone_recording.return_value = None
    result = _real_voice_detail(reader)
    reader.list_authorized.assert_called_once()
    assert result["project_id"] == C
    # Coarse I04 shape only: no manifest/origin/URL/grant unless the occurrence
    # is re-authorized and its validated facts describe a playable file.
    assert result["recording_attachment"] == {
        "occurrence_id": B,
        "parent": {"trace_id": A, "span_id": ROOT},
        "state": "pending",
    }
    assert "recording_attachment" not in result["observation_span"][0]


def test_voice_detail_recording_states_are_coarse():
    for state, bound, complete, expected in (
        ("ready", True, False, "waiting"),
        ("ready", False, True, "pending"),
        ("validating", True, False, "validating"),
        ("importing", True, False, "importing"),
        ("failed", True, True, "failed"),
    ):
        reader = Mock()
        reader.list_authorized.return_value = [
            _status(ROOT, RECORDING, state, bound, complete)
        ]
        result = _real_voice_detail(reader)
        assert result["recording_attachment"]["state"] == expected, state


def test_voice_detail_without_authorized_recording_keeps_legacy_tier():
    for statuses in (
        [],  # denied, foreign, missing and deleted rows are omitted identically
        [_status(ROOT, REF)],  # an attachment that is not a recording
        [_status(CHILD, REF)],
        [_status(ROOT, RECORDING, "deleted")],
        [_status(ROOT, RECORDING, "expired")],
        [_status(ROOT, RECORDING, "rejected")],
    ):
        reader = Mock()
        reader.list_authorized.return_value = statuses
        result = _real_voice_detail(reader)
        assert "recording_attachment" not in result
        assert "recording" in result  # the legacy URL tier is untouched
    result = _real_voice_detail(None)
    assert "recording_attachment" not in result
    assert all("attachments" not in row for row in result["observation_span"])


def test_voice_detail_projects_a_recording_bound_on_a_child_span():
    """LiveKit binds the session recording on agent_session, a child span.

    The projection names the occurrence's own parent; the manifest is read
    for that same parent, never for the root.
    """
    reader = Mock()
    reader.list_authorized.return_value = [
        replace(
            _status(CHILD, RECORDING),
            recording_id=RECORDING_ID,
        )
    ]
    reader.recording_manifest.return_value = _manifest()
    result = _real_voice_detail(reader)
    assert result["recording_attachment"] == {
        "occurrence_id": B,
        "parent": {"trace_id": A, "span_id": CHILD},
        "state": "ready",
        "manifest": _manifest(),
    }
    _, target, recording_id = reader.recording_manifest.call_args.args
    assert (target.parent, recording_id) == (Parent(A, CHILD), RECORDING_ID)
    # Coarse states project the same child parent.
    reader.list_authorized.return_value = [_status(CHILD, RECORDING, "validating")]
    assert _real_voice_detail(reader)["recording_attachment"]["parent"] == {
        "trace_id": A,
        "span_id": CHILD,
    }


def test_voice_detail_recordings_on_root_and_child_are_not_guessed():
    reader = Mock()
    reader.list_authorized.return_value = [
        _status(ROOT, RECORDING),
        _status(CHILD, {**RECORDING, "occurrence_id": C}),
    ]
    assert _real_voice_detail(reader)["recording_attachment"] == {"state": "rejected"}


def test_voice_detail_ambiguous_root_recordings_are_not_guessed():
    other = {**RECORDING, "occurrence_id": C}
    reader = Mock()
    reader.list_authorized.return_value = [
        _status(ROOT, RECORDING),
        _status(ROOT, other),
    ]
    result = _real_voice_detail(reader)
    assert result["recording_attachment"] == {"state": "rejected"}


def test_voice_detail_projection_requires_workspace_scope():
    reader = Mock()
    result = {
        "project_id": C,
        "observation_span": [{"id": ROOT, "trace_id": A}],
    }
    enrich_voice_detail(
        request=SimpleNamespace(organization=SimpleNamespace(id=A)),
        result=result,
        reader=reader,
    )
    reader.list_authorized.assert_not_called()
    assert "recording_attachment" not in result


RECORDING_ID = "22222222-2222-2222-2222-222222222222"
TRACK = "33333333-3333-3333-3333-333333333333"


def _segment_status(occurrence_id, recording_id=RECORDING_ID, state="ready"):
    return replace(
        _status(ROOT, {**RECORDING, "occurrence_id": occurrence_id}, state),
        recording_id=recording_id,
    )


def _manifest(segments=None, layout="mixed", complete=True, gaps=()):
    return {
        "schema_version": 1,
        "recording_id": RECORDING_ID,
        "timebase": "session_relative_ms",
        "sample_format": "s16le",
        "sample_rate_hz": 16000,
        "channels": 1,
        "codec": "pcm",
        "container": "wav",
        "layout": layout,
        "tracks": [
            {"track_id": TRACK, "channels": [0], "direction": "mixed", "role": "mixed"}
        ],
        "segments": (
            segments
            if segments is not None
            else [{"occurrence_id": B, "index": 0, "start_ms": 0, "duration_ms": 1000}]
        ),
        "gaps": list(gaps),
        "playout": [],
        "capture_complete": complete,
        "duration_ms": 1000,
    }


def test_voice_detail_projects_ready_manifest_for_one_mixed_occurrence():
    reader = Mock()
    reader.list_authorized.return_value = [_segment_status(B)]
    reader.recording_manifest.return_value = _manifest()
    result = _real_voice_detail(reader)
    assert result["recording_attachment"] == {
        "occurrence_id": B,
        "parent": {"trace_id": A, "span_id": ROOT},
        "state": "ready",
        "manifest": _manifest(),
    }
    viewer, target, recording_id = reader.recording_manifest.call_args.args
    assert (target.scope, target.parent, recording_id) == (
        Scope(A, B, C),
        Parent(A, ROOT),
        RECORDING_ID,
    )


def test_voice_detail_unplayable_manifests_stay_coarse():
    half = {"occurrence_id": B, "index": 0, "start_ms": 0, "duration_ms": 500}
    gap = {"start_ms": 500, "duration_ms": 500, "reason": "incomplete"}
    for manifest in (
        None,  # denied or not yet written
        _manifest(layout="stereo"),  # needs channel derivatives (B11)
        _manifest(complete=False),
        _manifest(segments=[half], complete=False, gaps=[gap]),
        _manifest(segments=[dict(half, occurrence_id=C)]),  # another occurrence
    ):
        reader = Mock()
        reader.list_authorized.return_value = [_segment_status(B)]
        reader.recording_manifest.return_value = manifest
        result = _real_voice_detail(reader)
        assert result["recording_attachment"]["state"] == "pending", manifest
        assert "manifest" not in result["recording_attachment"]


def test_voice_detail_segments_of_one_recording_are_one_recording():
    reader = Mock()
    reader.list_authorized.return_value = [
        _segment_status(B),
        _segment_status(C, state="validating"),
    ]
    result = _real_voice_detail(reader)
    # Multi-segment recordings need server derivatives, so never ready here.
    assert result["recording_attachment"]["state"] == "validating"
    reader.recording_manifest.assert_not_called()
    reader.list_authorized.return_value = [
        _segment_status(B),
        _segment_status(C, recording_id=A),
    ]
    assert _real_voice_detail(reader)["recording_attachment"] == {"state": "rejected"}


def _livekit_root(name="job_entrypoint", parent=None, **attributes):
    span = {
        "span_id": ROOT,
        "trace_id": A,
        "parent_id": parent,
        "name": name,
        "project_name": "voice",
        "project_type": "observe",
        "attributes": {"gen_ai.span.kind": "UNKNOWN", **attributes},
    }
    with patch("tracer.utils.otel.calculate_cost", return_value=0):
        return _convert_single_span(
            span, {("voice", A, "observe"): SimpleNamespace(id=C)}, {}, A
        )["observation_span"]["observation_type"]


WAV_FACTS = {
    "mime_type": "audio/wav",
    "sample_rate": 16000,
    "channels": 1,
    "frame_count": 16000,
}


def test_voice_detail_plays_a_ready_standalone_recording_of_any_audio_type():
    """No recording_id (LiveKit/Pipecat ``single`` tier): one synthesized mixed
    manifest over the validated blob facts, after a per-occurrence re-check."""
    from tracer.services.attachments import wire

    for mime, codec, container, rate, channels in (
        ("audio/wav", "pcm", "wav", 16000, 1),
        ("audio/ogg", "opus", "ogg", 48000, 2),
        ("audio/mpeg", "mp3", "mp3", 44100, 2),
        ("audio/mp4", "aac", "mp4", 44100, 1),
        ("audio/webm", "opus", "webm", 48000, 1),
        ("audio/flac", "flac", "flac", 96000, 2),
    ):
        reader = Mock()
        reference = {**RECORDING, "mime_type": mime}
        reader.list_authorized.return_value = [_status(ROOT, reference)]
        reader.standalone_recording.return_value = {
            "mime_type": mime,
            "sample_rate": rate,
            "channels": channels,
            "frame_count": rate * 12,  # 12 s
        }
        projection = _real_voice_detail(reader)["recording_attachment"]
        assert projection["state"] == "ready", mime
        manifest = projection["manifest"]
        assert (manifest["codec"], manifest["container"]) == (codec, container)
        assert manifest["sample_format"] == "encoded"
        assert (manifest["sample_rate_hz"], manifest["channels"]) == (rate, channels)
        assert manifest["duration_ms"] == 12000 and manifest["recording_id"] == B
        assert manifest["segments"] == [
            {"occurrence_id": B, "index": 0, "start_ms": 0, "duration_ms": 12000}
        ]
        (track,) = manifest["tracks"]
        assert track["channels"] == list(range(channels))
        assert (track["direction"], track["role"]) == ("mixed", "mixed")
        # Up to 30 s it is a complete, valid F01 RecordingManifest.
        assert wire.is_valid("RecordingManifest", manifest), mime
        _, target, occurrence_id = reader.standalone_recording.call_args.args
        assert (target.scope, target.parent, occurrence_id) == (
            Scope(A, B, C),
            Parent(A, ROOT),
            B,
        )
        reader.recording_manifest.assert_not_called()


def test_long_standalone_recording_tiles_thirty_second_segments():
    """F01 caps a segment at 30 s, so a long file is tiled by one occurrence."""
    from tracer.services.attachments import wire
    from tracer.services.attachments.view_enrichment import standalone_manifest

    manifest = standalone_manifest(B, {**WAV_FACTS, "frame_count": 16000 * 75})
    assert manifest["duration_ms"] == 75000
    assert [
        (segment["index"], segment["start_ms"], segment["duration_ms"])
        for segment in manifest["segments"]
    ] == [(0, 0, 30000), (1, 30000, 30000), (2, 60000, 15000)]
    assert {segment["occurrence_id"] for segment in manifest["segments"]} == {B}
    # Schema-valid; only the stored-manifest distinct-occurrence rule differs.
    assert wire._validator("RecordingManifest").is_valid(manifest)
    reader = Mock()
    reader.list_authorized.return_value = [_status(ROOT, RECORDING)]
    reader.standalone_recording.return_value = {**WAV_FACTS, "frame_count": 16000 * 75}
    projection = _real_voice_detail(reader)["recording_attachment"]
    assert projection["state"] == "ready" and projection["manifest"] == manifest


def test_standalone_recording_stays_coarse_unless_ready_authorized_and_valid():
    for status, facts in (
        (_status(ROOT, RECORDING), None),  # re-authorization denied
        (_status(ROOT, RECORDING, complete=False), WAV_FACTS),  # partial
        (_status(ROOT, RECORDING, "validating", complete=False), WAV_FACTS),
        (_status(ROOT, RECORDING, bound=False), WAV_FACTS),  # not bound yet
        (_status(ROOT, RECORDING), {**WAV_FACTS, "mime_type": "audio/L16"}),
        (_status(ROOT, RECORDING), {**WAV_FACTS, "sample_rate": 4000}),
        (_status(ROOT, RECORDING), {**WAV_FACTS, "channels": 0}),
        (_status(ROOT, RECORDING), {**WAV_FACTS, "frame_count": 0}),
        (_status(ROOT, RECORDING), {**WAV_FACTS, "frame_count": 16000 * 7201}),
        (_status(ROOT, RECORDING), {**WAV_FACTS, "sample_rate": "16000"}),
    ):
        reader = Mock()
        reader.list_authorized.return_value = [status]
        reader.standalone_recording.return_value = facts
        projection = _real_voice_detail(reader)["recording_attachment"]
        assert projection["state"] != "ready", (status, facts)
        assert "manifest" not in projection
    # Two standalone recordings in one call are never guessed between.
    reader = Mock()
    reader.list_authorized.return_value = [
        _status(ROOT, RECORDING),
        _status(CHILD, {**RECORDING, "occurrence_id": C}),
    ]
    reader.standalone_recording.return_value = WAV_FACTS
    assert _real_voice_detail(reader)["recording_attachment"] == {"state": "rejected"}
    reader.standalone_recording.assert_not_called()


def test_livekit_job_root_is_a_voice_call_only_with_attachments():
    """The SDK marks no conversation root for a LiveKit worker (I04)."""
    from django.test import override_settings

    job = {"lk.job_id": "AJ_synthetic"}
    with override_settings(FI_ATTACHMENTS_ENABLED=True):
        assert _livekit_root(**job) == "conversation"
        assert _livekit_root(parent=ROOT, **job) == "unknown"  # not a true root
        # README Quick Start: tracing starts inside the entrypoint, so the
        # untyped, parentless agent_session IS the call root.
        assert _livekit_root(name="agent_session", **job) == "conversation"
        assert _livekit_root(name="agent_session", parent=ROOT, **job) == "unknown"
        assert _livekit_root(name="agent_session") == "unknown"  # no lk.job_id
        assert _livekit_root(name="llm_request", **job) == "unknown"
        assert _livekit_root() == "unknown"  # no lk.job_id
        typed = {**job, "fi.span.kind": "AGENT", "gen_ai.span.kind": "AGENT"}
        assert _livekit_root(**typed) == "agent"  # only an untyped root maps
    with override_settings(FI_ATTACHMENTS_ENABLED=False):
        assert _livekit_root(**job) == "unknown"  # disabled: unchanged
        assert _livekit_root(name="agent_session", **job) == "unknown"
