"""Voice projection through real upload, validation, manifest and binding services.

PostgreSQL components with the existing explicit ClickHouse/session, immutable
storage and in-process probe doubles. Not native storage or browser evidence.
"""

import io
import uuid
import wave
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from tracer.models.attachment import AttachmentOccurrence, AttachmentState
from tracer.services.attachments import (
    control_plane,
    manifests,
    uploads,
    validation,
    wire,
)
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.probe import probe
from tracer.services.attachments.view_enrichment import enrich_voice_detail
from tracer.tests import test_attachment_control_plane as components
from tracer.tests import test_attachment_core as core

setup = components.setup
unlimited_retention = components.unlimited_retention
pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _segment_bytes(duration_ms):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x01" * (16 * duration_ms))
    return stream.getvalue()


def _recording(setup, user, monkeypatch, durations, *, reject_last=False):
    scope, _ = setup
    scope.record_audio, scope.policy_version = True, scope.policy_version + 1
    scope.save(update_fields=["record_audio", "policy_version"])
    session, _ = core.session(setup)
    recording_id = str(uuid.uuid4())
    rows, segments, start = [], [], 0
    monkeypatch.setattr(validation, "sandbox_probe", probe)
    for index, duration in enumerate(durations):
        data = _segment_bytes(duration)
        corrupt = reject_last and index == len(durations) - 1
        if corrupt:
            data = (
                b"BAD!" + data[4:]
            )  # Keep the full segment length; corrupt its header.
        upload, token, _, _ = core.initialized(
            setup,
            current=session,
            recording_id=recording_id,
            segment_index=index,
            purpose="conversation_recording",
            declared_bytes=len(data),
            # Client identity order must not define the playback timeline.
            occurrence_id=str(uuid.UUID(int=len(durations) - index)),
        )
        store = core.ImmutableStore()
        uploads.transfer(
            token,
            scope_id=scope.id,
            upload_id=upload.id,
            chunks=(
                data[offset : offset + uploads.CHUNK_BYTES]
                for offset in range(0, len(data), uploads.CHUNK_BYTES)
            ),
            storage=store,
        )
        uploads.complete(token, scope_id=scope.id, upload_id=upload.id)
        # Manifest publication may race asynchronous validation. Leave the bad
        # segment VALIDATING until after finalization, then let the real validator
        # reject it; never mutate immutable identities or force READY -> REJECTED.
        if not corrupt:
            validation.validate(upload.id, scope_id=scope.id, storage=store)
        upload.refresh_from_db()
        assert upload.state == (
            AttachmentState.VALIDATING if corrupt else AttachmentState.READY
        )
        row = AttachmentOccurrence.objects.select_related("attachment", "scope").get(
            pk=upload.occurrence_id
        )
        rows.append(row)
        segments.append(
            {
                "occurrence_id": str(row.client_id),
                "index": index,
                "start_ms": start,
                "duration_ms": duration,
            }
        )
        start += duration
    body = {
        "schema_version": 1,
        "recording_id": recording_id,
        "timebase": "session_relative_ms",
        "sample_format": "s16le",
        "sample_rate_hz": 16000,
        "channels": 1,
        "codec": "pcm",
        "container": "wav",
        "layout": "mixed",
        "tracks": [
            {
                "track_id": str(uuid.uuid4()),
                "channels": [0],
                "direction": "mixed",
                "role": "mixed",
            }
        ],
        "segments": segments,
        "gaps": [],
        "playout": [],
        "capture_complete": False,
        "duration_ms": start,
    }
    wire.validate("RecordingManifest", body)
    manifests.put_manifest(
        token, scope_id=scope.id, recording_id=recording_id, body=body, key="prefix"
    )
    spans = components.Spans()
    # Actual SDK recording segments bind from their authenticated parent, even
    # when the native span does not carry fi.attachments references.
    spans.put(scope.project_id, components.span_for(rows[0], refs=[]))
    assert control_plane.reconcile_tick(span_reader=spans)["bound"] == len(durations)
    reader = components.Reader(components.viewer_for(scope, user), spans)
    request = SimpleNamespace(
        organization=scope.organization, workspace=scope.workspace
    )

    def detail():
        result = {
            "project_id": str(scope.project_id),
            "observation_span": [
                {"trace_id": str(rows[0].trace_id), "id": rows[0].span_id}
            ],
        }
        enrich_voice_detail(request=request, result=result, reader=reader)
        return result

    # Fully uploaded and bound segments alone cannot imply SDK finalization.
    incomplete = detail()["recording_attachment"]
    if reject_last:
        assert incomplete["state"] in ("waiting", "validating")
    else:
        assert incomplete["state"] == "waiting"
    assert "manifest" not in incomplete
    body = {**body, "capture_complete": True}
    wire.validate("RecordingManifest", body)
    recording = manifests.put_manifest(
        token, scope_id=scope.id, recording_id=recording_id, body=body, key="final"
    )
    assert recording.capture_complete is True
    # Existing aggregate state is not updated by put_manifest or validation;
    # readiness must still come from the final manifest AND every authorized row.
    assert recording.state == AttachmentState.PENDING
    if reject_last:
        assert detail()["recording_attachment"]["state"] == "validating"
        with pytest.raises(AttachmentError):
            validation.validate(upload.id, scope_id=scope.id, storage=store)
        upload.refresh_from_db()
        assert upload.state == AttachmentState.REJECTED
    return body, rows, detail


