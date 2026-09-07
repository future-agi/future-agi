"""Regression coverage for dashboard ClickHouse error boundaries."""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from clickhouse_driver.errors import ServerException

from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.views.dashboard import (
    DashboardReadQuerySerializer,
    DashboardViewSet,
    DashboardWidgetViewSet,
    _canonicalize_persisted_dashboard_query_filters_for_read,
)


@pytest.fixture
def dashboard(db, workspace, user):
    return Dashboard.objects.create(
        workspace=workspace,
        name="Boundary Dashboard",
        created_by=user,
        updated_by=user,
    )


@pytest.fixture
def dashboard_widget(db, dashboard, user):
    return DashboardWidget.objects.create(
        dashboard=dashboard,
        name="Boundary Widget",
        position=0,
        width=6,
        height=4,
        query_config={
            "project_ids": [str(uuid.uuid4())],
            "granularity": "day",
            "time_range": {"preset": "7D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "aggregation": "avg",
                }
            ],
        },
        chart_config={"chart_type": "line"},
        created_by=user,
    )


def _trace_query(project_id):
    return {
        "project_ids": [str(project_id)],
        "granularity": "day",
        "time_range": {"preset": "7D"},
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ],
    }


def _cold_dashboard_cache_miss(_namespace, _identity, **kwargs):
    """Model the cache-only probe state returned before any refresh is queued."""

    payload = dict(kwargs["pending_payload"])
    payload.update(query_refreshing=False, query_refresh_failed=False)
    return payload


def _legacy_filtered_trace_query(project_id):
    """Exact shape persisted by dashboard d0d98a25 before canonical filters."""

    return {
        "project_ids": [str(project_id)],
        "granularity": "day",
        "time_range": {"preset": "30D"},
        "filters": [
            {
                "value": "32",
                "source": "traces",
                "operator": "equal_to",
                "metric_name": "error_rate",
                "metric_type": "system_metric",
            }
        ],
        "metrics": [
            {
                "id": "error_rate",
                "name": "error_rate",
                "type": "system_metric",
                "source": "traces",
                "aggregation": "count",
                "filters": [
                    {
                        "value": "32",
                        "source": "traces",
                        "operator": "equal_to",
                        "metric_name": "input_tokens",
                        "metric_type": "system_metric",
                    }
                ],
            }
        ],
        "breakdowns": [],
    }


def _canonical_filtered_trace_query(project_id):
    query = _legacy_filtered_trace_query(project_id)
    query["filters"] = [
        {
            "column_id": "error_rate",
            "source": "traces",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "32",
                "col_type": "SYSTEM_METRIC",
            },
        }
    ]
    query["metrics"][0]["filters"] = [
        {
            "column_id": "input_tokens",
            "source": "traces",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "32",
                "col_type": "SYSTEM_METRIC",
            },
        }
    ]
    return query


def _legacy_numeric_operator_query(project_id):
    query = _legacy_filtered_trace_query(project_id)
    query["filters"][0].pop("value")
    query["filters"][0]["operator"] = "is_numeric"
    query["metrics"][0]["filters"][0].pop("value")
    query["metrics"][0]["filters"][0]["operator"] = "is_not_numeric"
    return query


def _canonical_numeric_operator_query(project_id):
    query = _legacy_filtered_trace_query(project_id)
    query["filters"] = [
        {
            "column_id": "error_rate",
            "source": "traces",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "not_equals",
                "filter_value": 0,
                "col_type": "SYSTEM_METRIC",
            },
        }
    ]
    query["metrics"][0]["filters"] = [
        {
            "column_id": "input_tokens",
            "source": "traces",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "equals",
                "filter_value": 0,
                "col_type": "SYSTEM_METRIC",
            },
        }
    ]
    return query


def _malformed_dashboard_collection_query(project_id, location, malformed_value):
    query = _trace_query(project_id)
    if location == "filters":
        query["filters"] = malformed_value
    elif location == "metrics":
        query["metrics"] = malformed_value
    else:
        query["metrics"][0]["filters"] = malformed_value
    return query


_MALFORMED_DASHBOARD_COLLECTION_VALUES = (
    None,
    {"private-internal-value": "must-not-leak"},
    "private-internal-value",
    17,
)


