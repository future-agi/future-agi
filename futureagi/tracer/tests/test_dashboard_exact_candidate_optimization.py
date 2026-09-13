"""Offline dashboard SQL/dispatch regressions, not ClickHouse/SLO qualification.

Values and aggregates are synthetic. Native parity uses disposable in-process
chdb tables only; no network services, authentication, rollups, or production data.
"""

import json
import socket
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from clickhouse_driver.errors import ServerException
from clickhouse_driver.util.escape import escape_params
from django.core.cache.backends.locmem import LocMemCache

from tracer.services import exact_aggregation_cache
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.views import dashboard as view
from tracer.views.dashboard import (
    DashboardExactReadError,
    DashboardWidgetViewSet,
    _normalize_dashboard_query_filters,
)

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
ORGANIZATION = "33333333-3333-4333-8333-333333333333"
END = datetime(2026, 9, 4, 12, tzinfo=UTC)
DRIVER_CONTEXT = SimpleNamespace(
    server_info=SimpleNamespace(get_timezone=lambda: "UTC")
)


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    """Disable the repository's integration DDL for this offline suite."""
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    """Disable the repository's integration DDL for this offline suite."""
    yield


@pytest.fixture(autouse=True)
def _offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline dashboard test attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def _filter(key, values):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": values,
            "attribute_value_types": ["string"] * len(values),
        },
    }


def _config(*, days=30, filtered=True, breakdown="measurement", aggregations=("avg",)):
    return {
        "project_ids": [PROJECT],
        "time_range": {
            "custom_start": (END - timedelta(days=days)).isoformat(),
            "custom_end": END.isoformat(),
        },
        "granularity": "day",
        "metrics": [
            {
                "id": f"latency_ms_{aggregation}",
                "name": "latency_ms",
                "type": "custom_attribute",
                "source": "traces",
                "attribute_key": "latency_ms",
                "attribute_type": "number",
                "aggregation": aggregation,
            }
            for aggregation in aggregations
        ],
        "filters": [
            _filter("company_id", [str(10000001 + index) for index in range(42)]),
            _filter("prompt_slug", [f"fixture_prompt_{index}" for index in range(7)]),
        ]
        if filtered
        else [],
        "breakdowns": [
            {
                "name": breakdown,
                "type": "custom_attribute",
                "source": "traces",
                "attribute_type": "string",
            }
        ],
    }


def _builder(config):
    builder = DashboardQueryBuilderV2(_normalize_dashboard_query_filters(config))
    builder._latest_state_spans_required = True
    return builder


def _assert_scalar_replay(
    sql,
    params,
    *,
    metric_key,
    breakdown_key=None,
    breakdown_map="attrs_string",
    filtered=False,
):
    _, replay_and_live = sql.split("WITH latest_custom_metric_spans AS (", 1)
    replay, live = replay_and_live.split("), live_custom_metric_spans AS (", 1)
    replay_prewhere = replay.split("PREWHERE", 1)[1]
    compact = " ".join(replay_prewhere.split())
    assert "groupArray(" not in sql and "argMax(" not in sql
    assert "custom_metric_source.is_deleted" in replay
    assert "FROM spans AS custom_metric_source FINAL" in replay
    assert sql.count("FROM spans AS custom_metric_source") == 1
    assert "custom_metric_candidate" not in sql
    assert "attrs_" not in replay_prewhere
    assert "is_deleted" not in replay_prewhere
    assert "custom_metric_source.project_id IN %(project_ids)s" in replay_prewhere
    assert "GROUP BY" not in replay
    assert "toStartOfHour(custom_metric_source.start_time) >=" in compact
    assert "toStartOfHour(custom_metric_source.start_time) <" in compact
    assert ">= toStartOfHour(toDateTime64( %(start_date)s, 6, 'UTC' ))" in compact
    assert (
        "< toStartOfHour(toDateTime64( %(end_date)s, 6, 'UTC' )) + INTERVAL 1 HOUR"
        in compact
    )
    assert "tupleElement(latest_metric_state, 1) = 0" in live
    assert "ARRAY JOIN [metric_winner] AS latest_metric_state" in live
    assert "optimize_move_to_prewhere=0, optimize_move_to_prewhere_if_final=0" in sql
    assert (
        "enable_optimize_predicate_expression=1, enable_optimize_predicate_expression_to_final_subquery=1"
        in sql
    )
    assert "tupleElement(latest_metric_state, 3) = 1" in live
    assert "tupleElement(latest_metric_state, 2) >= %(start_date)s" in live
    assert "tupleElement(latest_metric_state, 2) < %(end_date)s" in live
    assert "tupleElement(latest_metric_state, 4) AS metric_value" in live
    assert params["custom_metric_attr_key"] == metric_key
    if breakdown_key is not None:
        assert params["_custom_bd_key_0"] == breakdown_key
        assert (
            f"mapContains(custom_metric_source.{breakdown_map}, %(_custom_bd_key_0)s)"
            in replay
        )
        assert "tupleElement(latest_metric_state, 5) = 1" in live
        assert "tupleElement(latest_metric_state, 6) AS breakdown_value" in live
    else:
        assert "breakdown_value" not in sql
    assert "SELECT *" not in sql and ".*" not in sql
    assert "ORDER BY custom_metric_source._version" not in sql
    assert "LIMIT " not in sql
    assert "parent_span_id" not in sql
    assert "remap" not in sql
    assert not any(key.startswith("dashboard_candidate") for key in params)
    assert "%(" not in sql % escape_params(params, context=DRIVER_CONTEXT)


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("breakdown", ["measurement", "session.id"])
@pytest.mark.parametrize("filtered", [False, True])
def test_custom_key_candidates_replay_full_latest_identity(days, breakdown, filtered):
    config = _config(days=days, breakdown=breakdown, filtered=filtered)
    original = deepcopy(config)
    builder = _builder(config)
    sql, params = builder.build_metric_query(builder.metrics[0])
    _assert_scalar_replay(
        sql, params, metric_key="latency_ms", breakdown_key=breakdown, filtered=filtered
    )
    if filtered:
        _, state = sql.split("WITH latest_custom_metric_spans AS (", 1)
        state, live = state.split("), live_custom_metric_spans AS (", 1)
        for key in ("company_id", "prompt_slug"):
            assert key in [
                value
                for name, value in params.items()
                if name.startswith("latest_filter")
            ]
        assert "latest_filter_key_0" in state and "latest_filter_key_1" in state
        assert "tupleElement(latest_metric_state, 7) = 1" in live
        assert " AND (" in state
        for index in range(42):
            assert str(10000001 + index) in repr(params)
        for index in range(7):
            assert f"fixture_prompt_{index}" in repr(params)
    assert "SAMPLE" not in sql
    assert "LIMIT 25" not in sql and "LIMIT 100" not in sql
    # Render the real driver placeholders too; missing bindings fail here.
    assert "%(" not in sql % escape_params(params, context=DRIVER_CONTEXT)
    assert config == original
    assert getattr(builder, "_exact_metric_presence", None) is None


