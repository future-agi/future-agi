"""Session-list transport coverage for bounded scalar-attribute filters."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest

from tracer.selectors.trace_filter_reads import BoundedFilterPage
from tracer.services.clickhouse.list_cursor import (
    ListCursorError,
    list_cursor_boundary_fingerprint,
)
from tracer.services.filter_attestation import applied_filter_attestation


def _attribute_filter() -> dict:
    return {
        "column_id": "final_status",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": ["Rejected"],
        },
    }


def _has_eval_filter(value: bool | str) -> dict:
    return {
        "column_id": "has_eval",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _has_annotation_filter(value: bool | str) -> dict:
    return {
        "column_id": "has_annotation",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _bounded_page(
    *,
    rows: list[dict] | None = None,
    has_more: bool = False,
    complete: bool = True,
    error_code: str | None = None,
    total_rows_lower_bound: int = 0,
    continuation_slice_end: datetime | None = None,
    continuation_before_start_time: datetime | None = None,
    continuation_before_id: str | None = None,
) -> BoundedFilterPage:
    return BoundedFilterPage(
        rows=list(rows or []),
        has_more=has_more,
        complete=complete,
        status="complete" if complete else "degraded",
        error_code=error_code,
        total_rows_lower_bound=total_rows_lower_bound,
        elapsed_ms=12.5,
        query_count=2,
        rows_returned=len(rows or []),
        result_payload_bytes=128,
        attempts=(),
        continuation_slice_end=continuation_slice_end,
        continuation_before_start_time=continuation_before_start_time,
        continuation_before_id=continuation_before_id,
    )


def _view_and_request():
    from tracer.views.trace_session import TraceSessionView

    view = TraceSessionView.__new__(TraceSessionView)
    view._gm = SimpleNamespace(
        success_response=lambda payload: ("ok", payload),
        custom_error_response=lambda status, message, code: (
            "error",
            status,
            message,
            code,
        ),
        bad_request=lambda message: ("bad_request", message),
    )
    organization = SimpleNamespace(id=uuid.uuid4())
    request = SimpleNamespace(
        query_params={},
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    return view, request


@pytest.mark.unit
def test_session_partial_page_cursor_prefers_hidden_rollup_seed_order():
    from tracer.views.trace_session import _session_list_cursor_order_for_partial_page

    seed_start = datetime(2025, 1, 1, 0, 0)
    exact_start = datetime(2026, 8, 11, 12, 0)
    raw_session_id = str(uuid.uuid4())
    canonical_session_id = str(uuid.uuid4())

    order = _session_list_cursor_order_for_partial_page(
        rows=[
            {
                "session_id": canonical_session_id,
                "start_time": exact_start,
                "_seed_order_start": seed_start,
                "_seed_order_id": raw_session_id,
            }
        ],
        bounded_page=SimpleNamespace(),
        cursor_state=None,
    )

    assert order == (seed_start, raw_session_id)


@pytest.mark.unit
def test_org_session_relational_collision_fails_before_id_only_hydration():
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    project_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    session_id = str(uuid.uuid4())
    builder = mock.MagicMock()
    builder.supports_candidate_first_page.return_value = False
    builder.supports_bounded_filter_scan.return_value = True
    builder.recommended_filter_classify_batch_size.return_value = 50
    builder_cls = mock.MagicMock(return_value=builder)
    analytics = mock.MagicMock()
    bounded = _bounded_page(
        rows=[
            {
                "session_id": session_id,
                "start_time": datetime(2026, 7, 31, 12, 0),
                # The classifier computes this over roots *before* applying
                # relational membership, so a matching A row cannot hide the
                # colliding session UUID that also exists in project B.
                "project_count": 2,
            }
        ],
        total_rows_lower_bound=1,
    )

    with (
        mock.patch("tracer.views.trace_session.SessionListQueryBuilderV2", builder_cls),
        mock.patch(
            "tracer.views.trace_session.read_bounded_filter_page",
            return_value=bounded,
        ),
    ):
        response = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=None,
            project=None,
            analytics=analytics,
            validated_data={
                "filters": [_attribute_filter()],
                "sort_params": [],
                "page_number": 0,
                "page_size": 25,
            },
            org_project_ids=project_ids,
        )

    assert response == (
        "error",
        503,
        "Session data is temporarily unavailable. Please retry.",
        "service_unavailable",
    )
    builder.build_page_metrics_query.assert_not_called()
    builder.build_content_query.assert_not_called()
    builder.build_span_attributes_query.assert_not_called()
    analytics.execute_ch_query.assert_not_called()


@pytest.mark.unit
def test_default_org_session_collision_fails_before_id_only_hydration():
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    project_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    session_id = str(uuid.uuid4())
    builder = mock.MagicMock()
    builder.supports_candidate_first_page.return_value = True
    builder.build_candidate_page_query.return_value = ("candidate page", {})
    builder_cls = mock.MagicMock(return_value=builder)
    analytics = mock.MagicMock()
    analytics.execute_ch_query.return_value = SimpleNamespace(
        data=[
            {
                "session_id": session_id,
                "session_start": datetime(2026, 7, 31, 12, 0),
                "project_count": 2,
                "max_project_count": 2,
                "total_count": 1,
            }
        ]
    )

    with mock.patch(
        "tracer.views.trace_session.SessionListQueryBuilderV2", builder_cls
    ):
        response = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=None,
            project=None,
            analytics=analytics,
            validated_data={
                "filters": [],
                "sort_params": [],
                "page_number": 0,
                "page_size": 25,
            },
            org_project_ids=project_ids,
        )

    assert response == (
        "error",
        503,
        "Session data is temporarily unavailable. Please retry.",
        "service_unavailable",
    )
    builder.build_page_metrics_query.assert_not_called()
    builder.build_content_query.assert_not_called()
    builder.build_span_attributes_query.assert_not_called()


@pytest.mark.unit
def test_org_session_view_passes_disjoint_annotation_label_sets_to_builder():
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    project_a = str(uuid.uuid4())
    project_b = str(uuid.uuid4())
    label_a = SimpleNamespace(id=uuid.uuid4())
    label_b = SimpleNamespace(id=uuid.uuid4())
    builder = mock.MagicMock()
    builder.supports_candidate_first_page.return_value = True
    builder.build_candidate_page_query.return_value = ("candidate page", {})
    builder.build_candidate_count_query.return_value = ("candidate count", {})
    builder_cls = mock.MagicMock(return_value=builder)
    analytics = mock.MagicMock()
    analytics.execute_ch_query.side_effect = [
        SimpleNamespace(data=[]),
        SimpleNamespace(data=[{"total": 0}]),
    ]

    with (
        mock.patch("tracer.views.trace_session.SessionListQueryBuilderV2", builder_cls),
        mock.patch(
            "tracer.views.trace_session.get_annotation_labels_by_project",
            return_value={project_a: [label_a], project_b: [label_b]},
        ) as label_source,
    ):
        status, payload = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=None,
            project=None,
            analytics=analytics,
            validated_data={
                "filters": [_has_annotation_filter(True)],
                "sort_params": [],
                "page_number": 0,
                "page_size": 25,
            },
            org_project_ids=[project_a, project_b],
        )

    assert status == "ok"
    assert payload["metadata"] == {"total_rows": 0}
    label_source.assert_called_once_with(
        [project_a, project_b], organization=request.organization
    )
    kwargs = builder_cls.call_args.kwargs
    assert kwargs["annotation_label_ids"] == []
    assert kwargs["annotation_label_ids_by_project"] == {
        project_a: [str(label_a.id)],
        project_b: [str(label_b.id)],
    }


@pytest.mark.unit
def test_direct_write_session_attribute_query_replays_all_typed_maps():
    from tracer.services.clickhouse.v2.query_builders.session_list import (
        SessionListQueryBuilderV2,
    )

    builder = SessionListQueryBuilderV2(
        project_id=str(uuid.uuid4()),
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        datetime(2026, 7, 1, tzinfo=UTC),
                        datetime(2026, 8, 1, tzinfo=UTC),
                    ],
                },
            }
        ],
    )

    query, _ = builder.build_span_attributes_query([str(uuid.uuid4())])

    assert "argMax(attrs_string, _version) AS latest_attrs_string" in query
    assert "argMax(attrs_number, _version) AS latest_attrs_number" in query
    assert "argMax(attrs_bool, _version) AS latest_attrs_bool" in query
    assert "latest_attrs_bool AS attrs_bool" in query
    assert "length(mapKeys(latest_attrs_bool)) > 0" in query


@pytest.mark.unit
def test_direct_write_session_attribute_merge_unions_scalar_and_json_sources():
    from tracer.views.trace_session import _merge_session_attribute_sources

    attrs = _merge_session_attribute_sources(
        {
            "span_attributes_raw": '{"structured":{"attempt":2},"shared":"json"}',
            "attrs_string": {
                "final_status": "Rechazado",
                "shared": "typed-map",
            },
            "attrs_number": {"score": 12.5},
            "attrs_bool": {"approved": 1, "rejected": 0},
        }
    )

    assert attrs == {
        "structured": {"attempt": 2},
        "shared": "json",
        "final_status": "Rechazado",
        "score": 12.5,
        "approved": True,
        "rejected": False,
    }


@pytest.mark.unit
def test_session_end_user_enrichment_drops_clickhouse_null_before_uuid_lookup():
    from tracer.views.trace_session import TraceSessionView

    session_id = str(uuid.uuid4())
    analytics = mock.MagicMock()
    analytics.execute_ch_query.return_value = SimpleNamespace(
        data=[{"session_id": session_id, "end_user_id": None}]
    )

    with (
        mock.patch(
            "tracer.views.trace_session._resolve_session_ids_to_canonical",
            return_value={session_id: session_id},
        ),
        mock.patch(
            "tracer.services.clickhouse.v2.end_user_dict_reader.resolve_end_user_fields"
        ) as resolve_end_user_fields,
    ):
        result = TraceSessionView._fetch_end_user_info(
            [session_id],
            analytics,
        )

    assert result == {}
    resolve_end_user_fields.assert_not_called()


@pytest.mark.unit
def test_session_page_canonicalization_never_builds_global_remap_window():
    from tracer.views.trace_session import _resolve_session_ids_to_canonical

    session_ids = [str(uuid.uuid4()) for _ in range(30)]
    analytics = mock.MagicMock()
    analytics.execute_ch_query.return_value = SimpleNamespace(data=[])

    resolved = _resolve_session_ids_to_canonical(analytics, session_ids)

    assert resolved == {session_id: session_id for session_id in session_ids}
    sql, params = analytics.execute_ch_query.call_args.args[:2]
    assert "FROM trace_session_id_remap FINAL" in sql
    assert "old_id IN %(ids)s OR new_id IN %(ids)s" in sql
    assert "OVER (PARTITION BY new_id)" not in sql
    assert set(params["ids"]) == set(session_ids)


@pytest.mark.unit
def test_session_end_user_span_query_remap_is_page_bounded():
    from tracer.views.trace_session import TraceSessionView

    session_ids = [str(uuid.uuid4()) for _ in range(30)]
    analytics = mock.MagicMock()
    analytics.execute_ch_query.return_value = SimpleNamespace(data=[])

    with (
        mock.patch(
            "tracer.views.trace_session._resolve_session_ids_to_canonical",
            return_value={session_id: session_id for session_id in session_ids},
        ),
        mock.patch(
            "tracer.services.clickhouse.v2.end_user_dict_reader.resolve_end_user_fields"
        ) as resolve_end_user_fields,
    ):
        result = TraceSessionView._fetch_end_user_info(session_ids, analytics)

    assert result == {}
    resolve_end_user_fields.assert_not_called()
    sql, params = analytics.execute_ch_query.call_args.args[:2]
    assert "FROM trace_session_id_remap FINAL" in sql
    assert "old_id IN %(session_ids)s OR new_id IN %(session_ids)s" in sql
    assert "OVER (PARTITION BY new_id)" not in sql
    assert set(params["session_ids"]) == set(session_ids)


@pytest.mark.unit
def test_session_end_user_dictionary_lookup_remap_is_candidate_bounded():
    from tracer.services.clickhouse.v2.end_user_dict_reader import (
        resolve_end_user_fields,
    )

    end_user_ids = [str(uuid.uuid4()) for _ in range(30)]
    client = mock.MagicMock()
    client.query.return_value = SimpleNamespace(result_rows=[])

    with mock.patch(
        "tracer.services.clickhouse.v2.end_user_dict_reader._get_client",
        return_value=client,
    ):
        assert resolve_end_user_fields(end_user_ids) == {}

    sql = client.query.call_args.args[0]
    params = client.query.call_args.kwargs["parameters"]
    assert "FROM end_user_id_remap FINAL" in sql
    assert "old_id IN %(ids)s OR new_id IN %(ids)s" in sql
    assert "OVER (PARTITION BY new_id)" not in sql
    assert set(params["ids"]) == set(end_user_ids)


@pytest.mark.unit
def test_user_detail_reverse_lookup_keeps_transport_and_query_under_10_seconds(
    monkeypatch,
):
    import sys

    from tracer.services.clickhouse.v2 import end_user_dict_reader as reader

    client = mock.MagicMock()
    client.query.return_value = SimpleNamespace(result_rows=[])
    client_factory = mock.MagicMock(return_value=client)
    monkeypatch.setattr(reader, "_client", None)
    monkeypatch.setattr(
        reader,
        "get_v2_config",
        lambda: {
            "host": "clickhouse.invalid",
            "http_port": 8123,
            "tcp_port": 9000,
            "user": "readonly",
            "password": "",
            "database": "futureagi",
            "server_enforced_readonly": False,
        },
    )
    monkeypatch.setitem(
        sys.modules,
        "clickhouse_connect",
        SimpleNamespace(get_client=client_factory),
    )

    try:
        assert (
            reader.resolve_end_user_ids_by_user_id(
                "customer-1",
                organization_id=str(uuid.uuid4()),
                timeout_ms=120_000,
                settings={
                    "max_threads": 2,
                    "max_rows_to_read": 1,
                    "max_execution_time": 120,
                    "max_bytes_to_read": 256 * 1024 * 1024,
                    "max_memory_usage": 36 * 1024 * 1024 * 1024,
                    "max_result_rows": 10_000,
                },
            )
            == []
        )
    finally:
        reader._reset_client()

    assert client_factory.call_args.kwargs["send_receive_timeout"] == 9.5
    assert "settings" not in client_factory.call_args.kwargs
    query_settings = client.query.call_args.kwargs["settings"]
    assert query_settings["max_execution_time"] == 9.5
    assert "max_rows_to_read" not in query_settings
    assert query_settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
    assert query_settings["max_bytes_to_read"] == 256 * 1024 * 1024
    assert query_settings["max_threads"] == 2
    assert query_settings["max_result_rows"] == 10_000


@pytest.mark.unit
def test_user_detail_reverse_lookup_rejects_unbounded_locked_transport(monkeypatch):
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
    from tracer.services.clickhouse.server_readonly import (
        ServerEnforcedReadOnlyNativeClient,
    )
    from tracer.services.clickhouse.v2 import end_user_dict_reader as reader

    locked_client = object.__new__(ServerEnforcedReadOnlyNativeClient)
    monkeypatch.setattr(reader, "_get_client", lambda: locked_client)

    with pytest.raises(ReadDeadlineExceeded, match="cannot enforce request deadline"):
        reader.resolve_end_user_ids_by_user_id(
            "customer-1",
            organization_id=str(uuid.uuid4()),
            timeout_ms=30_000,
            settings={"max_threads": 2},
        )


@pytest.mark.unit
def test_session_export_context_reaches_clickhouse_list_path():
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    project_id = str(uuid.uuid4())
    request.query_params = {"project_id": project_id}
    request.method = "GET"
    request.data = {}
    view.request = request
    project = SimpleNamespace(id=project_id, source="observe")
    project_queryset = mock.MagicMock()
    project_queryset.get.return_value = project
    expected = object()

    with (
        mock.patch(
            "tracer.views.trace_session._project_queryset_for_request",
            return_value=project_queryset,
        ),
        mock.patch(
            "tracer.views.trace_session.V2AnalyticsQueryService",
            return_value=mock.MagicMock(),
        ),
        mock.patch.object(
            view,
            "_build_bookmark_filter",
            return_value=None,
        ),
        mock.patch.object(
            view,
            "_list_sessions_clickhouse",
            return_value=expected,
        ) as list_clickhouse,
    ):
        response = TraceSessionView.list_sessions(view, request, export=True)

    assert response is expected
    assert list_clickhouse.call_args.kwargs["export"] is True


@pytest.mark.unit
def test_attribute_session_list_uses_bounded_protocol_and_page_scoped_hydration():
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    project_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    start_time = datetime(2026, 7, 31, 12, 0)

    builder = mock.MagicMock()
    builder.supports_candidate_first_page.return_value = False
    builder.supports_bounded_filter_scan.return_value = True
    builder.recommended_filter_classify_batch_size.return_value = 50
    builder.build_page_metrics_query.return_value = ("page metrics", {})
    builder.build_content_query.return_value = ("page content", {})
    builder.build_span_attributes_query.return_value = ("page attributes", {})
    builder.format_sessions.side_effect = lambda rows, columns: [
        dict(zip(columns, row, strict=True)) for row in rows
    ]
    builder_cls = mock.MagicMock(return_value=builder)

    analytics = mock.MagicMock()

    def _execute(query, _params, **_kwargs):
        if query == "page metrics":
            return SimpleNamespace(
                data=[
                    {
                        "session_id": session_id,
                        "session_start": start_time,
                        "session_end": start_time,
                        "duration": 0,
                        "total_cost": 0,
                        "total_tokens": 0,
                        "traces_count": 1,
                    }
                ]
            )
        if query == "page content":
            return SimpleNamespace(
                data=[
                    {
                        "session_id": session_id,
                        "first_message": "first",
                        "last_message": "last",
                    }
                ]
            )
        if query == "page attributes":
            return SimpleNamespace(data=[])
        raise AssertionError(f"unexpected broad ClickHouse query: {query}")

    analytics.execute_ch_query.side_effect = _execute
    view._fetch_session_names = mock.MagicMock(return_value={})
    view._fetch_end_user_info = mock.MagicMock(return_value={})

    bounded = _bounded_page(
        rows=[{"session_id": session_id, "start_time": start_time}],
        has_more=True,
        total_rows_lower_bound=6,
    )
    filters = [_attribute_filter()]
    with (
        mock.patch(
            "tracer.views.trace_session.SessionListQueryBuilderV2",
            builder_cls,
        ),
        mock.patch(
            "tracer.views.trace_session.read_bounded_filter_page",
            return_value=bounded,
        ) as bounded_read,
        mock.patch(
            "tracer.views.trace_session.AnnotationsLabels.objects.filter",
            return_value=[],
        ),
    ):
        omitted_status, omitted_payload = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=project_id,
            project=None,
            analytics=analytics,
            validated_data={
                "filters": filters,
                "sort_params": [],
                "page_number": 4,
                "page_size": 1,
            },
        )
        request.query_params = {"allow_sampled": "false"}
        explicit_false_response = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=project_id,
            project=None,
            analytics=analytics,
            validated_data={
                "filters": filters,
                "sort_params": [],
                "page_number": 4,
                "page_size": 1,
                "allow_sampled": False,
            },
        )
        request.query_params = {"allow_sampled": "true"}
        status, payload = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=project_id,
            project=None,
            analytics=analytics,
            validated_data={
                "filters": filters,
                "sort_params": [],
                "page_number": 4,
                "page_size": 1,
                "allow_sampled": True,
            },
        )
        request.query_params = {}
        export_response = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=project_id,
            project=None,
            analytics=analytics,
            validated_data={
                "filters": filters,
                "sort_params": [],
                "page_number": 4,
                "page_size": 1,
            },
            export=True,
        )

    assert omitted_status == "ok"
    assert omitted_payload["metadata"]["total_rows_is_lower_bound"] is True
    assert explicit_false_response[0] == "error"
    assert explicit_false_response[1] == 503
    assert export_response == (
        "error",
        503,
        "A complete session export is temporarily unavailable. Narrow the filters and retry.",
        "service_unavailable",
    )
    assert status == "ok"
    assert payload["metadata"] == {
        "total_rows": 6,
        "total_rows_is_lower_bound": True,
        "has_more": True,
        "query_complete": True,
        "query_status": "complete",
        "query_error_code": None,
        "query_exact": False,
        "query_provenance": "spans_per_session_candidate",
        "ordering_exact": False,
        **applied_filter_attestation(
            project_id=project_id,
            observe_type="session",
            filters=filters,
        ),
    }
    assert payload["table"][0]["first_message"] == "first"
    assert payload["table"][0]["last_message"] == "last"
    assert bounded_read.call_count == 4
    bounded_kwargs = bounded_read.call_args.kwargs
    assert bounded_kwargs["key_field"] == "session_id"
    assert bounded_kwargs["page_number"] == 4
    assert bounded_kwargs["page_size"] == 1
    assert bounded_kwargs["max_candidates"] == 200
    assert bounded_kwargs["classify_batch_size"] == 50
    builder.build_candidate_page_query.assert_not_called()
    builder.build.assert_not_called()
    assert builder.build_page_metrics_query.call_count == 4
    assert builder.build_content_query.call_count == 4
    assert builder.build_span_attributes_query.call_count == 4


@pytest.mark.unit
def test_candidate_first_session_list_keeps_exact_metadata():
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    builder = mock.MagicMock()
    builder.supports_candidate_first_page.return_value = True
    builder.build_candidate_page_query.return_value = ("candidate page", {})
    builder.build_candidate_count_query.return_value = ("candidate count", {})
    builder_cls = mock.MagicMock(return_value=builder)
    analytics = mock.MagicMock()

    def _execute(query, _params, **_kwargs):
        if query == "candidate page":
            return SimpleNamespace(data=[])
        if query == "candidate count":
            return SimpleNamespace(data=[{"total": 0}])
        raise AssertionError(f"unexpected ClickHouse query: {query}")

    analytics.execute_ch_query.side_effect = _execute
    with (
        mock.patch(
            "tracer.views.trace_session.SessionListQueryBuilderV2",
            builder_cls,
        ),
        mock.patch(
            "tracer.views.trace_session.read_bounded_filter_page"
        ) as bounded_read,
        mock.patch(
            "tracer.views.trace_session.AnnotationsLabels.objects.filter",
            return_value=[],
        ),
    ):
        status, payload = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=str(uuid.uuid4()),
            project=None,
            analytics=analytics,
            validated_data={
                "filters": [],
                "sort_params": [],
                "page_number": 4,
                "page_size": 30,
            },
        )

    assert status == "ok"
    assert payload["metadata"] == {"total_rows": 0}
    bounded_read.assert_not_called()
    builder.build_candidate_page_query.assert_called_once_with()
    builder.build_candidate_count_query.assert_called_once_with()


@pytest.mark.unit
def test_session_list_keeps_exact_page_when_end_user_label_enrichment_exhausts_budget():
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    project_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    start_time = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    builder = mock.MagicMock()
    builder.supports_candidate_first_page.return_value = True
    builder.build_candidate_page_query.return_value = ("candidate page", {})
    builder.build_page_metrics_query.return_value = ("page metrics", {})
    builder.build_content_query.return_value = ("page content", {})
    builder.build_span_attributes_query.return_value = ("page attributes", {})
    builder.format_sessions.side_effect = lambda rows, columns: [
        dict(zip(columns, row, strict=True)) for row in rows
    ]
    analytics = mock.MagicMock()

    def _execute(query, _params, **_kwargs):
        if query == "candidate page":
            return SimpleNamespace(data=[{"session_id": session_id, "total_count": 1}])
        if query == "page metrics":
            return SimpleNamespace(
                data=[
                    {
                        "session_id": session_id,
                        "session_start": start_time,
                        "session_end": start_time,
                        "duration": 0,
                        "total_cost": 0,
                        "total_tokens": 0,
                        "traces_count": 1,
                    }
                ]
            )
        if query == "page content":
            return SimpleNamespace(data=[])
        if query == "page attributes":
            return SimpleNamespace(data=[])
        raise AssertionError(f"unexpected ClickHouse query: {query}")

    analytics.execute_ch_query.side_effect = _execute
    view._fetch_session_names = mock.MagicMock(return_value={})
    view._fetch_end_user_info = mock.MagicMock(
        side_effect=ReadDeadlineExceeded("end-user label budget exhausted")
    )

    with (
        mock.patch(
            "tracer.views.trace_session.SessionListQueryBuilderV2",
            return_value=builder,
        ),
        mock.patch(
            "tracer.views.trace_session.AnnotationsLabels.objects.filter",
            return_value=[],
        ),
    ):
        status, payload = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=project_id,
            project=None,
            analytics=analytics,
            validated_data={
                "filters": [],
                "sort_params": [],
                "page_number": 0,
                "page_size": 25,
            },
        )

    assert status == "ok"
    assert payload["metadata"] == {"total_rows": 1}
    assert payload["table"][0]["session_id"] == session_id
    assert payload["table"][0]["user_id"] is None
    assert payload["table"][0]["user_id_type"] is None
    assert payload["table"][0]["user_id_hash"] is None


@pytest.mark.unit
def test_session_export_rejects_truncated_exact_first_page():
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    project_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    start_time = datetime(2026, 7, 31, 12, 0)
    builder = mock.MagicMock()
    builder.supports_candidate_first_page.return_value = True
    builder.build_candidate_page_query.return_value = ("candidate page", {})
    builder.build_page_metrics_query.return_value = ("page metrics", {})
    builder.build_content_query.return_value = ("page content", {})
    builder.build_span_attributes_query.return_value = ("page attributes", {})
    builder.format_sessions.side_effect = lambda rows, columns: [
        dict(zip(columns, row, strict=True)) for row in rows
    ]
    analytics = mock.MagicMock()

    def _execute(query, _params, **_kwargs):
        if query == "candidate page":
            return SimpleNamespace(data=[{"session_id": session_id, "total_count": 2}])
        if query == "page metrics":
            return SimpleNamespace(
                data=[
                    {
                        "session_id": session_id,
                        "session_start": start_time,
                        "session_end": start_time,
                        "duration": 0,
                        "total_cost": 0,
                        "total_tokens": 0,
                        "traces_count": 1,
                    }
                ]
            )
        if query == "page content":
            return SimpleNamespace(
                data=[
                    {
                        "session_id": session_id,
                        "first_message": "first",
                        "last_message": "last",
                    }
                ]
            )
        if query == "page attributes":
            return SimpleNamespace(data=[])
        raise AssertionError(f"unexpected ClickHouse query: {query}")

    analytics.execute_ch_query.side_effect = _execute
    view._fetch_session_names = mock.MagicMock(return_value={})
    view._fetch_end_user_info = mock.MagicMock(return_value={})

    with (
        mock.patch(
            "tracer.views.trace_session.SessionListQueryBuilderV2",
            return_value=builder,
        ),
        mock.patch(
            "tracer.views.trace_session.AnnotationsLabels.objects.filter",
            return_value=[],
        ),
    ):
        response = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=project_id,
            project=None,
            analytics=analytics,
            validated_data={
                "filters": [],
                "sort_params": [],
                "page_number": 0,
                "page_size": 1,
            },
            export=True,
        )

    assert response == (
        "error",
        503,
        "A complete session export is temporarily unavailable. Narrow the filters and retry.",
        "service_unavailable",
    )


@pytest.mark.unit
def test_incomplete_bounded_session_list_returns_sanitized_503_without_hydration():
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    builder = mock.MagicMock()
    builder.supports_candidate_first_page.return_value = False
    builder.supports_bounded_filter_scan.return_value = True
    builder_cls = mock.MagicMock(return_value=builder)
    analytics = mock.MagicMock()

    with (
        mock.patch(
            "tracer.views.trace_session.SessionListQueryBuilderV2",
            builder_cls,
        ),
        mock.patch(
            "tracer.views.trace_session.read_bounded_filter_page",
            return_value=_bounded_page(
                complete=False,
                error_code="deadline_exceeded",
            ),
        ),
    ):
        response = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=str(uuid.uuid4()),
            project=None,
            analytics=analytics,
            validated_data={
                "filters": [_attribute_filter()],
                "sort_params": [],
                "page_number": 0,
                "page_size": 30,
            },
        )

    assert response == (
        "error",
        503,
        "Filtered session data is temporarily unavailable. Please retry.",
        "service_unavailable",
    )
    analytics.execute_ch_query.assert_not_called()
    builder.build_candidate_page_query.assert_not_called()
    builder.build.assert_not_called()
    builder.build_page_metrics_query.assert_not_called()


@pytest.mark.unit
def test_tampered_session_cursor_fails_closed_before_clickhouse_read():
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    analytics = mock.MagicMock()

    with pytest.raises(ListCursorError) as exc_info:
        TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=str(uuid.uuid4()),
            project=None,
            analytics=analytics,
            validated_data={
                "filters": [_attribute_filter()],
                "sort_params": [],
                "page_number": 0,
                "page_size": 30,
                "cursor_mode": True,
                "cursor": "tampered-token",
            },
        )

    assert exc_info.value.code == "invalid_cursor"
    assert str(exc_info.value) == "The continuation cursor is invalid."
    analytics.execute_ch_query.assert_not_called()


@pytest.mark.unit
def test_positive_user_cursor_uses_exact_keyset_pages_without_duplicates():
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    project_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    newest_id = str(uuid.uuid4())
    oldest_id = str(uuid.uuid4())
    newest_start = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    oldest_start = newest_start - timedelta(minutes=1)
    window_start = newest_start - timedelta(days=30)
    window_end = newest_start + timedelta(days=1)

    builder = mock.MagicMock()
    builder.supports_candidate_first_page.return_value = True
    builder.supports_candidate_cursor_page.return_value = True
    builder.supports_bounded_filter_scan.return_value = True
    builder.parse_time_range.return_value = (window_start, window_end)
    builder.build_candidate_cursor_page_query.side_effect = [
        ("candidate cursor first", {}),
        ("candidate cursor next", {}),
    ]
    builder.build_page_metrics_query.side_effect = lambda ids: (
        "page metrics",
        {"ids": tuple(ids)},
    )
    builder.build_content_query.side_effect = lambda ids: (
        "page content",
        {"ids": tuple(ids)},
    )
    builder.build_span_attributes_query.return_value = ("page attributes", {})
    builder.format_sessions.side_effect = lambda rows, columns: [
        dict(zip(columns, row, strict=True)) for row in rows
    ]
    analytics = mock.MagicMock()

    def _metrics_row(session_id: str, start_time: datetime) -> dict:
        return {
            "session_id": session_id,
            "session_start": start_time,
            "session_end": start_time,
            "duration": 0,
            "total_cost": 0,
            "total_tokens": 0,
            "traces_count": 1,
        }

    starts = {newest_id: newest_start, oldest_id: oldest_start}

    def _execute(query, params, **_kwargs):
        if query == "candidate cursor first":
            return SimpleNamespace(
                data=[
                    {
                        "session_id": newest_id,
                        "session_start": newest_start,
                        "remaining_count": 2,
                    },
                    {
                        "session_id": oldest_id,
                        "session_start": oldest_start,
                        "remaining_count": 2,
                    },
                ]
            )
        if query == "candidate cursor next":
            return SimpleNamespace(
                data=[
                    {
                        "session_id": oldest_id,
                        "session_start": oldest_start,
                        "remaining_count": 1,
                    }
                ]
            )
        if query == "page metrics":
            return SimpleNamespace(
                data=[_metrics_row(sid, starts[sid]) for sid in params["ids"]]
            )
        if query == "page content":
            return SimpleNamespace(
                data=[
                    {
                        "session_id": sid,
                        "first_message": f"first-{sid}",
                        "last_message": f"last-{sid}",
                    }
                    for sid in params["ids"]
                ]
            )
        if query == "page attributes":
            return SimpleNamespace(data=[])
        raise AssertionError(f"unexpected ClickHouse query: {query}")

    analytics.execute_ch_query.side_effect = _execute
    view._fetch_session_names = mock.MagicMock(return_value={})
    view._fetch_end_user_info = mock.MagicMock(return_value={})
    validated_data = {
        "filters": [
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": [user_id],
                },
            }
        ],
        "sort_params": [],
        "page_number": 0,
        "page_size": 1,
        "cursor_mode": True,
    }

    with (
        mock.patch(
            "tracer.views.trace_session.SessionListQueryBuilderV2",
            return_value=builder,
        ),
        mock.patch(
            "tracer.views.trace_session.read_bounded_filter_page"
        ) as bounded_read,
        mock.patch(
            "tracer.views.trace_session.AnnotationsLabels.objects.filter",
            return_value=[],
        ),
    ):
        first_status, first_payload = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=project_id,
            project=None,
            analytics=analytics,
            validated_data=validated_data,
        )
        cursor = first_payload["metadata"]["next_cursor"]
        second_status, second_payload = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=project_id,
            project=None,
            analytics=analytics,
            validated_data={**validated_data, "cursor": cursor},
        )

    assert first_status == second_status == "ok"
    assert [row["session_id"] for row in first_payload["table"]] == [newest_id]
    assert [row["session_id"] for row in second_payload["table"]] == [oldest_id]
    assert first_payload["metadata"] == {
        "total_rows": 2,
        "total_rows_exact": 2,
        "total_rows_is_lower_bound": False,
        "has_more": True,
        "next_cursor": cursor,
        "next_cursor_fingerprint": list_cursor_boundary_fingerprint(cursor),
        "query_complete": True,
        "query_status": "complete",
        "query_error_code": None,
        **applied_filter_attestation(
            project_id=project_id,
            observe_type="session",
            filters=validated_data["filters"],
        ),
    }
    assert second_payload["metadata"] == {
        "total_rows": 2,
        "total_rows_exact": 2,
        "total_rows_is_lower_bound": False,
        "has_more": False,
        "next_cursor": None,
        "next_cursor_fingerprint": None,
        "query_complete": True,
        "query_status": "complete",
        "query_error_code": None,
        **applied_filter_attestation(
            project_id=project_id,
            observe_type="session",
            filters=validated_data["filters"],
        ),
    }
    bounded_read.assert_not_called()
    first_call, second_call = builder.build_candidate_cursor_page_query.call_args_list
    assert first_call.kwargs == {
        "before_start_time": None,
        "before_session_id": None,
    }
    assert second_call.kwargs == {
        "before_start_time": newest_start,
        "before_session_id": newest_id,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "membership_filter", [_attribute_filter(), _has_eval_filter(False)]
)
def test_sparse_session_cursor_follows_checkpoint_without_skip_or_duplicate(
    membership_filter,
):
    from tracer.views.trace_session import TraceSessionView

    view, request = _view_and_request()
    request.query_params = {"cursor_mode": "true", "allow_sampled": "false"}
    project_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    window_start = datetime(2025, 8, 1, tzinfo=UTC)
    window_end = datetime(2026, 8, 1, tzinfo=UTC)
    checkpoint_end = window_end - timedelta(days=30)
    session_start = window_start + timedelta(days=2)

    builder = mock.MagicMock()
    builder.supports_candidate_first_page.return_value = True
    builder.supports_candidate_cursor_page.return_value = False
    builder.supports_bounded_filter_scan.return_value = True
    builder.recommended_filter_classify_batch_size.return_value = 50
    builder.parse_time_range.return_value = (window_start, window_end)
    builder.build_page_metrics_query.return_value = ("page metrics", {})
    builder.build_content_query.return_value = ("page content", {})
    builder.build_span_attributes_query.return_value = ("page attributes", {})
    builder.format_sessions.side_effect = lambda rows, columns: [
        dict(zip(columns, row, strict=True)) for row in rows
    ]
    analytics = mock.MagicMock()

    def _execute(query, _params, **_kwargs):
        if query == "page metrics":
            return SimpleNamespace(
                data=[
                    {
                        "session_id": session_id,
                        "session_start": session_start,
                        "session_end": session_start,
                        "duration": 0,
                        "total_cost": 0,
                        "total_tokens": 0,
                        "traces_count": 1,
                    }
                ]
            )
        if query == "page content":
            return SimpleNamespace(
                data=[
                    {
                        "session_id": session_id,
                        "first_message": "first",
                        "last_message": "last",
                    }
                ]
            )
        if query == "page attributes":
            return SimpleNamespace(data=[])
        raise AssertionError(f"unexpected ClickHouse query: {query}")

    analytics.execute_ch_query.side_effect = _execute
    view._fetch_session_names = mock.MagicMock(return_value={})
    view._fetch_end_user_info = mock.MagicMock(return_value={})
    first_page = _bounded_page(
        complete=False,
        error_code="deadline_exceeded",
        continuation_slice_end=checkpoint_end,
    )
    second_page = _bounded_page(
        rows=[{"session_id": session_id, "start_time": session_start}],
        complete=True,
        total_rows_lower_bound=1,
    )
    validated_data = {
        "filters": [membership_filter],
        "sort_params": [],
        "page_number": 0,
        "page_size": 1,
        "cursor_mode": True,
        "allow_sampled": False,
    }

    with (
        mock.patch(
            "tracer.views.trace_session.SessionListQueryBuilderV2",
            return_value=builder,
        ),
        mock.patch(
            "tracer.views.trace_session.read_bounded_filter_page",
            side_effect=[first_page, second_page],
        ) as bounded_read,
        mock.patch(
            "tracer.views.trace_session.AnnotationsLabels.objects.filter",
            return_value=[],
        ),
    ):
        first_status, first_payload = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=project_id,
            project=None,
            analytics=analytics,
            validated_data=validated_data,
        )
        cursor = first_payload["metadata"]["next_cursor"]
        second_status, second_payload = TraceSessionView._list_sessions_clickhouse(
            view,
            request,
            project_id=project_id,
            project=None,
            analytics=analytics,
            validated_data={**validated_data, "cursor": cursor},
        )

    assert first_status == "ok"
    assert first_payload["table"] == []
    assert first_payload["metadata"]["total_rows"] == 0
    assert first_payload["metadata"]["total_rows_is_lower_bound"] is True
    assert first_payload["metadata"]["has_more"] is True
    assert first_payload["metadata"]["query_complete"] is True
    assert first_payload["metadata"]["query_status"] == "complete"
    assert first_payload["metadata"]["query_error_code"] is None
    assert first_payload["metadata"]["query_exact"] is False
    assert first_payload["metadata"]["query_provenance"] == (
        "spans_per_session_candidate"
    )
    assert first_payload["metadata"]["ordering_exact"] is False
    assert isinstance(cursor, str)
    assert second_status == "ok"
    assert [row["session_id"] for row in second_payload["table"]] == [session_id]
    assert second_payload["metadata"]["has_more"] is False
    assert second_payload["metadata"]["next_cursor"] is None
    assert second_payload["metadata"]["total_rows_exact"] == 1
    assert bounded_read.call_count == 2
    assert all(
        "additional_table_filters" not in call.kwargs["read_settings"]
        for call in bounded_read.call_args_list
    )
    continuation = bounded_read.call_args_list[1].kwargs
    assert continuation["page_number"] == 0
    assert continuation["bounded_continuation"] is True
    assert continuation["include_incomplete_rows"] is True
    assert continuation["continuation_slice_end"] == checkpoint_end
