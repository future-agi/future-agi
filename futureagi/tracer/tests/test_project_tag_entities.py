"""Regression: tagging a ClickHouse-only trace must not require a Postgres row.

TH-7275. After the CH25 cutover, retrieve reads ClickHouse while
``TraceView.update_tags`` still loads ``Trace`` from Postgres and returns
400 "Trace not found" when that row is absent. The frontend also posts
``{name, color}`` objects; colour belongs on the project tag, and the
assignment written to ClickHouse must stay a name list.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from rest_framework import status

from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace


def _ch_only_root(project, *, span_id, trace_id, tags=None, start=None):
    """A root span that exists only as a ClickHouse row, not in Postgres."""
    start = start or datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

    class _Span:
        pass

    span = _Span()
    span.id = span_id
    span.trace_id = trace_id
    span.project_id = project.id
    span.project = project
    span.project_version_id = None
    span.parent_span_id = ""
    span.name = "root"
    span.observation_type = "agent"
    span.status = "OK"
    span.start_time = start
    span.end_time = start + timedelta(milliseconds=20)
    span.tags = tags or []
    span.deleted = False
    return span


@pytest.mark.integration
@pytest.mark.django_db
class TestProjectScopedTagEntities:
    def test_patch_trace_tags_succeeds_when_only_clickhouse_has_the_trace(
        self, auth_client, observe_project, ch_seed
    ):
        trace_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())
        ch_seed(
            _ch_only_root(
                observe_project,
                span_id=span_id,
                trace_id=trace_id,
            )
        )
        assert not Trace.objects.filter(id=trace_id).exists()
        assert not ObservationSpan.objects.filter(id=span_id).exists()

        response = auth_client.patch(
            f"/tracer/trace/{trace_id}/tags/",
            {
                "tags": [
                    {"name": "plain-one", "color": "#22C55E"},
                    "bare-name",
                ]
            },
            format="json",
            QUERY_STRING=f"project_id={observe_project.id}",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"]["tags"] == ["plain-one", "bare-name"]
        from tracer.models.project import ProjectTagDefinition

        definition = ProjectTagDefinition.objects.get(
            project=observe_project, name="plain-one"
        )
        assert definition.color == "#22C55E"
        from tracer.tests._ch_seed import _get_ch_client

        client = _get_ch_client()
        try:
            rows = client.query(
                "SELECT tags FROM spans FINAL "
                "WHERE project_id = %(project_id)s AND id = %(span_id)s",
                parameters={
                    "project_id": str(observe_project.id),
                    "span_id": span_id,
                },
            ).result_rows
        finally:
            client.close()
        assert rows, "root span assignment was not written"
        assert "plain-one" in rows[0][0]
        assert "#22C55E" not in rows[0][0]

    def test_tag_definitions_list_rename_and_delete(
        self, auth_client, observe_project, ch_seed
    ):
        span = _ch_only_root(
            observe_project,
            span_id=str(uuid.uuid4()),
            trace_id=str(uuid.uuid4()),
            tags=["sdk-only", "plain-one"],
        )
        ch_seed(span)

        listed = auth_client.get(
            f"/tracer/project/{observe_project.id}/tag-definitions/"
        )
        assert listed.status_code == status.HTTP_200_OK, listed.data
        names = {item["name"] for item in listed.data["result"]["tags"]}
        assert "sdk-only" in names

        renamed = auth_client.patch(
            f"/tracer/project/{observe_project.id}/tag-definitions/",
            {"name": "sdk-only", "new_name": "plain-one", "color": "#3B82F6"},
            format="json",
        )
        assert renamed.status_code == status.HTTP_200_OK, renamed.data

        deleted = auth_client.delete(
            f"/tracer/project/{observe_project.id}/tag-definitions/?name=plain-one"
        )
        assert deleted.status_code == status.HTTP_200_OK, deleted.data

        from tracer.tests._ch_seed import _get_ch_client

        client = _get_ch_client()
        try:
            rows = client.query(
                "SELECT tags FROM spans FINAL WHERE id = {span_id:String}",
                parameters={"span_id": span.id},
            ).result_rows
        finally:
            client.close()
        assert "plain-one" not in rows[0][0]
        assert "sdk-only" not in rows[0][0]

    def test_patch_trace_tags_resolves_project_when_client_omits_project_id(
        self, auth_client, observe_project, ch_seed
    ):
        trace_id = str(uuid.uuid4())
        ch_seed(
            _ch_only_root(
                observe_project,
                span_id=str(uuid.uuid4()),
                trace_id=trace_id,
            )
        )

        response = auth_client.patch(
            f"/tracer/trace/{trace_id}/tags/",
            {"tags": ["legacy-caller"]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"]["tags"] == ["legacy-caller"]
