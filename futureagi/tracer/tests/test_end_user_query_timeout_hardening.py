from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from clickhouse_driver.errors import ErrorCodes, ServerException

from tracer.services.clickhouse.query_builders.user_time_series import (
    UserTimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.v2 import end_user_dict_reader
from tracer.services.users_list_manager import (
    MAX_EXPORT_ROWS,
    UsersListManager,
    _users_attr_enrichment_query,
)
from tracer.views.trace_session import TraceSessionView


def _date_filters():
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    "2026-07-01T00:00:00Z",
                    "2026-07-15T00:00:00Z",
                ],
            },
        }
    ]


def test_users_attribute_enrichment_sql_requires_project_and_time_scope():
    project_id = str(uuid4())

    query, params = _users_attr_enrichment_query([project_id])

    assert "project_id IN %(attr_project_ids)s" in query
    assert "start_time >= %(attr_start_date)s" in query
    assert "start_time < %(attr_end_date)s" in query
    assert "end_user_id_remap" in query
    assert params["attr_project_ids"] == (project_id,)


def test_users_attribute_enrichment_rejects_empty_tenant_scope():
    try:
        _users_attr_enrichment_query([])
    except ValueError as exc:
        assert "non-empty project scope" in str(exc)
    else:
        raise AssertionError("empty project scope must not produce an unscoped query")


def test_users_list_and_export_reads_use_bounded_clickhouse_settings():
    project_id = str(uuid4())
    manager = UsersListManager(
        organization_id=str(uuid4()),
        allowed_project_ids=[project_id],
        project_id=project_id,
    )
    builder = MagicMock()
    builder.build.return_value = ("SELECT 1", {})
    builder.format_rows.return_value = {"table": [], "total_count": 0}
    analytics = MagicMock()
    analytics.execute_ch_query.return_value = SimpleNamespace(data=[])

    with (
        patch(
            "tracer.services.users_list_manager.UserListQueryBuilderV2",
            return_value=builder,
        ),
        patch(
            "tracer.services.users_list_manager.AnalyticsQueryService",
            return_value=analytics,
        ),
    ):
        manager._fetch_rows(limit=30, offset=0)
        list_kwargs = analytics.execute_ch_query.call_args.kwargs
        assert list_kwargs["timeout_ms"] == 750
        assert list_kwargs["settings"]["max_threads"] == 2
        assert list_kwargs["settings"]["max_bytes_to_read"] == 1_073_741_824
        assert list_kwargs["settings"]["max_result_rows"] == 2000

        manager._fetch_rows(
            limit=None,
            offset=None,
            max_rows=MAX_EXPORT_ROWS + 1,
        )
        export_kwargs = analytics.execute_ch_query.call_args.kwargs
        assert export_kwargs["timeout_ms"] == 750
        assert export_kwargs["settings"]["max_result_rows"] == MAX_EXPORT_ROWS + 1


def test_users_attribute_enrichment_binds_requested_window_and_read_cap():
    project_id = str(uuid4())
    end_user_id = str(uuid4())
    manager = UsersListManager(
        organization_id=str(uuid4()),
        allowed_project_ids=[project_id],
        project_id=project_id,
        filters=_date_filters(),
    )
    builder = MagicMock()
    start = datetime(2026, 7, 1)
    end = datetime(2026, 7, 15)
    builder.parse_time_range.return_value = (start, end)
    analytics = MagicMock()
    analytics.execute_ch_query.return_value = SimpleNamespace(data=[])

    with patch(
        "tracer.services.users_list_manager.AnalyticsQueryService",
        return_value=analytics,
    ):
        manager._enrich_with_span_attributes(
            [{"end_user_id": end_user_id}],
            builder,
        )

    query, params = analytics.execute_ch_query.call_args.args[:2]
    kwargs = analytics.execute_ch_query.call_args.kwargs
    assert "project_id IN %(attr_project_ids)s" in query
    assert params["attr_project_ids"] == (project_id,)
    assert params["attr_start_date"] == start
    assert params["attr_end_date"] == end
    assert kwargs["timeout_ms"] == 750
    assert kwargs["settings"]["read_overflow_mode"] == "throw"


def test_end_user_dictionary_reads_have_server_side_deadline_and_caps():
    project_id = str(uuid4())
    client = MagicMock()
    client.query.return_value = SimpleNamespace(result_rows=[])

    with patch.object(end_user_dict_reader, "_get_client", return_value=client):
        assert (
            end_user_dict_reader.resolve_end_user_ids_by_user_id(
                "customer-1",
                project_id=project_id,
            )
            == []
        )

    kwargs = client.query.call_args.kwargs
    assert kwargs["parameters"]["pid"] == project_id
    assert kwargs["settings"]["max_execution_time"] == 0.75
    assert kwargs["settings"]["max_bytes_to_read"] == 1_073_741_824
    assert kwargs["settings"]["result_overflow_mode"] == "throw"


