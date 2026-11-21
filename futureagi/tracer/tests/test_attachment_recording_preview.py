"""App-only recording preview and derivative header with the F01 HTTP fixture."""

import json
import uuid

import pytest

from tracer.models.attachment_recording import AttachmentRecordingDerivative
from tracer.services.attachments import control_plane, derivatives
from tracer.tests import test_attachment_api as f01

api = f01.api
pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def preview(api, occurrence, span=f01.SPAN):
    return api.client.get(
        f"{f01.base(api)}/occurrences/{occurrence}/recording-preview",
        {"trace_id": f01.TRACE, "span_id": span},
        **f01.auth(api.token),
    )


def test_preview_requires_an_authorized_selected_call(api, settings):
    _, body, _, row = f01.ready_occurrence(api, purpose="conversation_recording")
    response = preview(api, row.client_id)
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    projection = response.json()
    assert set(projection) == {"parent", "occurrence_id", "manifest", "state"}
    assert projection["state"] == "ready"
    assert projection["occurrence_id"] == body["occurrence_id"]
    assert projection["manifest"]["recording_id"] == body["occurrence_id"]
    assert projection["parent"] == {
        "trace_id": str(row.trace_id),
        "span_id": row.span_id,
    }
    _, _, _, clip = f01.ready_occurrence(api, purpose="turn_recording")
    for denied in (
        preview(api, clip.client_id),
        preview(api, row.client_id, f01.OTHER_SPAN),
        preview(api, body["attachment_id"]),
        preview(api, uuid.uuid4()),
    ):
        f01.assert_error(denied, 404, "not_found")
    settings.FI_ATTACHMENTS_ENABLED = False
    f01.assert_error(preview(api, row.client_id), 404, "not_found")


def test_peaks_header_matches_only_the_permitted_immutable_bytes(api, monkeypatch):
    _, body, _, occurrence = f01.ready_occurrence(api, purpose="conversation_recording")

    # Supply the same retained source span to the worker as to the HTTP viewer.
    def span_batch(project_id, span_ids, **_kwargs):
        from tracer.services.attachments.occurrences import Parent

        span = f01.span_double(api.state)(
            project_id, Parent(occurrence.trace_id, occurrence.span_id)
        )
        return (
            {(str(occurrence.trace_id), occurrence.span_id): span}
            if occurrence.span_id in span_ids
            else {}
        )

    monkeypatch.setattr(control_plane, "read_spans", span_batch)
    derivative = AttachmentRecordingDerivative.objects.get(
        attachment_id=occurrence.attachment_id, representation="peaks"
    )
    assert (
        derivatives.generate(derivative.id, scope_id=api.scope.id, storage=api.store)
        == "ready"
    )
    handle = f01.media_exchange(api)["X-Attachment-Media-Session"]

    def path(rep):
        item = f01.resolve(
            api, body["occurrence_id"], representation=rep, media_session_id=handle
        ).json()["items"][0]
        assert item["status"] == "ready"
        return item["gateway_path"]

    peaks = path("peaks")
    response = f01.gateway(api, peaks)
    assert response.status_code == 200
    assert json.loads(response["X-Attachment-Peaks"]) == {
        "v": 1,
        "sample_rate": 24000,
        "channels": 2,
        "frame_count": 240,
        "levels": [
            {"frames_per_bucket": width, "buckets": 1} for width in (240, 2400, 24000)
        ],
    }
    # Core fixture PCM: each left sample is 256, each right sample 512.
    assert (
        b"".join(response.streaming_content) == b"\x00\x01\x00\x01\x00\x02\x00\x02" * 3
    )
    assert "X-Attachment-Peaks" not in f01.gateway(api, path("playable"))
    assert "X-Attachment-Peaks" not in f01.gateway(api, peaks, method="head")
    api.client.cookies.clear()
    refused = f01.gateway(api, peaks)
    assert refused.status_code == 404 and "X-Attachment-Peaks" not in refused
