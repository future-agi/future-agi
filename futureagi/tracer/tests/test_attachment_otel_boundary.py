"""Exercise the actual legacy-media and trace/Voice response boundaries."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tracer.services.attachments.binding import Parent, Scope
from tracer.services.attachments.reads import AuthorizedStatus, ReadTarget
from tracer.services.attachments.references import NONCE, REFS, SCHEMA, SESSION
from tracer.services.attachments.view_enrichment import (
    enrich_observations,
    enrich_trace_detail,
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
