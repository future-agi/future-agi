"""RT-05/10 query and response contracts. No database/provider calls."""

from contextlib import nullcontext
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.test import override_settings

from tracer.services.clickhouse.query_builders.eval_metrics import (
    EvalMetricsQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.eval_metrics import (
    EvalMetricsQueryBuilderV2,
)

START = datetime(2026, 10, 1)
PROJECT = "00000000-0000-0000-0000-000000000011"
CONFIG = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def lifecycle_capable_logger():
    with override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger"):
        yield


def builder(cls=EvalMetricsQueryBuilder, kind="SCORE", **kwargs):
    return cls(
        custom_eval_config_id=CONFIG,
        project_id=PROJECT,
        start_date=START,
        end_date=START + timedelta(hours=2),
        eval_output_type=kind,
        choices=["yes", "no"],
        **kwargs,
    )


@pytest.mark.parametrize("cls", [EvalMetricsQueryBuilder, EvalMetricsQueryBuilderV2])
@pytest.mark.parametrize("kind", ["SCORE", "PASS_FAIL", "CHOICES"])
@pytest.mark.parametrize("table", ["tracer_eval_logger", "tracer_eval_logger_v2"])
def test_rt05_rt10_always_latest_raw_scoped_bounded(cls, kind, table):
    with override_settings(CH25_EVAL_LOGGER_TABLE=table):
        if table.endswith("_v2"):
            # The checked-in direct-write schema has no lifecycle columns;
            # never fabricate completed status or silently read another source.
            from tracer.services.clickhouse.exact_graph_reads import ExactGraphReadError

            with pytest.raises(
                ExactGraphReadError, match="lifecycle columns"
            ) as failure:
                builder(cls, kind, use_preaggregated=True).build()
            assert failure.value.error_code == "eval_lifecycle_unavailable"
            return
        query, params = builder(cls, kind, use_preaggregated=True).build()
    assert "eval_metrics_hourly" not in query
    assert f"{table}" in query and "FINAL" in query
    # Version collapse is inside a subquery; state/tombstone guards outside it.
    assert "SELECT * FROM" in query
    assert query.index("FINAL") < query.index("WHERE")
    assert "skipped_reason" in query and "output_str" in query and "status" in query
    assert (
        "error_count" in query and "pending_count" in query and "running_count" in query
    )
    assert "skipped_count" in query and "scored_count" in query
    assert "nullIf" in query and "greatest(" not in query
    assert "%(project_id)s" in query
    assert "trace_session" in query and "target_type" in query
    assert "%(start_date)s" in query and "%(end_date)s" in query
    assert params["project_id"] == PROJECT
    assert params["eval_config_id"] == CONFIG
    if table.endswith("_v2"):
        assert "raw_eval_logger.is_deleted" in query
    else:
        assert "raw_eval_logger._peerdb_is_deleted" in query
        assert "raw_eval_logger.deleted" in query


@pytest.mark.parametrize("kind", ["SCORE", "PASS_FAIL", "CHOICES"])
def test_rt05_format_null_zero_and_counts(kind):
    obj = builder(kind=kind)
    col = "choice_0" if kind == "CHOICES" else "value"
    cols = [
        "time_bucket",
        col,
        "error_count",
        "pending_count",
        "running_count",
        "skipped_count",
        "scored_count",
        "unscored_count",
    ]
    result = obj.format_result([(START, None, 2, 1, 1, 1, 0, 5)], cols)
    series = result[0] if isinstance(result, list) else result
    assert series["data"][0]["value"] is None
    assert series["data"][0]["error_count"] == 2
    assert series["data"][1]["value"] is None
    assert series["data"][1]["error_count"] == 0
    result = obj.format_result([(START, 0, 0, 0, 0, 0, 1, 0)], cols)
    series = result[0] if isinstance(result, list) else result
    assert series["data"][0]["value"] == 0


def test_rt05_error_rate_denominator_excludes_skipped_and_inflight():
    query, _ = builder().build()
    assert "error_rate" in query
    assert "completed_count" in query
    assert "rate_limited" in query
    assert "insufficient_credits" in query


def test_rt05_old_cached_zero_payloads_have_a_different_namespace(monkeypatch):
    from tracer.services.clickhouse import graph_dispatch

    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(graph_dispatch, "_read_or_refresh_exact_graph", capture)
    graph_dispatch.fetch_eval_chart_series_ch(
        analytics=None,
        project_id=PROJECT,
        filters=[],
        interval="hour",
        req_data_config={"id": CONFIG},
        eval_name="quality",
    )
    assert captured["namespace"] == "observe-eval-chart-series-nk20-v1"


def test_rt05_serializer_preserves_null_and_lifecycle_counts():
    from tracer.serializers.filters import ObserveGraphDataPointSerializer

    point = {
        "timestamp": START.isoformat(),
        "value": None,
        "error_count": 2,
        "pending_count": 1,
        "running_count": 1,
        "skipped_count": 1,
        "scored_count": 0,
        "unscored_count": 5,
        "completed_count": 0,
        "count": 5,
        "failed_count": 0,
        "error_rate": 100,
    }
    serializer = ObserveGraphDataPointSerializer(data=point)
    assert serializer.is_valid(), serializer.errors
    assert serializer.data == point


@pytest.fixture
def exact_eval_reader(monkeypatch):
    """Run the real reader/builder, replacing only I/O and window resolution."""
    from tracer.services.clickhouse import exact_graph_reads as reads

    config = SimpleNamespace(
        name="quality",
        eval_template=SimpleNamespace(
            config={"output": "choices"}, choices=["yes", "no"]
        ),
    )
    configs = Mock()
    configs.get.return_value = config
    monkeypatch.setattr(
        reads.CustomEvalConfig.objects, "select_related", lambda *_: configs
    )
    monkeypatch.setattr(reads, "application_postgres_reads", lambda **_: nullcontext())
    monkeypatch.setattr(
        reads, "_snapshot_window", lambda _: (START, START + timedelta(hours=2), False)
    )
    monkeypatch.setattr(reads, "_annotation_label_ids_for_filters", lambda *_: ())
    query = Mock(return_value=SimpleNamespace(data=[], columns=[]))
    monkeypatch.setattr(reads, "_execute_direct_exact_graph_query", query)
    return reads, query


@pytest.mark.parametrize("requested_choices", [None, ["yes"]])
@pytest.mark.parametrize("selected", [None, "yes"])
def test_rt05_selected_choice_retains_full_configured_vocabulary(
    exact_eval_reader, requested_choices, selected
):
    reads, query = exact_eval_reader
    result = reads.read_exact_eval_graph(
        analytics=object(),
        project_id=PROJECT,
        filters=[],
        interval="hour",
        req_data_config={"id": CONFIG, "value": selected, "choices": requested_choices},
        observe_type="trace",
    )
    sql = query.call_args.kwargs["query"]
    params = query.call_args.kwargs["params"]
    assert params["valid_choices"] == ["yes", "no"]
    assert params["choice_0"] == "yes"
    assert "choice_1" not in params
    assert "has(%(valid_choices)s, ifNull(output_str, ''))" in sql
    assert "nullIf(countIf(nk_valid), 0)" in sql
    assert result["query_complete"] is True


@pytest.mark.parametrize("empty", [False, True])
def test_rt05_v2_sync_read_is_degraded_without_a_number(
    monkeypatch, exact_eval_reader, empty
):
    from tracer.services.clickhouse import graph_dispatch

    reads, query = exact_eval_reader
    monkeypatch.setattr(
        reads, "_snapshot_window", lambda _: (START, START + timedelta(hours=2), empty)
    )
    monkeypatch.setattr(
        graph_dispatch, "_affordable_raw_log_membership_read", lambda **_: None
    )
    with override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2"):
        result = graph_dispatch.fetch_eval_graph_ch(
            analytics=object(),
            project_id=PROJECT,
            filters=[],
            interval="hour",
            req_data_config={"id": CONFIG},
        )
    assert result["query_status"] == "degraded"
    assert result["query_complete"] is False
    assert result["query_exact"] is False
    assert result["query_error_code"] == "eval_lifecycle_unavailable"
    assert result["data"] == []
    assert "value" not in result
    query.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "namespace", ["observe-eval-graph-nk20-v1", "observe-eval-chart-series-nk20-v1"]
)
@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
def test_rt05_v2_async_refresh_records_failure_without_publishing(
    monkeypatch, exact_eval_reader, namespace
):
    from django.core.cache import cache

    from tracer.services import exact_aggregation_cache as cache_module
    from tracer.services.exact_aggregation_cache import (
        begin_exact_refresh,
        exact_refresh_state,
        normalize_exact_observe_identity,
        read_exact_snapshot,
        read_or_schedule_exact_snapshot,
    )
    from tracer.tasks import exact_aggregation as task_module

    _, query = exact_eval_reader
    cache.clear()
    # Freeze the default window once so the worker and polling reader address
    # the same digest. A raw identity here would turn the poll into a new job.
    identity = normalize_exact_observe_identity(
        {
            "project_id": PROJECT,
            "filters": [],
            "interval": "hour",
            "req_data_config": {"id": CONFIG},
            "observe_type": "trace",
        }
    )
    enqueue = Mock(
        side_effect=lambda *a, **kw: pytest.fail("unexpected refresh enqueue")
    )
    monkeypatch.setattr(cache_module, "_enqueue_exact_refresh", enqueue)
    monkeypatch.setattr(
        task_module, "_reauthorize_exact_observe_project", lambda _: None
    )
    monkeypatch.setattr(
        task_module, "_exact_observe_analytics", lambda: nullcontext(object())
    )
    token = begin_exact_refresh(namespace, identity)
    assert token
    with override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2"):
        with pytest.raises(RuntimeError, match="exact aggregation refresh failed"):
            task_module.refresh_exact_aggregation_snapshot.run_sync(
                namespace=namespace, identity=identity, refresh_token=token
            )
    assert exact_refresh_state(namespace, identity) == "failed"
    assert read_exact_snapshot(namespace, identity) is None
    failed = read_or_schedule_exact_snapshot(
        namespace,
        identity,
        refresh=False,
        pending_payload={
            "data": [],
            "query_complete": False,
            "query_status": "pending",
        },
    )
    assert failed["query_refresh_failed"] is True
    assert failed["query_refreshing"] is False
    assert failed["query_complete"] is False
    assert failed["data"] == []
    assert "value" not in failed
    enqueue.assert_not_called()
    query.assert_not_called()