@pytest.mark.parametrize("days", [7, 30, 365])
def test_five_compatible_metrics_share_one_exact_replay_and_quantile_state(days):
    builder = _builder(
        _config(days=days, aggregations=("avg", "min", "max", "p25", "p50"))
    )
    plan = builder.build_compatible_metric_group_query(latest_state=True)
    assert plan is not None
    assert len(plan.metrics) == 5
    assert "custom_metric_candidate" not in plan.sql
    assert plan.sql.count("FROM spans AS custom_metric_source") == 1
    # One CTE definition and one use, not a recursively expanded subtree per
    # metric. Keep a generous SQL-size guard; this is not an analyzer benchmark.
    assert plan.sql.count("FROM spans AS ") == 1
    assert len(plan.sql.encode()) < 40_000
    assert "quantilesExact(0.25, 0.5)" in plan.sql
    assert plan.sql.count("ARRAY JOIN [metric_winner]") == 1
    assert "LIMIT " not in plan.sql
    assert "cluster(" not in plan.sql
    assert "%(" not in plan.sql % escape_params(plan.params, context=DRIVER_CONTEXT)
    complete, results = builder.metric_group_results(
        plan,
        [
            {
                "time_bucket": END,
                "breakdown_value": "synthetic_only",
                **{
                    column: index + 0.5
                    for index, column in enumerate(plan.value_columns)
                },
            }
        ],
    )
    assert complete is True
    assert [rows[0]["value"] for _, rows in results] == [0.5, 1.5, 2.5, 3.5, 4.5]


@pytest.mark.parametrize("difference", ["key", "filter"])
def test_different_metric_populations_are_not_intersected_to_force_grouping(difference):
    config = _config(aggregations=("avg", "max"))
    if difference == "key":
        config["metrics"][1]["attribute_key"] = "agent.duration_s"
    else:
        config["metrics"][1]["filters"] = [_filter("prompt_slug", ["other_prompt"])]
    builder = _builder(config)
    assert builder.build_compatible_metric_group_query(latest_state=True) is None
    assert builder._latest_state_spans_required is True
    assert getattr(builder, "_exact_metric_presence", None) is None


def _execute(config, *, worker=False, failure=None, rows=None):
    scope = MagicMock()
    scope.filter.return_value = scope
    scope.count.return_value = len(config["project_ids"])
    scope.values_list.return_value = []
    fetch = MagicMock(return_value=rows or [], side_effect=failure)
    with (
        patch(
            "tracer.views.dashboard._materialize_dashboard_query_scope",
            side_effect=lambda config, *_args, **_kwargs: config,
        ),
        patch(
            "tracer.views.dashboard._bind_dashboard_annotation_completeness",
            side_effect=lambda config, *_args, **_kwargs: config,
        ),
        patch(
            "tracer.views.dashboard._project_queryset_for_dashboard_scope",
            return_value=scope,
        ),
        patch("tracer.views.dashboard.Project.objects.filter", return_value=scope),
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService", return_value=MagicMock()
        ),
        patch("tracer.views.dashboard._fetch_exact_dashboard_rows", fetch),
        patch(
            "tracer.views.dashboard.read_or_schedule_exact_snapshot", return_value=None
        ) as cache,
        patch(
            "tracer.views.dashboard._read_dashboard_rollup_fast_path",
            side_effect=AssertionError("exact trace query used stale rollup"),
        ),
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(
            config,
            SimpleNamespace(id=WORKSPACE, organization_id=ORGANIZATION),
            _exact_worker=worker,
        )
    return response, fetch, cache


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("grouped", [False, True])
def test_public_and_worker_dispatch_latest_sql_and_exact_provenance(worker, grouped):
    config = _config(
        aggregations=("avg", "min", "max", "p25", "p50") if grouped else ("avg",)
    )
    response, fetch, cache = _execute(config, worker=worker)
    assert response.status_code == 200
    fetch.assert_called_once()
    sql = fetch.call_args.kwargs["sql"]
    assert "FROM spans AS custom_metric_source FINAL" in sql
    assert sql.count("ARRAY JOIN [metric_winner]") == 1
    assert "LIMIT " not in sql
    assert "cluster(" not in sql
    result = response.data["result"]
    assert result["query_exact"] is True
    assert result["query_provenance"] == "exact_snapshot"
    assert len(result["metrics"]) == (5 if grouped else 1)
    assert all(metric["query_exact"] is True for metric in result["metrics"])
    if not worker:
        assert (
            cache.call_args.args[1]["trace_snapshot_semantics"]
            == "physical-latest-complete-series-v2"
        )