def test_session_detail_and_remap_reads_share_bounded_settings():
    project_id = str(uuid4())
    session_id = str(uuid4())
    analytics = MagicMock()
    analytics.execute_ch_query.side_effect = [
        SimpleNamespace(data=[]),  # canonical remap
        SimpleNamespace(data=[]),  # group expansion
        SimpleNamespace(data=[]),  # session aggregate
        SimpleNamespace(data=[]),  # trace page
    ]

    with patch(
        "tracer.views.trace_session.get_session_navigation",
        return_value=(None, None),
    ):
        response = TraceSessionView()._retrieve_clickhouse(
            MagicMock(),
            session_id,
            project_id,
            analytics,
            {"page_number": 0, "page_size": 30},
        )

    assert response.status_code == 200
    assert len(analytics.execute_ch_query.call_args_list) == 4
    for call in analytics.execute_ch_query.call_args_list:
        assert call.kwargs["timeout_ms"] == 750
        assert call.kwargs["settings"]["max_threads"] == 2
        assert call.kwargs["settings"]["max_bytes_to_read"] == 1_073_741_824


def test_user_time_series_filter_subqueries_use_v2_tenant_scope():
    project_id = str(uuid4())
    builder = UserTimeSeriesQueryBuilder(
        project_id=project_id,
        filters=[
            *_date_filters(),
            {
                "column_id": "status",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "ERROR",
                },
            },
        ],
        interval="day",
    )

    query, params = builder.build()

    assert "project_id = %(project_id)s" in query
    assert "is_deleted = 0" in query
    assert "_peerdb_is_deleted" not in query
    assert params["project_id"] == project_id
    assert params["start_date"] == datetime(2026, 7, 1)
    assert params["end_date"] == datetime(2026, 7, 15)


def _timeout():
    return ServerException(
        "Code: 159. private-cluster-host exceeded execution time",
        code=ErrorCodes.TIMEOUT_EXCEEDED,
    )


@pytest.mark.django_db
def test_project_aggregate_user_graph_timeout_is_sanitized_degraded_200(
    auth_client,
    observe_project,
):
    analytics = MagicMock()
    analytics.execute_ch_query.side_effect = _timeout()

    with patch(
        "tracer.views.project.AnalyticsQueryService",
        return_value=analytics,
    ):
        response = auth_client.post(
            "/tracer/project/get_users_aggregate_graph_data/",
            {
                "project_id": str(observe_project.id),
                "interval": "day",
                "filters": _date_filters(),
                "req_data_config": {
                    "id": "active_users",
                    "type": "SYSTEM_METRIC",
                },
            },
            format="json",
        )

    assert response.status_code == 200
    assert response.json()["result"] == {
        "metric_name": "active_users",
        "data": [],
        "query_complete": False,
        "query_status": "degraded",
        "query_error_code": "read_budget_exceeded",
    }
    assert "private-cluster-host" not in response.content.decode()


@pytest.mark.django_db
def test_project_user_detail_graph_timeout_preserves_series_shape(
    auth_client,
    observe_project,
):
    analytics = MagicMock()
    analytics.execute_ch_query.side_effect = _timeout()

    with patch(
        "tracer.views.project.AnalyticsQueryService",
        return_value=analytics,
    ):
        response = auth_client.post(
            "/tracer/project/get_user_graph_data/"
            f"?project_id={observe_project.id}&end_user_id={uuid4()}",
            {
                "interval": "day",
                "filters": _date_filters(),
            },
            format="json",
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["session"] == []
    assert result["trace"] == []
    assert result["query_status"] == "degraded"
    assert result["query_error_code"] == "read_budget_exceeded"
    assert "private-cluster-host" not in response.content.decode()


@pytest.mark.django_db
def test_session_graph_timeout_is_sanitized_degraded_200(
    auth_client,
    observe_project,
):
    analytics = MagicMock()
    analytics.execute_ch_query.side_effect = _timeout()

    with patch(
        "tracer.services.clickhouse.query_service.AnalyticsQueryService",
        return_value=analytics,
    ):
        response = auth_client.post(
            "/tracer/trace-session/get_session_graph_data/",
            {
                "project_id": str(observe_project.id),
                "interval": "day",
                "filters": _date_filters(),
                "req_data_config": {
                    "id": "session_count",
                    "type": "SYSTEM_METRIC",
                },
            },
            format="json",
        )

    assert response.status_code == 200
    assert response.json()["result"]["query_status"] == "degraded"
    assert response.json()["result"]["query_error_code"] == "read_budget_exceeded"
    assert "private-cluster-host" not in response.content.decode()


@pytest.mark.django_db
def test_users_list_error_does_not_expose_clickhouse_exception(
    auth_client,
    observe_project,
):
    analytics = MagicMock()
    analytics.execute_ch_query.side_effect = RuntimeError(
        "Code: 159. private-cluster-host exceeded execution time"
    )

    with patch(
        "tracer.services.users_list_manager.AnalyticsQueryService",
        return_value=analytics,
    ):
        response = auth_client.get(
            "/tracer/users/",
            {"project_id": str(observe_project.id)},
        )

    assert response.status_code == 400
    assert response.json()["result"] == "error fetching users"
    assert "private-cluster-host" not in response.content.decode()
