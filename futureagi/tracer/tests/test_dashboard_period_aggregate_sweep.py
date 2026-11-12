"""Period scalars must be computed from rows, never from bucket aggregates."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tracer.serializers.dashboard import DashboardQueryApiResponseSerializer
from tracer.services.clickhouse.query_builders.dashboard import DashboardQueryBuilder
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.services.exact_aggregation_cache import snapshot_cache_key
from tracer.views.dashboard import DashboardWidgetViewSet

PROJECT = "11111111-1111-4111-8111-111111111111"
START = datetime(2026, 11, 1, tzinfo=UTC)
END = datetime(2026, 11, 3, tzinfo=UTC)


def config(metrics=None, breakdowns=None):
    return {
        "project_ids": [PROJECT],
        "time_range": {
            "custom_start": START.isoformat(),
            "custom_end": END.isoformat(),
        },
        "granularity": "day",
        "metrics": metrics
        or [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ],
        "breakdowns": breakdowns or [],
        "filters": [
            {"type": "system_metric", "name": "status", "operator": "is", "value": "OK"}
        ],
    }


@pytest.mark.parametrize(
    "builder_type", [DashboardQueryBuilder, DashboardQueryBuilderV2]
)
@pytest.mark.parametrize("aggregation", ["avg", "count_distinct", "p95"])
@pytest.mark.parametrize(
    "breakdowns", [[], [{"type": "system_metric", "name": "model"}]]
)
def test_period_aggregate_uses_same_filtered_population(
    builder_type, aggregation, breakdowns
):
    metric = {"id": "tokens", "name": "tokens", "aggregation": aggregation}
    builder = builder_type(config([metric], breakdowns))
    bucket_sql, bucket_params = builder.build_metric_query(metric)
    builder.include_period_aggregate = True
    sql, params = builder.build_metric_query(metric)
    assert params == bucket_params
    assert "GROUPING(time_bucket) AS is_period_aggregate" in sql
    expected = (
        "GROUP BY GROUPING SETS ((time_bucket, breakdown_value), (breakdown_value))"
        if breakdowns
        else "GROUP BY GROUPING SETS ((time_bucket), ())"
    )
    assert expected in sql
    # The population, latest-state source, and predicates are identical. Only
    # the outer grouping/select list may differ from the bucket query.
    assert (
        sql.split("\nFROM ", 1)[1].split("\nGROUP BY ")[0]
        == (bucket_sql.split("\nFROM ", 1)[1].split("\nGROUP BY ")[0])
    )
    aggregate = {
        "avg": "avg(total_tokens)",
        "count_distinct": "uniqExact(total_tokens)",
        "p95": "quantileExact(0.95)(total_tokens)",
    }[aggregation]
    assert aggregate in sql


@pytest.mark.parametrize(
    "breakdowns",
    [[], [{"type": "custom_attribute", "name": "channel", "attribute_type": "string"}]],
)
def test_v2_latest_custom_metric_keeps_period_rows_in_grouped_results(breakdowns):
    metrics = [
        {
            "id": "score",
            "name": "score",
            "type": "custom_attribute",
            "attribute_key": "score",
            "attribute_type": "number",
            "aggregation": agg,
        }
        for agg in ("p95", "p99")
    ]
    query = config(metrics, breakdowns)
    query["filters"] = []
    query["require_versioned_snapshot"] = True
    builder = DashboardQueryBuilderV2(query)
    builder.include_period_aggregate = True
    plan = builder.build_compatible_metric_group_query(latest_state=True)
    assert plan is not None
    assert "GROUP BY GROUPING SETS" in plan.sql
    assert "quantilesExact(0.95, 0.99)(metric_value)" in plan.sql
    assert plan.sql.count("FROM spans AS custom_metric_source FINAL") == 1
    assert plan.has_breakdown is bool(breakdowns)
    row = {
        "time_bucket": START,
        "is_period_aggregate": 1,
        "breakdown_value": "voice",
        **dict.fromkeys(plan.value_columns, 42),
    }
    complete, results = builder.metric_group_results(plan, [row])
    assert complete
    assert all(rows[0]["is_period_aggregate"] == 1 for _, rows in results)
    assert all(
        ("breakdown_value" in rows[0]) is bool(breakdowns) for _, rows in results
    )


@pytest.mark.parametrize(
    "aggregation,period_value",
    [("avg", 19), ("count_distinct", 3), ("p95", 100), ("sum", 0), ("avg", None)],
)
def test_period_scalar_survives_formatting_and_response_serialization(
    aggregation, period_value
):
    builder = DashboardQueryBuilder(
        config(breakdowns=[{"name": "project", "type": "system_metric"}])
    )
    builder.include_period_aggregate = True
    metric = {"id": "latency", "name": "latency", "aggregation": aggregation}
    rows = [
        {
            "time_bucket": START,
            "breakdown_value": PROJECT,
            "value": 10,
            "is_period_aggregate": 0,
        },
        {
            "time_bucket": datetime(2026, 11, 2, tzinfo=UTC),
            "breakdown_value": PROJECT,
            "value": 100,
            "is_period_aggregate": 0,
        },
        # A grouping-set default timestamp must not overwrite a real bucket.
        {
            "time_bucket": START,
            "breakdown_value": PROJECT,
            "value": period_value,
            "is_period_aggregate": 1,
        },
    ]
    formatted = builder.format_results([(metric, rows)], {PROJECT: "Project one"})
    response = DashboardQueryApiResponseSerializer(
        {"status": True, "result": formatted}
    ).data
    series = response["result"]["metrics"][0]["series"][0]
    assert series["name"] == "Project one"
    assert series["aggregate_value"] == period_value
    assert [
        point["value"] for point in series["data"] if point["value"] is not None
    ] == [10, 100]


def test_missing_period_value_is_explicitly_unavailable_not_bucket_average():
    builder = DashboardQueryBuilder(config())
    builder.include_period_aggregate = True
    formatted = builder.format_results(
        [({"name": "latency", "aggregation": "avg"}, [])]
    )
    assert formatted["metrics"][0]["series"][0]["aggregate_value"] is None


@pytest.mark.parametrize(
    "builder_type,column",
    [
        (DashboardQueryBuilder, "span_attr_num"),
        (DashboardQueryBuilderV2, "attrs_number"),
    ],
)
@pytest.mark.parametrize(
    "aggregation,function", [("avg", "avg"), ("p95", "quantileExact(0.95)")]
)
def test_ttft_seconds_are_scaled_before_aggregation(
    builder_type, column, aggregation, function
):
    metric = {
        "id": "time_to_first_token",
        "name": "time_to_first_token",
        "aggregation": aggregation,
    }
    builder = builder_type(config([metric]))
    sql, _ = builder.build_metric_query(metric)
    assert f"{function}({column}['gen_ai.server.time_to_first_token'] * 1000)" in sql
    formatted = builder.format_results(
        [(metric, [{"time_bucket": START, "value": 250}])]
    )
    assert formatted["metrics"][0]["unit"] == "ms"


@pytest.mark.parametrize(
    "builder_type,column",
    [
        (DashboardQueryBuilder, "span_attr_num"),
        (DashboardQueryBuilderV2, "attrs_number"),
    ],
)
@pytest.mark.parametrize("aggregation", ["avg", "p95", "count"])
@pytest.mark.parametrize(
    "breakdowns", [[], [{"type": "system_metric", "name": "model"}]]
)
def test_ttft_population_excludes_spans_without_the_attribute(
    builder_type, column, aggregation, breakdowns
):
    """A span with no TTFT reads 0 from the Map; it must not enter the mean.

    One generation measured at 0.25 s and one root span without the key
    averaged to 125 ms. Only spans carrying the key are observations, in the
    bucket rows and the period scalar alike.
    """
    metric = {
        "id": "time_to_first_token",
        "name": "time_to_first_token",
        "aggregation": aggregation,
    }
    presence = f"mapContains({column}, 'gen_ai.server.time_to_first_token')"
    builder = builder_type(config([metric], breakdowns))
    bucket_sql, _ = builder.build_metric_query(metric)
    builder.include_period_aggregate = True
    period_sql, _ = builder.build_metric_query(metric)

    for sql in (bucket_sql, period_sql):
        assert presence in sql
        assert sql.index("\nWHERE ") < sql.index(presence) < sql.rindex("GROUP BY")


@pytest.mark.parametrize(
    "builder_type", [DashboardQueryBuilder, DashboardQueryBuilderV2]
)
def test_other_system_metrics_do_not_require_the_ttft_attribute(builder_type):
    metric = {"id": "tokens", "name": "tokens", "aggregation": "avg"}
    sql, _ = builder_type(config([metric])).build_metric_query(metric)
    assert "gen_ai.server.time_to_first_token" not in sql


@pytest.mark.parametrize("mixed_sources", [False, True])
@pytest.mark.parametrize("worker", [False, True])
def test_live_trace_execution_requests_and_returns_full_period_scalar(
    mixed_sources, worker
):
    query = config()
    query["filters"] = []
    if mixed_sources:
        query["metrics"].append(
            {
                "id": "row_count",
                "name": "row_count",
                "type": "system_metric",
                "aggregation": "count",
                "source": "datasets",
            }
        )
    workspace = SimpleNamespace(
        id="22222222-2222-4222-8222-222222222222",
        organization_id="33333333-3333-4333-8333-333333333333",
    )
    calls = []

    class Analytics:
        def execute_ch_query(self, sql, params, *, timeout_ms, settings):
            calls.append(sql)
            assert "GROUP BY GROUPING SETS ((time_bucket), ())" in sql
            return SimpleNamespace(
                data=[
                    {"time_bucket": START, "value": 10, "is_period_aggregate": 0},
                    {
                        "time_bucket": datetime(2026, 11, 2, tzinfo=UTC),
                        "value": 100,
                        "is_period_aggregate": 0,
                    },
                    {"time_bucket": START, "value": 19, "is_period_aggregate": 1},
                ],
                columns=[],
            )

    projects = MagicMock()
    projects.filter.return_value = projects
    projects.count.return_value = 1
    projects.values_list.return_value = []
    with (
        patch(
            "tracer.views.dashboard._materialize_dashboard_query_scope",
            side_effect=lambda value, *_a, **_kw: value,
        ),
        patch(
            "tracer.views.dashboard._bind_dashboard_annotation_completeness",
            side_effect=lambda value, *_a, **_kw: value,
        ),
        patch(
            "tracer.views.dashboard._read_dashboard_rollup_fast_path", return_value=None
        ),
        patch(
            "tracer.views.dashboard._project_queryset_for_dashboard_scope",
            return_value=projects,
        ),
        patch("tracer.views.dashboard.Project.objects.filter", return_value=projects),
        patch(
            "tracer.views.dashboard.V2AnalyticsQueryService", return_value=Analytics()
        ),
        patch("tracer.views.dashboard.probe_candidate_estimates", return_value={}),
        patch("tracer.views.dashboard.observe_completed_read"),
        patch("tracer.views.dashboard.exceeds_remaining_deadline", return_value=False),
        patch(
            "tracer.views.dashboard.read_or_schedule_exact_snapshot", return_value=None
        ) as probe,
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(
            query, workspace, _exact_worker=worker
        )
    assert response.status_code == 200
    assert len(calls) == 1
    assert len(response.data["result"]["metrics"]) == (2 if mixed_sources else 1)
    assert response.data["result"]["metrics"][0]["series"][0]["aggregate_value"] == 19
    if not worker:
        namespace, identity = probe.call_args.args
        assert (
            identity["trace_snapshot_semantics"] == "physical-latest-complete-series-v2"
        )
        assert identity["trace_period_aggregate_semantics"] == "grouping-sets-v1"
        old_identity = dict(identity)
        old_identity.pop("trace_period_aggregate_semantics")
        assert snapshot_cache_key(namespace, identity) != snapshot_cache_key(
            namespace, old_identity
        )