@pytest.mark.parametrize("old_tag", [None, "physical-latest-v1"])
def test_complete_series_cache_identity_cannot_reuse_or_mutate_old_snapshot(
    old_tag, monkeypatch
):
    response, _, probe = _execute(_config(filtered=False))
    assert response.status_code == 200
    namespace, identity = probe.call_args.args
    assert namespace == "dashboard-query"
    assert identity["trace_snapshot_semantics"] == "physical-latest-complete-series-v2"
    old_identity = deepcopy(identity)
    if old_tag is None:
        old_identity.pop("trace_snapshot_semantics")
    else:
        old_identity["trace_snapshot_semantics"] = old_tag
    old_key = exact_aggregation_cache.snapshot_cache_key(namespace, old_identity)
    new_key = exact_aggregation_cache.snapshot_cache_key(namespace, identity)
    assert new_key != old_key

    # Real cache read/publish functions with isolated process-local storage only.
    local_cache = MagicMock(wraps=LocMemCache(f"dashboard-tag-{uuid4()}", {}))
    monkeypatch.setattr(exact_aggregation_cache, "cache", local_cache)
    old_payload = {**deepcopy(response.data["result"]), "fixture_snapshot": "old"}
    exact_aggregation_cache.publish_exact_snapshot(namespace, old_identity, old_payload)
    old_stored = deepcopy(local_cache.get(old_key))
    local_cache.reset_mock()

    assert exact_aggregation_cache.read_exact_snapshot(namespace, identity) is None
    pending = exact_aggregation_cache.read_or_schedule_exact_snapshot(
        *probe.call_args.args, **probe.call_args.kwargs
    )
    assert "fixture_snapshot" not in pending
    local_cache.set.assert_not_called()

    new_payload = {**deepcopy(response.data["result"]), "fixture_snapshot": "new"}
    exact_aggregation_cache.publish_exact_snapshot(namespace, identity, new_payload)
    local_cache.set.assert_called_once()
    assert local_cache.set.call_args.args[0] == new_key
    assert local_cache.get(old_key) == old_stored
    assert (
        exact_aggregation_cache.read_exact_snapshot(namespace, old_identity)[
            "fixture_snapshot"
        ]
        == "old"
    )
    current = exact_aggregation_cache.read_or_schedule_exact_snapshot(
        *probe.call_args.args, **probe.call_args.kwargs
    )
    assert current["fixture_snapshot"] == "new"
    local_cache.delete.assert_not_called()
    local_cache.delete_many.assert_not_called()
    local_cache.clear.assert_not_called()
    local_cache.touch.assert_not_called()


def test_exact_group_budget_error_never_falls_back_to_raw_or_partial_rows():
    with pytest.raises(DashboardExactReadError, match="read budget"):
        _execute(
            _config(aggregations=("avg", "max")),
            worker=True,
            failure=ServerException("synthetic budget exhaustion", code=241),
        )


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize(
    "kind,map_name",
    [("string", "attrs_string"), ("number", "attrs_number"), ("boolean", "attrs_bool")],
)
def test_scalar_breakdown_state_retains_typed_presence_and_value(days, kind, map_name):
    config = _config(days=days, filtered=False, breakdown="measurement")
    config["breakdowns"][0]["attribute_type"] = kind
    builder = _builder(config)
    sql, params = builder.build_metric_query(builder.metrics[0])
    _assert_scalar_replay(
        sql,
        params,
        metric_key="latency_ms",
        breakdown_key="measurement",
        breakdown_map=map_name,
    )


@pytest.mark.parametrize("key", ["latency_ms", "latency", "cost", "total_tokens"])
@pytest.mark.parametrize("breakdown", [None, "session.id", "measurement"])
def test_scalar_custom_reserved_names_do_not_become_native_or_root_metrics(
    key, breakdown
):
    config = _config(filtered=False, breakdown=breakdown)
    if breakdown is None:
        config["breakdowns"] = []
    config["metrics"][0].update(name=key, attribute_key=key)
    builder = _builder(config)
    sql, params = builder.build_metric_query(builder.metrics[0])
    _assert_scalar_replay(sql, params, metric_key=key, breakdown_key=breakdown)
    assert "avg(metric_value) AS value" in sql
    assert "custom_metric_source.attrs_number[" in sql


@pytest.mark.parametrize("days,breakdown", [(7, "session.id"), (30, "measurement")])
def test_filterless_five_aggregations_share_one_scalar_replay_for_four_projects(
    days, breakdown
):
    config = _config(
        days=days,
        filtered=False,
        breakdown=breakdown,
        aggregations=("avg", "min", "max", "p25", "p50"),
    )
    config["project_ids"] = [
        f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 5)
    ]
    builder = _builder(config)
    plan = builder.build_compatible_metric_group_query(latest_state=True)
    assert plan is not None
    assert len(plan.metrics) == 5
    assert list(plan.params["project_ids"]) == config["project_ids"]
    _assert_scalar_replay(
        plan.sql, plan.params, metric_key="latency_ms", breakdown_key=breakdown
    )
    assert "custom_metric_candidate" not in plan.sql
    assert plan.sql.count("FROM spans AS custom_metric_source") == 1
    assert plan.sql.count("ARRAY JOIN [metric_winner]") == 1
    assert "quantilesExact(0.25, 0.5)(metric_value)" in plan.sql
    assert len(plan.sql.encode()) < 15_000


@pytest.mark.parametrize(
    "days,breakdown,aggregations",
    [
        (7, "session.id", ("avg",)),
        (30, "measurement", ("avg", "min", "max", "p25", "p50")),
    ],
)
def test_saved_widget_omitted_attribute_type_defaults_to_numeric_scalar_replay(
    days, breakdown, aggregations
):
    config = _config(
        days=days, filtered=False, breakdown=breakdown, aggregations=aggregations
    )
    config["project_ids"] = [
        f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 5)
    ]
    for metric in config["metrics"]:
        metric.pop("attribute_type")
    original = deepcopy(config)
    builder = _builder(config)
    if len(aggregations) == 1:
        sql, params = builder.build_metric_query(builder.metrics[0])
    else:
        plan = builder.build_compatible_metric_group_query(latest_state=True)
        assert plan is not None and len(plan.metrics) == 5
        sql, params = plan.sql, plan.params
    _assert_scalar_replay(sql, params, metric_key="latency_ms", breakdown_key=breakdown)
    assert sql.count("ARRAY JOIN [metric_winner]") == 1
    assert sql.count("FROM spans AS custom_metric_source") == 1
    assert config == original
    # The actual public serializer/dispatcher also preserves this numeric
    # default, with one statement for all compatible saved metric selections.
    response, fetch, _ = _execute(config, worker=True)
    assert response.status_code == 200
    fetch.assert_called_once()
    _assert_scalar_replay(
        fetch.call_args.kwargs["sql"],
        fetch.call_args.kwargs["params"],
        metric_key="latency_ms",
        breakdown_key=breakdown,
    )