@pytest.mark.parametrize(
    "location",
    ("filters", "metrics", "metric_filters"),
)
@pytest.mark.parametrize(
    "malformed_value",
    _MALFORMED_DASHBOARD_COLLECTION_VALUES,
    ids=("null", "object", "string", "number"),
)
def test_dashboard_read_canonicalizer_preserves_malformed_collection_for_validation(
    location,
    malformed_value,
):
    query = _malformed_dashboard_collection_query(
        uuid.uuid4(), location, malformed_value
    )
    before = json.dumps(query, sort_keys=True)

    restored = _canonicalize_persisted_dashboard_query_filters_for_read(query)

    assert json.dumps(query, sort_keys=True) == before
    if location == "filters":
        assert restored["filters"] == malformed_value
    elif location == "metrics":
        assert restored["metrics"] == malformed_value
    else:
        assert restored["metrics"][0]["filters"] == malformed_value


@pytest.mark.parametrize(
    "location",
    ("filters", "metrics", "metric_filters"),
)
@pytest.mark.parametrize(
    "malformed_value",
    _MALFORMED_DASHBOARD_COLLECTION_VALUES,
    ids=("null", "object", "string", "number"),
)
def test_dashboard_read_serializer_rejects_malformed_collections_without_exception(
    location,
    malformed_value,
):
    query = _malformed_dashboard_collection_query(
        uuid.uuid4(), location, malformed_value
    )

    serializer = DashboardReadQuerySerializer(data=query)

    assert not serializer.is_valid()
    errors = json.dumps(serializer.errors).lower()
    assert "expected a list" in errors
    assert "private-internal-value" not in errors


@pytest.mark.django_db
@pytest.mark.parametrize(
    "location",
    ("filters", "metrics", "metric_filters"),
)
@pytest.mark.parametrize(
    "malformed_value",
    _MALFORMED_DASHBOARD_COLLECTION_VALUES,
    ids=("null", "object", "string", "number"),
)
def test_dashboard_query_rejects_malformed_collections_as_sanitized_400(
    auth_client,
    observe_project,
    location,
    malformed_value,
):
    query = _malformed_dashboard_collection_query(
        observe_project.id, location, malformed_value
    )

    with patch(
        "tracer.views.dashboard.read_or_schedule_exact_snapshot",
        side_effect=AssertionError("validation must stop before dashboard execution"),
    ):
        response = auth_client.post(
            "/tracer/dashboard/query/",
            query,
            format="json",
        )

    assert response.status_code == 400
    payload = json.dumps(response.json()).lower()
    assert response.json()["type"] == "validation_error"
    assert response.json()["code"] == "invalid"
    assert "expected a list" in payload
    assert "private-internal-value" not in payload
    assert "typeerror" not in payload
    assert "traceback" not in payload
    assert "internal server" not in payload


@pytest.mark.parametrize(
    "query_factory",
    (_legacy_filtered_trace_query, _canonical_filtered_trace_query),
    ids=("legacy-flattened", "current-canonical"),
)
def test_persisted_dashboard_filter_read_normalization_is_semantically_identical(
    query_factory,
):
    project_id = uuid.uuid4()
    query = query_factory(project_id)

    restored = _canonicalize_persisted_dashboard_query_filters_for_read(query)

    assert restored["filters"] == _canonical_filtered_trace_query(project_id)["filters"]
    assert (
        restored["metrics"][0]["filters"]
        == _canonical_filtered_trace_query(project_id)["metrics"][0]["filters"]
    )
    # Read compatibility must never rewrite the model's in-memory JSON value.
    assert query == query_factory(project_id)


def test_legacy_numeric_operators_normalize_without_mutating_persisted_query():
    project_id = uuid.uuid4()
    legacy_query = _legacy_numeric_operator_query(project_id)

    restored = _canonicalize_persisted_dashboard_query_filters_for_read(legacy_query)

    current_query = _canonical_numeric_operator_query(project_id)
    assert restored["filters"] == current_query["filters"]
    assert restored["metrics"][0]["filters"] == current_query["metrics"][0]["filters"]
    assert legacy_query == _legacy_numeric_operator_query(project_id)