@pytest.mark.parametrize("durations", [(30000, 6467), (30000, 30000, 6467)])
def test_completed_multisegment_recording_projects_the_full_validated_timeline(
    setup, user, monkeypatch, durations
):
    body, rows, detail = _recording(setup, user, monkeypatch, durations)
    result = detail()
    projection = result["recording_attachment"]
    assert projection == {
        "occurrence_id": str(rows[0].client_id),
        "parent": {"trace_id": str(rows[0].trace_id), "span_id": rows[0].span_id},
        "state": "ready",
        "manifest": body,
    }
    assert projection["manifest"]["duration_ms"] == sum(durations)
    assert len(projection["manifest"]["segments"]) == len(durations)
    assert [s["start_ms"] for s in body["segments"]] == [
        sum(durations[:i]) for i in range(len(durations))
    ]
    assert body["tracks"][0]["direction"] == "mixed"
    assert (body["codec"], body["container"], body["sample_rate_hz"]) == (
        "pcm",
        "wav",
        16000,
    )
    assert len(result["observation_span"][0]["attachments"]) == len(durations)


@pytest.mark.parametrize(
    "fence",
    [
        "deleted",
        "expired",
        "policy",
        "generation",
        "unbound",
        "blob_deleted",
        "rejected",
    ],
)
def test_authority_fences_prevent_a_complete_voice_projection(
    setup, user, monkeypatch, fence
):
    _, rows, detail = _recording(
        setup, user, monkeypatch, (30000, 6467), reject_last=fence == "rejected"
    )
    if fence == "rejected":
        projection = detail()["recording_attachment"]
        assert projection["state"] != "ready"
        assert "manifest" not in projection
        return
    assert detail()["recording_attachment"]["state"] == "ready"
    if fence in ("policy", "generation"):
        # These are scope-wide authorities; per-occurrence snapshots are
        # immutable. Invalidate through the live authority, not an illegal row edit.
        scope, _ = setup
        field = "policy_version" if fence == "policy" else "deletion_generation"
        setattr(scope, field, getattr(scope, field) + 1)
        scope.save(update_fields=[field])
        assert "recording_attachment" not in detail()
        return
    row = rows[1]
    if fence == "deleted":
        row.deleted_at = timezone.now()
    elif fence == "expired":
        row.expires_at = timezone.now() - timedelta(seconds=1)
    elif fence == "unbound":
        row.bound_at = None
    elif fence == "blob_deleted":
        row.attachment.deleted_at = timezone.now()
        row.attachment.save(update_fields=["deleted_at"])
    # Change only this fence; binding populated source authority after row load.
    changed = {
        "deleted": "deleted_at",
        "expired": "expires_at",
        "unbound": "bound_at",
    }.get(fence)
    if changed:
        row.save(update_fields=[changed])
    projection = detail()["recording_attachment"]
    assert projection["state"] != "ready"
    assert "manifest" not in projection


def test_metadata_page_cannot_prove_recording_uniqueness(setup, user, monkeypatch):
    scope, _ = setup
    scope.record_audio, scope.policy_version = True, scope.policy_version + 1
    scope.save(update_fields=["record_audio", "policy_version"])
    spans = components.Spans()
    reader = components.Reader(components.viewer_for(scope, user), spans)
    request = SimpleNamespace(
        organization=scope.organization, workspace=scope.workspace
    )
    monkeypatch.setattr(control_plane, "MAX_READ_ROWS", 1)
    for count in (1, 2):
        row = components.occurrence(
            setup, monkeypatch, purpose="conversation_recording"
        )
        if count == 1:
            spans.put(scope.project_id, components.span_for(row, refs=[]))
        assert control_plane.reconcile_tick(span_reader=spans)["bound"] == 1
        result = {
            "project_id": str(scope.project_id),
            "observation_span": [{"trace_id": str(row.trace_id), "id": row.span_id}],
        }
        enrich_voice_detail(request=request, result=result, reader=reader)
        assert len(result["observation_span"][0]["attachments"]) == 1
        if count == 1:
            assert result["recording_attachment"]["state"] == "ready"
        else:
            assert result.get("recording_attachment", {}).get("occurrence_id") is None
