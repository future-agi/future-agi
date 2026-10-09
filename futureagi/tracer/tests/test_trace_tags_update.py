"""PATCH /tracer/trace/{id}/tags/ (TraceView.update_tags) — TH-8026.

The trace list reads tags from ClickHouse ``traces`` (v2 trace_list
``argMax(tags, _version)``). With the CDC chain dropped, the only path from a
Postgres ``tracer_trace`` write to that table is
``trace_writer.mirror_traces_to_clickhouse`` scheduled on commit, so a tag
save that skips it never reaches the list — and the next bulk add from the
grid merges into the stale list and drops the earlier tags.
"""

import pytest
from rest_framework import status


@pytest.fixture
def mirrored(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.trace_writer.mirror_traces_to_clickhouse",
        lambda ids: calls.append([str(i) for i in ids]),
    )
    return calls


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestTraceTagsUpdate:
    def test_saves_tag_names_and_mirrors_the_trace_to_clickhouse(
        self, auth_client, trace, mirrored, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.patch(
                f"/tracer/trace/{trace.id}/tags/",
                {"tags": ["prod", "need improvement"]},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        trace.refresh_from_db()
        assert trace.tags == ["prod", "need improvement"]
        assert mirrored == [[str(trace.id)]]

    def test_rejects_tag_objects_without_saving_or_mirroring(
        self, auth_client, trace, mirrored, django_capture_on_commit_callbacks
    ):
        trace.tags = ["prod"]
        trace.save(update_fields=["tags"])

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.patch(
                f"/tracer/trace/{trace.id}/tags/",
                {"tags": [{"name": "need improvement", "color": "#EF4444"}]},
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Not a valid string" in response.content.decode()
        trace.refresh_from_db()
        assert trace.tags == ["prod"]
        assert mirrored == []