@pytest.mark.parametrize(
    "query_factory",
    (_legacy_filtered_trace_query, _canonical_filtered_trace_query),
    ids=("legacy-flattened", "current-canonical"),
)
def test_widget_query_accepts_old_and_current_persisted_filter_shapes_without_write(
    query_factory,
):
    stored_query = query_factory(uuid.uuid4())
    original_query = query_factory(stored_query["project_ids"][0])
    workspace = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())

    with (
        patch(
            "tracer.views.dashboard._materialize_dashboard_query_scope",
            side_effect=lambda config, *_args, **_kwargs: config,
        ),
        patch(
            "tracer.views.dashboard._read_dashboard_rollup_fast_path",
            return_value=None,
        ),
        patch(
            "tracer.views.dashboard.DashboardQueryBuilderV2",
            wraps=DashboardQueryBuilderV2,
        ) as builder_cls,
        patch(
            "tracer.views.dashboard._fetch_exact_dashboard_rows",
            return_value=[],
        ),
        patch(
            "tracer.views.dashboard._project_queryset_for_dashboard_scope",
            return_value=MagicMock(
                filter=MagicMock(return_value=MagicMock(count=lambda: 1))
            ),
        ),
        patch(
            "tracer.views.dashboard.Project.objects.filter",
            return_value=MagicMock(values_list=MagicMock(return_value=[])),
        ),
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(
            stored_query,
            workspace,
        )

    assert response.status_code == 200
    assert response.data["result"]["query_status"] == "complete"
    normalized = next(
        call.args[0]
        for call in builder_cls.call_args_list
        if call.args[0].get("require_versioned_snapshot") is True
    )
    assert [
        {key: value for key, value in item.items() if key != "canonical_filter"}
        for item in normalized["filters"]
    ] == [
        {
            "metric_type": "system_metric",
            "metric_name": "error_rate",
            "operator": "equal_to",
            "value": "32",
            "source": "traces",
        }
    ]
    assert [
        {key: value for key, value in item.items() if key != "canonical_filter"}
        for item in normalized["metrics"][0]["filters"]
    ] == [
        {
            "metric_type": "system_metric",
            "metric_name": "input_tokens",
            "operator": "equal_to",
            "value": "32",
            "source": "traces",
        }
    ]
    assert stored_query == original_query


def test_legacy_numeric_operators_match_current_direct_query_without_write():
    project_id = uuid.uuid4()
    workspace = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    stored_queries = [
        _legacy_numeric_operator_query(project_id),
        _canonical_numeric_operator_query(project_id),
    ]
    with (
        patch(
            "tracer.views.dashboard._materialize_dashboard_query_scope",
            side_effect=lambda config, *_args, **_kwargs: config,
        ),
        patch(
            "tracer.views.dashboard._read_dashboard_rollup_fast_path",
            return_value=None,
        ),
        patch(
            "tracer.views.dashboard.DashboardQueryBuilderV2",
            wraps=DashboardQueryBuilderV2,
        ) as builder_cls,
        patch(
            "tracer.views.dashboard._fetch_exact_dashboard_rows",
            return_value=[],
        ),
        patch(
            "tracer.views.dashboard._project_queryset_for_dashboard_scope",
            return_value=MagicMock(
                filter=MagicMock(return_value=MagicMock(count=lambda: 1))
            ),
        ),
        patch(
            "tracer.views.dashboard.Project.objects.filter",
            return_value=MagicMock(values_list=MagicMock(return_value=[])),
        ),
    ):
        responses = [
            DashboardWidgetViewSet()._execute_ch_query_config(query, workspace)
            for query in stored_queries
        ]

    assert [response.status_code for response in responses] == [200, 200]
    normalized_configs = [
        call.args[0]
        for call in builder_cls.call_args_list
        if call.args[0].get("require_versioned_snapshot") is True
    ]
    assert len(normalized_configs) == 2
    assert normalized_configs[0]["filters"] == normalized_configs[1]["filters"]
    assert (
        normalized_configs[0]["metrics"][0]["filters"]
        == normalized_configs[1]["metrics"][0]["filters"]
    )
    normalized = normalized_configs[0]
    assert {
        key: value
        for key, value in normalized["filters"][0].items()
        if key != "canonical_filter"
    } == {
        "metric_type": "system_metric",
        "metric_name": "error_rate",
        "operator": "not_equal_to",
        "value": 0,
        "source": "traces",
    }
    assert {
        key: value
        for key, value in normalized["metrics"][0]["filters"][0].items()
        if key != "canonical_filter"
    } == {
        "metric_type": "system_metric",
        "metric_name": "input_tokens",
        "operator": "equal_to",
        "value": 0,
        "source": "traces",
    }
    assert stored_queries == [
        _legacy_numeric_operator_query(project_id),
        _canonical_numeric_operator_query(project_id),
    ]


def test_invalid_persisted_dashboard_filter_error_is_sanitized():
    query = _trace_query(uuid.uuid4())
    query["filters"] = [{"operator": "equal_to", "value": "private-value"}]

    response = DashboardWidgetViewSet()._execute_ch_query_config(
        query,
        SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4()),
    )

    assert response.status_code == 400
    payload = json.dumps(response.data)
    assert "private-value" not in payload
    assert "ErrorDetail" not in payload
    assert "Missing filter item keys" not in payload
    assert "Dashboard query configuration is invalid" in payload