def test_scalar_latest_state_rechecks_exact_partial_hour_window_after_replay():
    config = _config(filtered=False, breakdown="measurement")
    start = END - timedelta(days=7) + timedelta(minutes=17, microseconds=1)
    end = END + timedelta(minutes=23, microseconds=9)
    config["time_range"] = {
        "custom_start": start.isoformat(),
        "custom_end": end.isoformat(),
    }
    builder = _builder(config)
    sql, params = builder.build_metric_query(builder.metrics[0])
    _assert_scalar_replay(
        sql, params, metric_key="latency_ms", breakdown_key="measurement"
    )
    assert params["start_date"] == start
    assert params["end_date"] == end
    # Timestamp, tombstone, both presence bits and values belong to one winner.
    # They must not be independent argMaxIf aggregates that skip removed keys.
    _, state = sql.split("WITH latest_custom_metric_spans AS (", 1)
    state = " ".join(state.split("AS metric_winner", 1)[0].split()).removeprefix(
        "SELECT "
    )
    assert state == (
        "tuple( custom_metric_source.is_deleted, custom_metric_source.start_time, "
        "mapContains( custom_metric_source.attrs_number, %(custom_metric_attr_key)s ), "
        "custom_metric_source.attrs_number[ %(custom_metric_attr_key)s ], "
        "mapContains(custom_metric_source.attrs_string, %(_custom_bd_key_0)s), "
        "custom_metric_source.attrs_string[%(_custom_bd_key_0)s] )"
    )


@pytest.mark.parametrize("difference", ["key", "filter"])
def test_scalar_group_refuses_incompatible_metric_populations(difference):
    config = _config(filtered=False, aggregations=("avg", "max"))
    if difference == "key":
        config["metrics"][1]["attribute_key"] = "other_metric"
    else:
        config["metrics"][1]["filters"] = [_filter("company_id", ["10000001"])]
    assert (
        _builder(config).build_compatible_metric_group_query(latest_state=True) is None
    )


@pytest.mark.parametrize("count", [101, 257])
@pytest.mark.parametrize("grouped", [False, True])
def test_exact_worker_preserves_every_returned_series_above_former_cap(count, grouped):
    config = _config(
        days=7,
        filtered=False,
        breakdown="session.id",
        aggregations=("avg", "min", "max", "p25", "p50") if grouped else ("avg",),
    )
    config["project_ids"] = [
        f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 5)
    ]
    rows = [
        {
            "time_bucket": (END - timedelta(days=1)).replace(hour=0),
            "breakdown_value": f"fixture-session-{index}",
            **(
                {f"dashboard_metric_value_{metric}": index for metric in range(5)}
                if grouped
                else {"value": index}
            ),
        }
        for index in range(count)
    ]
    response, fetch, _ = _execute(config, worker=True, rows=rows)
    fetch.assert_called_once()
    result = response.data["result"]
    assert result["query_complete"] is True
    for metric in result["metrics"]:
        assert len(metric["series"]) == count
        assert {series["name"] for series in metric["series"]} == {
            row["breakdown_value"] for row in rows
        }
        assert metric["series"][-1]["name"] == "fixture-session-0"
        assert [
            point["value"]
            for point in metric["series"][-1]["data"]
            if point["value"] is not None
        ] == [0]
    read_settings = fetch.call_args.kwargs["settings"]
    assert read_settings["read_overflow_mode"] == "throw"
    assert read_settings["result_overflow_mode"] == "throw"
    assert read_settings["max_result_rows"] > count
    assert read_settings["max_result_bytes"] > 0


@pytest.mark.parametrize("worker", [False, True])
def test_exact_mixed_source_preserves_all_trace_series(worker):
    from tracer.services.clickhouse.query_builders.dataset_dashboard import (
        DatasetQueryBuilder,
    )

    config = _config(filtered=False)
    rows = [
        {
            "time_bucket": END.replace(hour=0),
            "breakdown_value": f"synthetic_{index:03}",
            "value": None if index == 0 else float(index - 128),
        }
        for index in range(257)
    ]
    trace_only, original_fetch, _ = _execute(config, worker=worker, rows=rows)
    expected = trace_only.data["result"]["metrics"][0]["series"]
    assert len(expected) == 257
    config["metrics"].append(
        {
            "id": "row_count",
            "name": "row_count",
            "type": "system_metric",
            "source": "datasets",
            "aggregation": "count",
        }
    )
    response, mixed_fetch, _ = _execute(config, worker=worker, rows=rows)
    result = response.data["result"]
    trace = next(
        metric for metric in result["metrics"] if metric["id"] == "latency_ms_avg"
    )
    assert response.status_code == 200
    assert result["query_exact"] is True and result["query_complete"] is True
    assert trace["series"] == expected
    assert {series["name"] for series in trace["series"]} == {
        row["breakdown_value"] for row in rows
    }
    assert original_fetch.call_count == mixed_fetch.call_count == 1
    assert original_fetch.call_args.kwargs["sql"] == mixed_fetch.call_args.kwargs["sql"]
    assert (
        original_fetch.call_args.kwargs["params"]
        == mixed_fetch.call_args.kwargs["params"]
    )
    # This internal exact-response opt-in does not change legacy direct callers.
    assert len(DatasetQueryBuilder(config)._build_series_data(rows)) == 100


@pytest.mark.parametrize("code", [158, 241, 396])
def test_scalar_result_cap_failure_is_not_returned_as_complete(code):
    with pytest.raises(DashboardExactReadError):
        _execute(
            _config(filtered=False, aggregations=("avg", "max")),
            worker=True,
            failure=ServerException("synthetic result/memory cap", code=code),
        )


@pytest.fixture
def dashboard_native(tmp_path):
    """Disposable in-process RMT only; never an application CH connection."""
    pytest.importorskip("chdb", reason="native dashboard parity requires local chdb")
    from chdb.session import Session

    with Session(str(tmp_path / "dashboard-rmt")) as session:

        def execute(sql, params=None):
            rendered = sql % escape_params(params or {}, context=DRIVER_CONTEXT)
            return [
                json.loads(line)
                for line in str(session.query(rendered, "JSONEachRow")).splitlines()
                if line
            ]

        execute("SET max_threads=1, max_memory_usage=268435456, max_execution_time=10")
        execute("""CREATE TABLE spans (
            project_id UUID, observation_type LowCardinality(String),
            service_name LowCardinality(String), start_time DateTime64(6, 'UTC'),
            trace_id String, id String, attrs_string Map(String, String),
            attrs_number Map(String, Float64), attrs_bool Map(String, Bool),
            is_deleted UInt8, _version UInt64
        ) ENGINE=ReplacingMergeTree(_version, is_deleted)
          PARTITION BY toDate(start_time)
          ORDER BY (project_id, observation_type, service_name,
                    toStartOfHour(start_time), trace_id, id)""")
        yield execute