@pytest.mark.parametrize("envelope", [False, True])
def test_rt05_lifecycle_error_is_in_graph_response_contract(envelope):
    from tracer.serializers.filters import (
        ObserveGraphDataErrorResponseSerializer,
        ObserveGraphDataResultSerializer,
    )

    payload = {
        "metric_name": CONFIG,
        "data": [],
        "query_status": "degraded",
        "query_complete": False,
        "query_exact": False,
        "query_error_code": "eval_lifecycle_unavailable",
    }
    if envelope:
        payload["message"] = "Graph data is temporarily unavailable. Please retry."
        serializer = ObserveGraphDataErrorResponseSerializer(
            data={"status": False, "code": "service_unavailable", "result": payload}
        )
    else:
        serializer = ObserveGraphDataResultSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    result = serializer.data["result"] if envelope else serializer.data
    assert result["query_error_code"] == "eval_lifecycle_unavailable"
    assert result["data"] == []


@pytest.mark.parametrize("surface", ["trace", "span", "user"])
def test_rt05_v2_graph_views_preserve_error_code_and_log_ids(
    monkeypatch, exact_eval_reader, surface
):
    from importlib import import_module
    from inspect import unwrap

    from tracer.serializers.filters import ObserveGraphDataErrorResponseSerializer
    from tracer.services import exact_aggregation_cache as cache_module
    from tracer.services.clickhouse import graph_dispatch

    module_name, class_name, method_name, event = {
        "trace": ("trace", "TraceView", "get_graph_methods", "trace_graph_degraded"),
        "span": (
            "observation_span",
            "ObservationSpanView",
            "get_graph_methods",
            "span_graph_degraded",
        ),
        "user": (
            "project",
            "ProjectView",
            "get_users_aggregate_graph_data",
            "project_users_graph_degraded",
        ),
    }[surface]
    module = import_module(f"tracer.views.{module_name}")
    view = getattr(module, class_name)()
    project = SimpleNamespace(trace_type="observe", organization_id=PROJECT)
    projects = Mock()
    projects.filter.return_value.first.return_value = project
    if surface == "trace":
        monkeypatch.setattr(module, "_project_queryset_for_request", lambda _: projects)
    elif surface == "span":
        monkeypatch.setattr(module.Project.objects, "get", Mock(return_value=project))
    else:
        monkeypatch.setattr(view, "_get_project_in_scope", lambda _: project)
    configs = Mock()
    configs.exists.return_value = True
    monkeypatch.setattr(module.CustomEvalConfig.objects, "filter", lambda **_: configs)
    monkeypatch.setattr(module, "graph_action_postgres_budget", lambda _: nullcontext())
    monkeypatch.setattr(module, "V2AnalyticsQueryService", lambda: object())
    monkeypatch.setattr(
        graph_dispatch, "_affordable_raw_log_membership_read", lambda **_: None
    )
    enqueue = Mock(
        side_effect=lambda *a, **kw: pytest.fail("unexpected refresh enqueue")
    )
    monkeypatch.setattr(cache_module, "_enqueue_exact_refresh", enqueue)
    logger = Mock()
    monkeypatch.setattr(module, "logger", logger)
    # The real view -> dispatcher -> reader -> v2 builder must fail closed.
    # Only authorization metadata and external I/O are replaced by fixtures.
    request = SimpleNamespace(
        validated_query_data={"refresh": False},
        validated_data={
            "project_id": PROJECT,
            "filters": [],
            "property": "eval",
            "interval": "hour",
            "req_data_config": {
                "id": CONFIG,
                "type": "EVAL",
                "value": "private-choice",
            },
        },
        user=SimpleNamespace(id=PROJECT, organization=SimpleNamespace(id=PROJECT)),
    )
    view.request = request
    with override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2"):
        response = unwrap(getattr(type(view), method_name))(view, request)
    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    result = response.data["result"]
    assert result["query_error_code"] == "eval_lifecycle_unavailable"
    assert result["query_complete"] is False
    assert result["query_exact"] is False
    assert result["data"] == []
    assert "value" not in result
    prefix = "User graph" if surface == "user" else "Graph"
    assert (
        result["message"] == f"{prefix} data is temporarily unavailable. Please retry."
    )
    serializer = ObserveGraphDataErrorResponseSerializer(data=response.data)
    assert serializer.is_valid(), serializer.errors
    logger.warning.assert_called_once_with(
        event,
        query_error_code="eval_lifecycle_unavailable",
        project_id=PROJECT,
        eval_config_id=CONFIG,
    )
    logger.error.assert_not_called()
    logger.exception.assert_not_called()
    enqueue.assert_not_called()
    exact_eval_reader[1].assert_not_called()