DIRECT_WRITE_ROUTING_CONFIGS = (
    pytest.param({}, id="routing-missing"),
    pytest.param(
        {"QUERY_TYPES_DISABLED": "dashboard"},
        id="routing-disabled",
    ),
    pytest.param(
        {
            "QUERY_TYPES_V2_ONLY": "trace_list",
            "QUERY_TYPES_SHADOW": "dashboard",
        },
        id="routing-misconfigured-shadow",
    ),
)


@pytest.mark.django_db
@pytest.mark.parametrize("routing_config", DIRECT_WRITE_ROUTING_CONFIGS)
def test_dashboard_query_uses_direct_write_backend_independent_of_routing(
    routing_config,
    settings,
    auth_client,
    observe_project,
):
    settings.CLICKHOUSE_V2 = routing_config
    v2_client = MagicMock()
    v2_client.execute_read.return_value = (
        [(datetime(2026, 8, 1, tzinfo=UTC), 123.0)],
        [("time_bucket", "DateTime('UTC')"), ("metric_0", "Float64")],
        1.0,
    )
    v2_client.server_enforced_readonly = False
    v2_client.server_profile_locked = False

    with (
        patch(
            "tracer.services.clickhouse.v2.query_service.get_v2_query_client",
            return_value=v2_client,
        ),
        patch(
            "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
            side_effect=AssertionError("dashboard dispatch must not be consulted"),
        ) as dispatch,
        patch(
            "tracer.views.dashboard.AnalyticsQueryService",
            side_effect=AssertionError("legacy analytics must not be constructed"),
        ) as legacy_analytics,
        patch(
            "tracer.views.dashboard.DashboardQueryBuilderV2",
            wraps=DashboardQueryBuilderV2,
        ) as v2_builder,
        patch(
            "tracer.views.dashboard.read_or_schedule_exact_snapshot",
            side_effect=_cold_dashboard_cache_miss,
        ) as exact_snapshot,
    ):
        response = auth_client.post(
            "/tracer/dashboard/query/",
            _trace_query(observe_project.id),
            format="json",
        )

    assert response.status_code == 200
    assert response.json()["result"]["query_status"] == "complete"
    assert response.json()["result"]["query_provenance"] == "materialized_rollup"
    assert v2_client.execute_read.call_count == 1
    v2_builder.assert_called_once()
    exact_snapshot.assert_called_once()
    assert exact_snapshot.call_args.kwargs["schedule_on_miss"] is False
    dispatch.assert_not_called()
    legacy_analytics.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("routing_config", DIRECT_WRITE_ROUTING_CONFIGS)
