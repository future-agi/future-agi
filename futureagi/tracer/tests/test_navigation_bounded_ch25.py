"""Exact, fail-closed navigation over the direct-write ClickHouse lists."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest
from clickhouse_driver.errors import ServerException
from django.conf import settings as django_settings

from tracer.selectors.trace_filter_reads import BoundedFilterNeighbors


def _complete_neighbors(rows):
    newer, current, older = rows
    return BoundedFilterNeighbors(
        newer=newer,
        current=current,
        older=older,
        complete=True,
        error_code=None,
        query_count=1,
        rows_scanned=len(rows),
    )


def _incomplete_neighbors(error_code):
    return BoundedFilterNeighbors(
        newer=None,
        current=None,
        older=None,
        complete=False,
        error_code=error_code,
        query_count=1,
        rows_scanned=0,
    )


def _ordered_rows(*, span=False):
    started = datetime(2026, 7, 31, tzinfo=UTC)
    rows = []
    for offset, label in enumerate(("newer", "current", "older")):
        row = {
            "trace_id": f"trace-{label}",
            "start_time": started - timedelta(seconds=offset),
        }
        if span:
            row.update(
                {
                    "project_id": "project-1",
                    "id": f"span-{label}",
                }
            )
        rows.append(row)
    return rows


@pytest.mark.unit
def test_trace_navigation_preserves_newest_first_list_direction():
    from tracer.views.trace import TraceView

    neighbors = _complete_neighbors(_ordered_rows())
    with patch(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_neighbors",
        return_value=neighbors,
    ) as selector:
        response = TraceView()._get_trace_id_by_index_observe_clickhouse(
            MagicMock(),
            "trace-current",
            "project-1",
            [],
            MagicMock(),
        )

    assert response.status_code == 200
    assert response.data["result"] == {
        "next_trace_id": "trace-older",
        "previous_trace_id": "trace-newer",
    }
    assert selector.call_args.kwargs["target_id"] == "trace-current"
    assert selector.call_args.kwargs["scan_limit"] == 4095
    assert selector.call_args.kwargs["page_size"] == 200
    assert (
        selector.call_args.kwargs["deadline_ms"]
        == django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    assert selector.call_args.kwargs["max_query_count"] == 128


@pytest.mark.unit
def test_span_navigation_preserves_newest_first_list_direction():
    from tracer.views.observation_span import ObservationSpanView

    neighbors = _complete_neighbors(_ordered_rows(span=True))
    with (
        patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_neighbors",
            return_value=neighbors,
        ) as selector,
        patch("tracer.views.observation_span.V2AnalyticsQueryService"),
    ):
        response = ObservationSpanView()._bounded_span_navigation_response(
            project_id="project-1",
            span_id="span-current",
            filters=[],
        )

    assert response.status_code == 200
    assert response.data["result"] == {
        "next_trace_id": "trace-older",
        "previous_trace_id": "trace-newer",
    }
    assert selector.call_args.kwargs["target_id"] == "span-current"
    assert selector.call_args.kwargs["scan_limit"] == 4095
    assert selector.call_args.kwargs["page_size"] == 200
    assert (
        selector.call_args.kwargs["deadline_ms"]
        == django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    assert selector.call_args.kwargs["max_query_count"] == 128
    assert selector.call_args.kwargs["require_unique_target"] is True


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["trace", "span"])
def test_navigation_never_guesses_across_an_unread_page_boundary(kind):
    from tracer.views.observation_span import (
        ObservationSpanView,
        SpanNavigationReadUnavailable,
    )
    from tracer.views.trace import TraceNavigationReadUnavailable, TraceView

    neighbors = _incomplete_neighbors("page_depth_exceeded")
    with (
        patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_neighbors",
            return_value=neighbors,
        ),
        patch("tracer.views.observation_span.V2AnalyticsQueryService"),
    ):
        if kind == "trace":
            with pytest.raises(
                TraceNavigationReadUnavailable, match="page_depth_exceeded"
            ):
                TraceView()._get_trace_id_by_index_observe_clickhouse(
                    MagicMock(),
                    "trace-current",
                    "project-1",
                    [],
                    MagicMock(),
                )
        else:
            with pytest.raises(
                SpanNavigationReadUnavailable, match="page_depth_exceeded"
            ):
                ObservationSpanView()._bounded_span_navigation_response(
                    project_id="project-1",
                    span_id="span-current",
                    filters=[],
                )


@pytest.mark.unit
def test_trace_navigation_redacts_clickhouse_timeout():
    from tracer.views.trace import TraceView

    project_id = "00000000-0000-0000-0000-000000000001"
    trace_id = "00000000-0000-0000-0000-000000000002"
    request = SimpleNamespace(
        validated_query_data={
            "trace_id": trace_id,
            "project_id": project_id,
            "filters": [],
        }
    )
    project_scope = MagicMock()
    project_scope.filter.return_value.first.return_value = SimpleNamespace(
        trace_type="observe"
    )
    private_error = "secret SQL and internal ClickHouse stack"

    with (
        patch(
            "tracer.views.trace._project_queryset_for_request",
            return_value=project_scope,
        ),
        patch("tracer.views.trace.V2AnalyticsQueryService"),
        patch.object(
            TraceView,
            "_get_trace_id_by_index_observe_clickhouse",
            side_effect=ServerException(private_error, code=159),
        ),
    ):
        response = TraceView.get_trace_id_by_index_observe.__wrapped__(
            TraceView(), request
        )

    assert response.status_code == 503
    assert response.data["result"] == (
        "Trace navigation is temporarily unavailable. Please retry."
    )
    assert private_error not in str(response.data)
    assert "DB::Exception" not in str(response.data)


@pytest.mark.unit
def test_span_navigation_redacts_clickhouse_timeout():
    from tracer.views.observation_span import ObservationSpanView

    project_id = "00000000-0000-0000-0000-000000000001"
    span_id = "00000000-0000-0000-0000-000000000002"
    request = SimpleNamespace(
        validated_query_data={
            "span_id": span_id,
            "project_id": project_id,
            "user_id": None,
            "filters": [],
        }
    )
    private_error = "Code: 159. DB::Exception: secret SQL and internal stack"

    with (
        patch(
            "tracer.views.observation_span._project_workspace_scope_q",
            return_value=MagicMock(),
        ),
        patch(
            "tracer.views.observation_span._get_request_organization",
            return_value=MagicMock(),
        ),
        patch(
            "tracer.views.observation_span.Project.objects.get",
            return_value=SimpleNamespace(trace_type="observe"),
        ),
        patch.object(
            ObservationSpanView,
            "_bounded_span_navigation_response",
            side_effect=ServerException(private_error, code=159),
        ),
    ):
        response = (
            ObservationSpanView.get_trace_id_by_index_spans_as_observe.__wrapped__(
                ObservationSpanView(), request
            )
        )

    assert response.status_code == 503
    assert response.data["result"] == (
        "Span navigation is temporarily unavailable. Please retry."
    )
    assert response.data["code"] == "service_unavailable"
    assert private_error not in str(response.data)
    assert "DB::Exception" not in str(response.data)


@pytest.mark.unit
def test_trace_navigation_completed_miss_is_not_reported_as_unavailable():
    from tracer.views.trace import TraceNavigationReadUnavailable, TraceView

    project_id = "00000000-0000-0000-0000-000000000001"
    trace_id = "00000000-0000-0000-0000-000000000002"
    request = SimpleNamespace(
        validated_query_data={
            "trace_id": trace_id,
            "project_id": project_id,
            "filters": [],
        }
    )
    project_scope = MagicMock()
    project_scope.filter.return_value.first.return_value = SimpleNamespace(
        trace_type="observe"
    )

    with (
        patch(
            "tracer.views.trace._project_queryset_for_request",
            return_value=project_scope,
        ),
        patch("tracer.views.trace.V2AnalyticsQueryService"),
        patch.object(
            TraceView,
            "_get_trace_id_by_index_observe_clickhouse",
            side_effect=TraceNavigationReadUnavailable("trace_not_in_list"),
        ),
    ):
        response = TraceView.get_trace_id_by_index_observe.__wrapped__(
            TraceView(), request
        )

    assert response.status_code == 400
    assert response.data["result"] == "Trace not found"


@pytest.mark.unit
def test_non_observe_trace_navigation_redacts_database_failure():
    from tracer.views.trace import TraceView

    request = SimpleNamespace(
        validated_query_data={
            "trace_id": "00000000-0000-0000-0000-000000000001",
            "project_version_id": "00000000-0000-0000-0000-000000000002",
            "filters": [],
        }
    )
    private_error = "secret SQL and internal database stack"

    with patch(
        "tracer.views.trace._project_version_queryset_for_request",
        side_effect=ServerException(private_error, code=159),
    ):
        response = TraceView.get_trace_id_by_index.__wrapped__(TraceView(), request)

    assert response.status_code == 400
    assert response.data["result"] == "Trace navigation could not be loaded"
    assert private_error not in str(response.data)


@pytest.mark.unit
def test_observation_span_fields_redact_internal_failure():
    from tracer.models.observation_span import ObservationSpan
    from tracer.views.observation_span import ObservationSpanView

    private_error = "secret metadata and internal database stack"
    with patch.object(
        ObservationSpan._meta,
        "get_fields",
        side_effect=ServerException(private_error, code=159),
    ):
        response = ObservationSpanView().get_observation_span_fields(SimpleNamespace())

    assert response.status_code == 400
    assert response.data["result"] == "Observation span fields could not be loaded"
    assert private_error not in str(response.data)


@pytest.mark.unit
def test_observation_span_export_uses_bounded_list_page():
    from tracer.views.observation_span import ObservationSpanView

    request = SimpleNamespace(
        query_params={},
        validated_query_data={"project_id": "project-1", "filters": []},
        organization=SimpleNamespace(id="org-1"),
    )
    view = ObservationSpanView()
    view.request = request
    project = SimpleNamespace(name="project")
    project_queryset = MagicMock()
    project_queryset.first.return_value = project
    page = MagicMock(status_code=200)
    page.data = {"result": {"table": [], "metadata": {"has_more": False}}}

    with (
        patch(
            "tracer.views.observation_span.Project.objects.filter",
            return_value=project_queryset,
        ),
        patch.object(view, "list_spans_observe", return_value=page) as list_spans,
    ):
        response = view.get_spans_export_data.__wrapped__(view, request)

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    list_spans.assert_called_once_with(request, bounded_export=True)


@pytest.mark.unit
def test_trace_export_uses_bounded_list_page():
    from tracer.views.trace import TraceView

    request = SimpleNamespace(
        query_params={},
        validated_query_data={"project_id": "project-1", "filters": []},
    )
    view = TraceView()
    view.request = request
    project = SimpleNamespace(name="project")
    project_queryset = MagicMock()
    project_queryset.filter.return_value.first.return_value = project
    page = MagicMock(status_code=200)
    page.data = {"result": {"table": [], "metadata": {"has_more": False}}}

    with (
        patch(
            "tracer.views.trace._project_queryset_for_request",
            return_value=project_queryset,
        ),
        patch("tracer.views.trace._has_voice_conversation_roots", return_value=False),
        patch.object(view, "list_traces_of_session", return_value=page) as list_traces,
    ):
        response = view.get_trace_export_data.__wrapped__(view, request)

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    list_traces.assert_called_once_with(
        request,
        bounded_export=True,
        read_deadline=ANY,
    )