@pytest.mark.parametrize(
    "kind,selected",
    [("number", 1), ("number", 0), ("boolean", True), ("boolean", False)],
)
@pytest.mark.parametrize("per_metric", [False, True])
@pytest.mark.parametrize("grouped", [False, True])
def test_native_filtered_dashboard_latest_population_parity(
    dashboard_native, kind, selected, per_metric, grouped
):
    """Parity for both old sorted replay and new scalar state; no baseline bug claim."""
    start = END.replace(hour=12, minute=17)
    end = END.replace(hour=13, minute=23)
    config = _config(
        filtered=False,
        breakdown="session.id",
        aggregations=("avg", "min", "max", "p25", "p50") if grouped else ("avg",),
    )
    config["time_range"] = {
        "custom_start": start.isoformat(),
        "custom_end": end.isoformat(),
    }
    leaves = [
        {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": kind,
                "filter_op": "equals",
                "filter_value": selected,
            },
        }
        for key in ("company_id", "prompt_slug")
    ]
    config["filters"] = leaves[:1] if per_metric else leaves
    if per_metric:
        for metric in config["metrics"]:
            metric["filters"] = leaves[1:]
    typed_map = "attrs_number" if kind == "number" else "attrs_bool"
    other = selected + 1 if kind == "number" else not selected
    rows = []

    def add(name, **changes):
        row = {
            "project_id": PROJECT,
            "observation_type": "SPAN",
            "service_name": "svc",
            "start_time": start + timedelta(minutes=3),
            "trace_id": name,
            "id": name,
            "attrs_string": {"session.id": name},
            "attrs_number": {"latency_ms": 10.0},
            "attrs_bool": {},
            "is_deleted": 0,
            "_version": 1,
        }
        row[typed_map].update(company_id=selected, prompt_slug=selected)
        row.update(changes)
        rows.append(row)
        return row

    def revise(row, **changes):
        add(row["id"], **{**row, "_version": 2, **changes})

    for name in (
        "metric_clear",
        "deleted",
        "filter_clear",
        "filter_changed",
        "breakdown_clear",
        "time_before",
        "time_after",
        "type_changed",
    ):
        old = add(
            name,
            start_time=end - timedelta(minutes=3)
            if name == "time_after"
            else start + timedelta(minutes=3),
        )
        change = {
            "metric_clear": {
                "attrs_number": {
                    k: v for k, v in old["attrs_number"].items() if k != "latency_ms"
                }
            },
            "deleted": {"is_deleted": 1},
            "filter_clear": {
                typed_map: {
                    k: v for k, v in old[typed_map].items() if k != "company_id"
                }
            },
            "filter_changed": {typed_map: {**old[typed_map], "company_id": other}},
            "breakdown_clear": {"attrs_string": {}},
            "time_before": {"start_time": start - timedelta(seconds=1)},
            "time_after": {"start_time": end},
            "type_changed": {
                "attrs_number": {
                    k: v for k, v in old["attrs_number"].items() if k != "latency_ms"
                },
                "attrs_string": {"session.id": name, "latency_ms": "10"},
            },
        }[name]
        revise(old, **change)
    old = add("moved_in", start_time=start - timedelta(seconds=1))
    revise(old, start_time=start)
    old = add("metric_changed")
    revise(old, attrs_number={**old["attrs_number"], "latency_ms": 42})
    old = add("breakdown_changed")
    revise(old, attrs_string={"session.id": "renamed"})
    for key, value in (
        ("project_id", WORKSPACE),
        ("observation_type", "TOOL"),
        ("service_name", "other"),
        ("trace_id", "other"),
        ("start_time", end - timedelta(minutes=3)),
    ):
        old = add("identity_" + key)
        revise(old, **{key: value, "is_deleted": 1})
    for value in (10.0, 30.0, 50.0):
        add(
            "stable",
            id=f"stable-{value}",
            attrs_number={
                "latency_ms": value,
                **(
                    {"company_id": selected, "prompt_slug": selected}
                    if kind == "number"
                    else {}
                ),
            },
        )
    for name in ("split_spans", "split_versions"):
        old = add(name)
        old[typed_map].pop("prompt_slug")
        companion = add(
            name, id=name + "-other" if name == "split_spans" else name, _version=2
        )
        companion[typed_map].pop("company_id")
    missing = add("missing_zero_false")
    missing[typed_map].pop("company_id")
    collision = add("wrong_type")
    collision[typed_map].pop("company_id")
    collision["attrs_string"]["company_id"] = str(selected)

    # One immutable fixture part: no background merge controls or service DDL.
    fixture_params = {
        f"row_{i}": tuple(
            value.replace(tzinfo=None).isoformat(sep=" ")
            if isinstance(value, datetime)
            else value
            for value in row.values()
        )
        for i, row in enumerate(rows)
    }
    dashboard_native(
        "INSERT INTO spans VALUES " + ", ".join(f"%({key})s" for key in fixture_params),
        fixture_params,
    )
    builder = _builder(config)
    if grouped:
        plan = builder.build_compatible_metric_group_query(latest_state=True)
        assert plan is not None
        sql, params = plan.sql, plan.params
        columns = plan.value_columns
    else:
        sql, params = builder.build_metric_query(builder.metrics[0])
        columns = ("value",)
    actual = dashboard_native(sql, params)
    expressions = (
        ("avg", "min", "max", "quantileExact(0.25)", "quantileExact(0.5)")
        if grouped
        else ("avg",)
    )
    # Full FINAL oracle: no candidate code, compiler predicate, or argMax reuse.
    reference = dashboard_native(
        "SELECT toStartOfDay(start_time) AS time_bucket, attrs_string['session.id'] AS breakdown_value, "
        + ", ".join(
            f"{aggregate}(attrs_number['latency_ms']) AS {column}"
            for aggregate, column in zip(expressions, columns, strict=True)
        )
        + f""" FROM spans FINAL WHERE project_id = %(project)s AND is_deleted = 0
          AND start_time >= toDateTime64(%(start)s, 6, 'UTC') AND start_time < toDateTime64(%(end)s, 6, 'UTC')
          AND mapContains(attrs_number, 'latency_ms') AND mapContains(attrs_string, 'session.id')
          AND mapContains({typed_map}, 'company_id') AND {typed_map}['company_id'] = %(selected)s
          AND mapContains({typed_map}, 'prompt_slug') AND {typed_map}['prompt_slug'] = %(selected)s
          GROUP BY time_bucket, breakdown_value ORDER BY time_bucket, breakdown_value
          SETTINGS optimize_move_to_prewhere=0, optimize_move_to_prewhere_if_final=0""",
        {
            "project": PROJECT,
            "start": start.replace(tzinfo=None).isoformat(sep=" "),
            "end": end.replace(tzinfo=None).isoformat(sep=" "),
            "selected": selected,
        },
    )
    assert actual == reference
    expected = {
        "moved_in": 10,
        "metric_changed": 42,
        "renamed": 10,
        **{
            "identity_" + key: 10
            for key in (
                "project_id",
                "observation_type",
                "service_name",
                "trace_id",
                "start_time",
            )
        },
    }
    expected["stable"] = 30
    assert {row["breakdown_value"]: row[columns[0]] for row in actual} == expected
    if grouped:
        stable = next(row for row in actual if row["breakdown_value"] == "stable")
        assert [stable[column] for column in columns] == [30, 10, 50, 10, 30]