@pytest.mark.parametrize("action", ("execute", "preview"))
def test_widget_trace_queries_use_direct_write_backend_independent_of_routing(
    action,
    routing_config,
    settings,
    auth_client,
    dashboard,
    dashboard_widget,
    observe_project,
):
    settings.CLICKHOUSE_V2 = routing_config
    query_config = _trace_query(observe_project.id)
    dashboard_widget.query_config = query_config
    dashboard_widget.save(update_fields=["query_config"])

    v2_client = MagicMock()
    v2_client.execute_read.return_value = (
        [(datetime(2026, 8, 1, tzinfo=UTC), 123.0)],
        [("time_bucket", "DateTime('UTC')"), ("metric_0", "Float64")],
        1.0,
    )
    v2_client.server_enforced_readonly = False
    v2_client.server_profile_locked = False

    with (
        patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True),
        patch(
            "tracer.services.clickhouse.v2.query_service.get_v2_query_client",
            return_value=v2_client,
        ),
        patch(
            "tracer.services.clickhouse.v2.dispatch.get_query_builder_class",
            side_effect=AssertionError("dashboard dispatch must not be consulted"),
        ) as dispatch,
        patch(
            "tracer.views.dashboard.AnalyticsQueryService",
            side_effect=AssertionError("legacy analytics must not be constructed"),
        ) as legacy_analytics,
        patch(
            "tracer.views.dashboard.get_clickhouse_client",
            side_effect=AssertionError("legacy client must not be constructed"),
        ) as legacy_client,
        patch(
            "tracer.views.dashboard.DashboardQueryBuilderV2",
            wraps=DashboardQueryBuilderV2,
        ) as v2_builder,
        patch(
            "tracer.views.dashboard.read_or_schedule_exact_snapshot",
            side_effect=_cold_dashboard_cache_miss,
        ) as exact_snapshot,
    ):
        if action == "execute":
            response = auth_client.post(
                f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/query/"
            )
        else:
            response = auth_client.post(
                f"/tracer/dashboard/{dashboard.id}/widgets/preview/",
                {"query_config": query_config},
                format="json",
            )

    assert response.status_code == 200
    assert response.json()["result"]["query_status"] == "complete"
    assert response.json()["result"]["query_provenance"] == "materialized_rollup"
    assert v2_client.execute_read.call_count == 1
    v2_builder.assert_called_once()
    exact_snapshot.assert_called_once()
    assert exact_snapshot.call_args.kwargs["schedule_on_miss"] is False
    dispatch.assert_not_called()
    legacy_analytics.assert_not_called()
    legacy_client.assert_not_called()


@pytest.mark.django_db
def test_dashboard_running_refresh_suppresses_duplicate_direct_read(
    auth_client,
    observe_project,
):
    pending = {
        "metrics": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
        "query_refreshing": True,
    }
    with (
        patch(
            "tracer.views.dashboard.read_or_schedule_exact_snapshot",
            return_value=pending,
        ) as cache_probe,
        patch(
            "tracer.views.dashboard._read_dashboard_rollup_fast_path",
            side_effect=AssertionError("an in-flight worker must suppress direct reads"),
        ),
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService",
            side_effect=AssertionError("an in-flight worker must suppress direct reads"),
        ),
    ):
        response = auth_client.post(
            "/tracer/dashboard/query/",
            _trace_query(observe_project.id),
            format="json",
        )

    assert response.status_code == 200
    assert response.json()["result"] == pending
    cache_probe.assert_called_once()
    assert cache_probe.call_args.kwargs["schedule_on_miss"] is False


@pytest.mark.django_db
def test_dashboard_completed_heavy_cache_hit_skips_clickhouse(
    auth_client,
    observe_project,
):
    completed = {
        "metrics": [
            {
                "id": "latency",
                "series": [{"time": "2026-08-01T00:00:00+00:00", "value": 123.0}],
                "query_complete": True,
                "query_status": "complete",
                "query_sampled": False,
            }
        ],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
        "query_exact": False,
        "query_provenance": "bounded_candidates",
        "query_cached": True,
    }
    with (
        patch(
            "tracer.views.dashboard.read_or_schedule_exact_snapshot",
            return_value=completed,
        ) as cache_probe,
        patch(
            "tracer.views.dashboard._read_dashboard_rollup_fast_path",
            side_effect=AssertionError("a complete cache hit must skip ClickHouse"),
        ),
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService",
            side_effect=AssertionError("a complete cache hit must skip ClickHouse"),
        ),
    ):
        response = auth_client.post(
            "/tracer/dashboard/query/",
            _trace_query(observe_project.id),
            format="json",
        )

    assert response.status_code == 200
    assert response.json()["result"] == completed
    cache_probe.assert_called_once()
    assert cache_probe.call_args.kwargs["schedule_on_miss"] is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    "failure",
    [
        ServerException("private missing-column query", code=47),
        RuntimeError("private dashboard compiler invariant"),
    ],
)
@patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
@patch("tracer.views.dashboard.V2AnalyticsQueryService")
def test_system_filter_values_programming_defects_preserve_sanitized_500(
    mock_analytics_cls,
    _mock_ch_enabled,
    failure,
    auth_client,
    observe_project,
):
    mock_analytics_cls.return_value.execute_ch_query.side_effect = failure

    response = auth_client.get(
        "/tracer/dashboard/filter_values/"
        "?metric_name=model&metric_type=system_metric"
        f"&project_ids={observe_project.id}&source=traces"
    )

    assert response.status_code == 500
    payload = json.dumps(response.json())
    assert "private" not in payload
    assert "missing-column" not in payload
    assert "compiler invariant" not in payload


