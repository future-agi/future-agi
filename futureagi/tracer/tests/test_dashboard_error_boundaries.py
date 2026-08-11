"""Regression coverage for dashboard ClickHouse error boundaries."""

import json
import uuid
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
    captured = {}
    workspace = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())

    def _pending(namespace, identity, **kwargs):
        captured.update(namespace=namespace, identity=identity)
        return kwargs["pending_payload"]

    with (
        patch(
            "tracer.views.dashboard._materialize_dashboard_query_scope",
            side_effect=lambda config, *_args, **_kwargs: config,
        ),
        patch(
            "tracer.views.dashboard.read_or_schedule_exact_snapshot",
            side_effect=_pending,
        ),
        patch("tracer.views.dashboard.read_exact_snapshot", return_value=None),
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(
            stored_query,
            workspace,
        )

    assert response.status_code == 200
    assert response.data["result"]["query_status"] == "pending"
    assert captured["namespace"] == "dashboard-query"
    normalized = captured["identity"]["query_config"]
    assert normalized["filters"] == [
        {
            "metric_type": "system_metric",
            "metric_name": "error_rate",
            "operator": "equal_to",
            "value": "32",
            "source": "traces",
        }
    ]
    assert normalized["metrics"][0]["filters"] == [
        {
            "metric_type": "system_metric",
            "metric_name": "input_tokens",
            "operator": "equal_to",
            "value": "32",
            "source": "traces",
        }
    ]
    assert stored_query == original_query


def test_legacy_numeric_operators_match_current_widget_cache_identity_without_write():
    project_id = uuid.uuid4()
    workspace = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    stored_queries = [
        _legacy_numeric_operator_query(project_id),
        _canonical_numeric_operator_query(project_id),
    ]
    captured_identities = []

    def _pending(_namespace, identity, **kwargs):
        captured_identities.append(identity)
        return kwargs["pending_payload"]

    with (
        patch(
            "tracer.views.dashboard._materialize_dashboard_query_scope",
            side_effect=lambda config, *_args, **_kwargs: config,
        ),
        patch(
            "tracer.views.dashboard.read_or_schedule_exact_snapshot",
            side_effect=_pending,
        ),
        patch("tracer.views.dashboard.read_exact_snapshot", return_value=None),
    ):
        responses = [
            DashboardWidgetViewSet()._execute_ch_query_config(query, workspace)
            for query in stored_queries
        ]

    assert [response.status_code for response in responses] == [200, 200]
    assert captured_identities[0] == captured_identities[1]
    normalized = captured_identities[0]["query_config"]
    assert normalized["filters"][0] == {
        "metric_type": "system_metric",
        "metric_name": "error_rate",
        "operator": "not_equal_to",
        "value": 0,
        "source": "traces",
    }
    assert normalized["metrics"][0]["filters"][0] == {
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
    v2_client.execute_read.return_value = ([], [], 1.0)

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
            side_effect=lambda _namespace, _identity, **kwargs: kwargs[
                "pending_payload"
            ],
        ) as exact_snapshot,
    ):
        response = auth_client.post(
            "/tracer/dashboard/query/",
            _trace_query(observe_project.id),
            format="json",
        )

    assert response.status_code == 200
    assert response.json()["result"]["query_status"] == "pending"
    assert not v2_client.execute_read.called
    v2_builder.assert_not_called()
    exact_snapshot.assert_called_once()
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
    v2_client.execute_read.return_value = ([], [], 1.0)

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
            side_effect=lambda _namespace, _identity, **kwargs: kwargs[
                "pending_payload"
            ],
        ) as exact_snapshot,
        patch("tracer.views.dashboard.read_exact_snapshot", return_value=None),
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
    assert response.json()["result"]["query_status"] == "pending"
    assert not v2_client.execute_read.called
    v2_builder.assert_not_called()
    exact_snapshot.assert_called_once()
    dispatch.assert_not_called()
    legacy_analytics.assert_not_called()
    legacy_client.assert_not_called()


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
    "failure",
    [
        ServerException("private missing-column query", code=47),
        RuntimeError("private dashboard compiler invariant"),
        ServerException("private timeout query", code=159),
    ],
)
@patch("tracer.views.dashboard.V2AnalyticsQueryService")
def test_dashboard_poll_defers_clickhouse_failures_to_exact_worker(
    mock_analytics_cls,
    failure,
    auth_client,
    observe_project,
):
    mock_analytics_cls.return_value.execute_ch_query.side_effect = failure

    with patch(
        "tracer.views.dashboard.read_or_schedule_exact_snapshot",
        side_effect=lambda _namespace, _identity, **kwargs: kwargs["pending_payload"],
    ):
        response = auth_client.post(
            "/tracer/dashboard/query/",
            _trace_query(observe_project.id),
            format="json",
        )

    assert response.status_code == 200
    assert response.json()["result"]["query_status"] == "pending"
    mock_analytics_cls.assert_not_called()
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