def test_native_text_dashboard_requires_real_unicode_support(dashboard_native):
    functions = dashboard_native(
        "SELECT count() AS n FROM system.functions WHERE name='lowerUTF8'"
    )
    if not functions[0]["n"]:
        pytest.skip(
            "native text/Unicode parity blocked: local chdb lacks lowerUTF8; no ASCII substitution"
        )
    config = _config(filtered=False, breakdown="session.id")
    config["filters"] = [
        _filter("company_id", ["équipe"]),
        _filter("prompt_slug", ["réponse"]),
    ]
    dashboard_native(
        """INSERT INTO spans VALUES (
            %(project)s, 'SPAN', 'svc', %(time)s, 'trace', 'span',
            map('session.id', 'native', 'company_id', 'ÉQUIPE', 'prompt_slug', 'RÉPONSE'),
            map('latency_ms', 10), map(), 0, 1)""",
        {"project": PROJECT, "time": END - timedelta(hours=1)},
    )
    builder = _builder(config)
    sql, params = builder.build_metric_query(builder.metrics[0])
    # Execute the real Unicode predicate if supported; never use ASCII lower().
    assert "lowerUTF8" in sql
    assert [
        (row["breakdown_value"], row["value"]) for row in dashboard_native(sql, params)
    ] == [("native", 10)]


def _partial_group_config():
    config = _config(filtered=False, aggregations=("p25", "p50"))
    a = config["metrics"]
    b = [
        dict(a[0], id="b_avg", attribute_key="company_id", aggregation="avg"),
        dict(a[1], id="b_min", attribute_key="company_id", aggregation="min"),
    ]
    c = dict(
        a[0],
        id="empty_outlier",
        attribute_key="missing_fixture_metric",
        aggregation="max",
    )
    config["metrics"] = [a[0], b[0], c, a[1], b[1]]
    for index, metric in enumerate(config["metrics"]):
        metric["display_name"] = f"Display {index}"
    return config


def _prepare_partial_groups(config):
    builder = _builder(config)
    return builder, view.DashboardViewSet._prepare_metric_queries(builder)


def test_disjoint_groups_one_compile_per_metric_and_no_extra_root():
    config = _partial_group_config()
    before = deepcopy(config)
    builder = _builder(config)
    with patch.object(
        builder, "build_metric_query", wraps=builder.build_metric_query
    ) as compile_metric:
        queries = view.DashboardViewSet._prepare_metric_queries(builder)
        groups = builder.group_prepared_metric_queries(queries)
    assert compile_metric.call_count == len(config["metrics"])
    assert [indices for indices, _ in groups] == [(0, 3), (1, 4), (2,)]
    assert sorted(i for indices, _ in groups for i in indices) == list(range(5))
    for indices, plan in groups:
        sql = plan.sql if plan else queries[indices[0]][1]
        assert sql.count("FROM spans AS custom_metric_source FINAL") == 1
        assert "LIMIT " not in sql
        if plan:
            assert plan.metrics == tuple(queries[i][0] for i in indices)
            assert plan.params == queries[indices[0]][2]
    assert "quantilesExact(0.25, 0.5)" in groups[0][1].sql
    assert config == before and builder._latest_state_spans_required is True
    assert builder.build_compatible_metric_group_query(latest_state=True) is None


@pytest.mark.parametrize("difference", ["key", "filter"])
def test_pair_after_outlier_not_only_prefix(difference):
    config = _config(filtered=False, aggregations=("max", "avg", "min"))
    if difference == "key":
        config["metrics"][0]["attribute_key"] = "different_key"
    else:
        config["metrics"][0]["filters"] = [_filter("fixture", ["other"])]
    builder, queries = _prepare_partial_groups(config)
    groups = builder.group_prepared_metric_queries(queries)
    assert [indices for indices, _ in groups] == [(0,), (1, 2)]
    assert groups[0][1] is None and groups[1][1] is not None


@pytest.mark.parametrize(
    "difference",
    ["params", "settings", "tail", "dimensions", "unsupported", "annotation"],
)
def test_compiler_contract_stays_fail_closed(difference):
    builder, queries = _prepare_partial_groups(
        _config(filtered=False, aggregations=("avg", "min"))
    )
    metric, sql, params = queries[1]
    if difference == "params":
        params = {**params, "project_ids": ["33333333-3333-4333-8333-333333333333"]}
    elif difference == "settings":
        sql += ", max_threads=1"
    elif difference == "tail":
        sql = sql.replace("attrs_number", "attrs_bool")
    elif difference == "dimensions":
        sql = sql.replace("toStartOfDay", "toStartOfWeek")
    elif difference == "unsupported":
        metric = dict(metric, type="eval_metric")
    else:
        builder.breakdowns = [{"type": "annotation", "name": "fixture"}]
    groups = builder.group_prepared_metric_queries((queries[0], (metric, sql, params)))
    assert groups == [((0,), None), ((1,), None)]