@pytest.mark.django_db
@patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
@patch("tracer.views.dashboard.V2AnalyticsQueryService")
def test_system_filter_values_read_budget_is_sanitized_503(
    mock_analytics_cls,
    _mock_ch_enabled,
    auth_client,
    observe_project,
):
    mock_analytics_cls.return_value.execute_ch_query.side_effect = ServerException(
        "private timeout query", code=159
    )

    response = auth_client.get(
        "/tracer/dashboard/filter_values/"
        "?metric_name=model&metric_type=system_metric"
        f"&project_ids={observe_project.id}&source=traces"
    )

    assert response.status_code == 503
    payload = json.dumps(response.json())
    assert "temporarily unavailable" in payload
    assert "private" not in payload
    assert "timeout query" not in payload


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code", "expected_query_status"),
    [
        (
            ServerException("private missing-column query", code=47),
            400,
            None,
            None,
        ),
        (RuntimeError("private dashboard compiler invariant"), 400, None, None),
        (
            ServerException("private timeout query", code=159),
            200,
            None,
            "pending",
        ),
    ],
)
@patch("tracer.views.dashboard.V2AnalyticsQueryService")
def test_dashboard_query_sanitizes_direct_clickhouse_failures(
    mock_analytics_cls,
    failure,
    expected_status,
    expected_code,
    expected_query_status,
    auth_client,
    observe_project,
):
    mock_analytics_cls.return_value.execute_ch_query.side_effect = failure

    with patch(
        "tracer.views.dashboard._read_public_dashboard_query",
        return_value={
            "metrics": [],
            "query_complete": False,
            "query_status": "pending",
            "query_sampled": False,
            "query_refreshing": True,
        },
    ) as schedule_refresh:
        response = auth_client.post(
            "/tracer/dashboard/query/",
            _trace_query(observe_project.id),
            format="json",
        )

    assert response.status_code == expected_status
    if expected_code is not None:
        assert response.json()["code"] == expected_code
    if expected_query_status is not None:
        assert response.json()["result"]["query_status"] == expected_query_status
        schedule_refresh.assert_called_once()
    else:
        schedule_refresh.assert_not_called()
    assert mock_analytics_cls.return_value.execute_ch_query.call_count >= 1
    payload = json.dumps(response.json())
    assert "private" not in payload
    assert "missing-column" not in payload
    assert "compiler invariant" not in payload
    assert "timeout query" not in payload


def test_metric_query_programming_defect_propagates():
    builder = MagicMock()
    metric = {"name": "latency"}
    builder.metrics = [metric]
    builder.metric_info.return_value = {"name": "latency"}
    builder.build_metric_query.return_value = ("SELECT broken", {})

    def fail(_sql, _params):
        raise RuntimeError("dashboard compiler invariant")

    with pytest.raises(RuntimeError, match="compiler invariant"):
        DashboardViewSet._run_metric_queries(builder, "traces", fail)


@pytest.mark.django_db
@patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
@patch.object(DashboardWidgetViewSet, "_execute_ch_query_config")
def test_widget_query_programming_defect_preserves_sanitized_400(
    mock_execute,
    _mock_ch_enabled,
    auth_client,
    dashboard,
    dashboard_widget,
):
    mock_execute.side_effect = RuntimeError("private widget compiler invariant")

    response = auth_client.post(
        f"/tracer/dashboard/{dashboard.id}/widgets/{dashboard_widget.id}/query/"
    )

    assert response.status_code == 400
    assert "private" not in json.dumps(response.json())


@pytest.mark.django_db
@patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True)
@patch.object(DashboardWidgetViewSet, "_execute_ch_query_config")
def test_widget_preview_programming_defect_preserves_sanitized_400(
    mock_execute,
    _mock_ch_enabled,
    auth_client,
    dashboard,
    observe_project,
):
    mock_execute.side_effect = RuntimeError("private preview compiler invariant")

    response = auth_client.post(
        f"/tracer/dashboard/{dashboard.id}/widgets/preview/",
        {"query_config": _trace_query(observe_project.id)},
        format="json",
    )

    assert response.status_code == 400
    assert "private" not in json.dumps(response.json())