@pytest.mark.parametrize("worker", [False, True])
def test_actual_public_disjoint_fetches_order_labels_deadline_and_257_series(
    monkeypatch, worker
):
    config = _partial_group_config()
    original = deepcopy(config)
    builder, queries = _prepare_partial_groups(config)
    groups = builder.group_prepared_metric_queries(queries)
    by_sql = {
        plan.sql if plan else queries[indices[0]][1]: (indices, plan)
        for indices, plan in groups
    }
    fetched, remaining = [], []
    lock = Lock()

    class Deadline:
        def remaining_ms(self, ceiling=None, **kwargs):
            with lock:
                value = 9000 - 100 * len(remaining)
                remaining.append(value)
                return value

    monkeypatch.setattr(view.ReadDeadline, "start", lambda *_: Deadline())

    def fetch(**kwargs):
        with lock:
            fetched.append(kwargs)
        indices, plan = by_sql[kwargs["sql"]]
        if plan is None:
            return []
        return [
            {
                "time_bucket": END.replace(hour=0),
                "breakdown_value": f"series_{series:03}",
                **{
                    column: (None if series == 0 else float(index - 3))
                    for column, index in zip(plan.value_columns, indices, strict=True)
                },
            }
            for series in range(257)
        ]

    response, calls, _ = _execute(config, worker=worker, failure=fetch)
    assert response.status_code == 200 and calls.call_count == 3
    result = response.data["result"]
    assert result["query_complete"] is True
    assert result["query_status"] == "complete"
    assert result["query_exact"] is True
    assert result["query_sampled"] is False
    metrics = result["metrics"]
    assert [metric["id"] for metric in metrics] == [m["id"] for m in config["metrics"]]
    assert [metric["name"] for metric in metrics] == [
        m["display_name"] for m in config["metrics"]
    ]
    assert all(m["query_exact"] for m in metrics)
    for index, metric in enumerate(metrics):
        if index == 2:
            assert all(point["value"] is None for point in metric["series"][0]["data"])
        else:
            assert len(metric["series"]) == 257
            assert {s["name"] for s in metric["series"]} == {
                f"series_{i:03}" for i in range(257)
            }
            for series in metric["series"]:
                values = [p["value"] for p in series["data"] if p["value"] is not None]
                assert values == (
                    [] if series["name"] == "series_000" else [float(index - 3)]
                )
    assert len({call["sql"] for call in fetched}) == 3
    # Each read consumes a fresh shared budget, followed by the collection
    # fence. Formatting a complete exact payload does not start another read
    # and must not trigger a final deadline check that discards those results.
    assert remaining == [9000, 8900, 8800, 8700]
    assert {call["timeout_ms"] for call in fetched} == set(remaining[:3])
    assert config == original


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("failure_group", [True, False])
def test_group_and_singleton_budget_fail_closed(worker, failure_group):
    config = _partial_group_config()

    def fetch(**kwargs):
        grouped = "dashboard_metric_value_" in kwargs["sql"]
        if grouped == failure_group:
            raise ServerException("synthetic guard", code=241)
        return []

    with patch.object(
        view,
        "_read_public_dashboard_query",
        return_value={"query_complete": False, "query_status": "pending"},
    ) as schedule:
        if worker:
            with pytest.raises(view.DashboardExactReadError):
                _execute(config, worker=worker, failure=fetch)
            schedule.assert_not_called()
        else:
            response, _, _ = _execute(config, failure=fetch)
            assert response.data["result"]["query_complete"] is False
            schedule.assert_called_once()


def test_programming_and_malformed_group_errors_propagate():
    with pytest.raises(RuntimeError, match="fixture defect"):
        _execute(_partial_group_config(), failure=RuntimeError("fixture defect"))
    with pytest.raises(ValueError, match="malformed"):
        _execute(_partial_group_config(), rows=[{"time_bucket": END}])


def test_repeated_display_ids_do_not_reorder_or_collapse_metrics():
    config = _partial_group_config()
    for metric in config["metrics"]:
        metric["id"] = "same_display_id"
    response, calls, _ = _execute(config)
    assert response.status_code == 200 and calls.call_count == 3
    assert [m["id"] for m in response.data["result"]["metrics"]] == [
        "same_display_id"
    ] * 5
    assert [m["name"] for m in response.data["result"]["metrics"]] == [
        f"Display {i}" for i in range(5)
    ]


@pytest.mark.parametrize("selected", [0, 1])
def test_native_disjoint_groups_against_unfiltered_whole_final(
    dashboard_native, monkeypatch, selected
):
    captured = []
    original_builder = _builder

    def capture(config):
        captured.append(deepcopy(config))
        return original_builder(config)

    # Reuse unchanged typed latest/clear/tombstone/time/key/same-row fixture and
    # all of its original assertions, then independently materialize ALL winners.
    with patch(__name__ + "._builder", side_effect=capture):
        test_native_filtered_dashboard_latest_population_parity(
            dashboard_native, "number", selected, True, True
        )
    source_config = captured[0]
    winners = dashboard_native(
        "SELECT project_id, start_time, attrs_number, attrs_string, is_deleted FROM spans FINAL"
    )
    config = _partial_group_config()
    config["time_range"] = source_config["time_range"]
    config["breakdowns"] = source_config["breakdowns"]
    config["filters"] = source_config["filters"]
    for metric in config["metrics"]:
        metric["filters"] = deepcopy(source_config["metrics"][0]["filters"])
    start, end = [
        datetime.fromisoformat(config["time_range"][key])
        for key in ("custom_start", "custom_end")
    ]
    expected_rows = []
    for metric in config["metrics"]:
        grain_values = defaultdict(list)
        for row in winners:
            time = datetime.fromisoformat(row["start_time"]).replace(tzinfo=UTC)
            numbers, strings = row["attrs_number"], row["attrs_string"]
            if (
                row["project_id"] not in config["project_ids"]
                or row["is_deleted"]
                or not start <= time < end
                or "session.id" not in strings
                or metric["attribute_key"] not in numbers
                or any(
                    key not in numbers or numbers[key] != selected
                    for key in ("company_id", "prompt_slug")
                )
            ):
                continue
            grain_values[(time.date().isoformat(), strings["session.id"])].append(
                numbers[metric["attribute_key"]]
            )
        expected = []
        for (day, breakdown), values in sorted(grain_values.items()):
            op = metric["aggregation"]
            value = (
                sum(values) / len(values)
                if op == "avg"
                else min(values)
                if op == "min"
                else max(values)
                if op == "max"
                else sorted(values)[int(len(values) * {"p25": 0.25, "p50": 0.5}[op])]
            )
            expected.append(
                {"time_bucket": day, "breakdown_value": breakdown, "value": value}
            )
        expected_rows.append(expected)
    builder, queries = _prepare_partial_groups(config)
    groups = builder.group_prepared_metric_queries(queries)
    assert [indices for indices, _ in groups] == [(0, 3), (1, 4), (2,)]
    by_sql = {
        plan.sql if plan else queries[indices[0]][1]: (indices, plan)
        for indices, plan in groups
    }
    seen = []

    def fetch(**kwargs):
        indices, plan = by_sql[kwargs["sql"]]
        rows = dashboard_native(kwargs["sql"], kwargs["params"])
        seen.append(indices)
        results = (
            builder.metric_group_results(plan, rows)[1] if plan else [(None, rows)]
        )
        for index, (_, actual) in zip(indices, results, strict=True):
            normalized = [
                {
                    **row,
                    "time_bucket": datetime.fromisoformat(row["time_bucket"])
                    .date()
                    .isoformat(),
                }
                for row in actual
            ]
            assert normalized == expected_rows[index]
        return rows

    # Session transport owns one local RMT; do not concurrently call its client.
    monkeypatch.setattr(view, "_DASHBOARD_TRACE_MAX_CONCURRENT_METRICS", 1)
    response, calls, _ = _execute(config, worker=True, failure=fetch)
    assert response.status_code == 200 and calls.call_count == 3 and len(seen) == 3
    assert response.data["result"]["query_exact"] is True
    assert [m["id"] for m in response.data["result"]["metrics"]] == [
        m["id"] for m in config["metrics"]
    ]


@pytest.fixture
def root_final_native(tmp_path):
    from tracer.tests.test_span_physical_identity_latest import engine

    yield from engine.__wrapped__(tmp_path)


@pytest.mark.parametrize(
    "kind,operation,value,per_metric",
    [
        (None, None, None, False),
        ("boolean", "equals", False, False),
        ("boolean", "equals", True, True),
        ("number", "equals", 0, True),
        ("boolean", "not_equals", True, False),
        ("number", "not_equals", 1, False),
        ("text", "not_in", ["one"], True),
        ("text", "not_contains", "one", False),
        ("boolean", "is_null", None, False),
        ("boolean", "is_not_null", None, True),
    ],
)
def test_native_dashboard_fallback_resolves_winners_before_exact_time(
    root_final_native, kind, operation, value, per_metric
):
    run, insert = root_final_native
    start, end = END.replace(minute=15), END.replace(minute=30)
    base = {
        "project_id": PROJECT, "start_time": END.replace(minute=20),
        "attrs_bool": {"ready": 0}, "attrs_number": {"ready": 0},
        "attrs_string": {"ready": "zero"}, "latency_ms": 90,
    }

    def add(name, **changes):
        insert(**{**base, "id": name, **changes})

    for name, time in (("before", start - timedelta(minutes=5)),
                       ("after", end + timedelta(minutes=10))):
        add(name)
        add(name, _version=2, start_time=time)
    add("clear")
    add("clear", _version=2, attrs_bool={}, attrs_number={}, attrs_string={}, latency_ms=70)
    add("wrong-type", attrs_bool={}, attrs_number={}, attrs_string={"ready": "other"}, latency_ms=20)
    add("child", parent_span_id="parent")
    add("nil-parent", parent_span_id="00000000-0000-0000-0000-000000000000")
    add("root-to-child")
    add("root-to-child", _version=2, parent_span_id="parent")
    add("deleted")
    add("deleted", _version=2, is_deleted=1)
    add("foreign", project_id=WORKSPACE)

    for positive in (False, True):
        if positive:
            add("zero", latency_ms=10, parent_span_id="")
            add("one", attrs_bool={"ready": 1}, attrs_number={"ready": 1},
                attrs_string={"ready": "one"}, latency_ms=30)
            add("reverse", start_time=start - timedelta(minutes=5))
            add("reverse", _version=2, latency_ms=50)
            add("zero", service_name="other-service", latency_ms=17)
            add("zero", start_time=END + timedelta(hours=1), latency_ms=99)
            add("at-start", start_time=start, latency_ms=19)
            add("at-end", start_time=end, latency_ms=99)
            add("nullable", latency_ms=None)

        # Complete physical winners, materialized before ANY mutable filter.
        winners = run("SELECT project_id,start_time,is_deleted,parent_span_id,"
                      "attrs_bool,attrs_number,attrs_string,latency_ms FROM spans FINAL")
        roots = [r for r in winners if r["project_id"] == PROJECT and not r["is_deleted"]
                 and r["parent_span_id"] in (None, "")
                 and start <= r["start_time"].replace(tzinfo=UTC) < end]
        if kind:
            column = {"boolean": "attrs_bool", "number": "attrs_number", "text": "attrs_string"}[kind]

            def matches(row, column=column):
                attrs = row[column]
                if operation == "is_null":
                    return "ready" not in attrs
                if "ready" not in attrs:
                    return False
                actual = attrs["ready"]
                return {
                    "equals": lambda: actual == value,
                    "not_equals": lambda: actual != value,
                    "not_in": lambda: actual not in value,
                    "not_contains": lambda: value not in actual,
                    "is_not_null": lambda: True,
                }[operation]()

            roots = [r for r in roots if matches(r)]
        values = [r["latency_ms"] for r in roots if r["latency_ms"] is not None]
        body = _config(filtered=False)
        body.update(breakdowns=[], time_range={"custom_start": start.isoformat(), "custom_end": end.isoformat()})
        filters = [{"column_id": "ready", "filter_config": {
            "col_type": "SPAN_ATTRIBUTE", "filter_type": kind,
            "filter_op": operation, "filter_value": value,
        }}] if kind else []
        body["filters"] = [] if per_metric else filters
        body["metrics"] = [{"id": "latency", "name": "latency", "type": "system_metric",
            "source": "traces", "aggregation": "avg", "filters": filters if per_metric else []}]
        builder = _builder(body)
        sql, params = builder.build_metric_query(builder.metrics[0])
        assert "parent_span_id" in sql and "avg(latency_ms)" in sql
        actual = run(sql, params)
        assert len(actual) == bool(roots)
        if roots:
            assert values  # This fixture never relies on an all-NULL average.
            assert actual[0]["value"] == sum(values) / len(values)
