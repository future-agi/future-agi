"""Period scalars must be computed from rows, never from bucket aggregates."""

import re
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


# The string Map that sits beside each builder's numeric attribute Map.
_STRING_MAP = {"span_attr_num": "span_attr_str", "attrs_number": "attrs_string"}


def ttft_read(numbers, strings):
    """The TTFT read: seconds only on a span traceAI marks with unit "s"."""
    value = f"{numbers}['gen_ai.server.time_to_first_token']"
    unit = f"{strings}['traceai.timing.server_ttft.unit']"
    return f"if({unit} = 's', {value} * 1000, {value})"


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
def test_ttft_stored_milliseconds_are_aggregated_as_is(
    builder_type, column, aggregation, function
):
    # A span traceAI does not mark with unit "s" carries milliseconds and is
    # aggregated as is: a span carrying 35 reads 35 ms, never 35000.
    metric = {
        "id": "time_to_first_token",
        "name": "time_to_first_token",
        "aggregation": aggregation,
    }
    builder = builder_type(config([metric]))
    sql, _ = builder.build_metric_query(metric)
    assert f"{function}({ttft_read(column, _STRING_MAP[column])})" in sql
    formatted = builder.format_results(
        [(metric, [{"time_bucket": START, "value": 35}])]
    )
    result = formatted["metrics"][0]
    assert result["unit"] == "ms"
    points = result["series"][0]["data"]
    assert [p["value"] for p in points if p["value"] is not None] == [35]


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

    One generation measured at 250 ms and one root span without the key
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


_TTFT_SYSTEM_FILTER = {
    "metric_type": "system_metric",
    "metric_name": "time_to_first_token",
    "operator": "greater_than",
    "value": 35,
    "source": "traces",
}
_TTFT_SYSTEM_BREAKDOWN = {"type": "system_metric", "name": "time_to_first_token"}


@pytest.mark.parametrize(
    "builder_type,numbers,strings",
    [
        (DashboardQueryBuilder, "span_attr_num", "span_attr_str"),
        (DashboardQueryBuilderV2, "attrs_number", "attrs_string"),
    ],
)
@pytest.mark.parametrize("site", ["period", "filter", "metric_filter", "breakdown"])
def test_ttft_reads_seconds_only_when_traceai_marks_the_unit(
    builder_type, numbers, strings, site
):
    """A span marked unit "s" carries seconds and reads x1000 as ms.

    Unmarked spans, or any other marker value, read as stored milliseconds.
    Every TTFT site renders the one shared read, never a guess from size.
    """
    ttft = {
        "id": "time_to_first_token",
        "name": "time_to_first_token",
        "aggregation": "avg",
    }
    tokens = {"id": "tokens", "name": "tokens", "aggregation": "avg"}
    if site == "period":
        query = config([ttft], [{"type": "system_metric", "name": "model"}])
    elif site == "metric_filter":
        query = config([{**tokens, "filters": [_TTFT_SYSTEM_FILTER]}])
    elif site == "breakdown":
        query = config([tokens], [_TTFT_SYSTEM_BREAKDOWN])
    else:
        query = config([tokens])
        query["filters"] = [_TTFT_SYSTEM_FILTER]
    builder = builder_type(query)
    builder.include_period_aggregate = site == "period"
    sql, _ = builder.build_metric_query(builder.metrics[0])
    assert ttft_read(numbers, strings) in sql
    # The marker is only ever compared with exactly "s".
    marker = f"{strings}['traceai.timing.server_ttft.unit']"
    assert sql.count(marker) == sql.count(f"{marker} = 's'") > 0


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


# Every trace dashboard snapshot cached before TTFT had a unit version is keyed
# by exactly these identity fields.
_PRE_TTFT_UNIT_IDENTITY_FIELDS = (
    "workspace_id",
    "query_config",
    "trace_snapshot_semantics",
    "trace_period_aggregate_semantics",
)


def _complete_snapshot(label):
    return {
        "metrics": [],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
        "label": label,
    }


def _read_through_snapshot_cache(query, snapshots):
    """Run one dashboard read against an in-memory snapshot cache.

    A key holding no snapshot answers a complete "recomputed" one. It stands in
    for the fresh exact read, so the read ends at the cache and no ClickHouse
    runs. Returns the (namespace, identity) looked up and the label served.
    """
    workspace = SimpleNamespace(
        id="22222222-2222-4222-8222-222222222222",
        organization_id="33333333-3333-4333-8333-333333333333",
    )
    lookups = []

    def lookup(namespace, identity, **_kwargs):
        lookups.append((namespace, identity))
        key = snapshot_cache_key(namespace, identity)
        return snapshots.get(key) or _complete_snapshot("recomputed")

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
            "tracer.views.dashboard.read_or_schedule_exact_snapshot",
            side_effect=lookup,
        ),
    ):
        response = DashboardWidgetViewSet()._execute_ch_query_config(query, workspace)
    assert response.status_code == 200
    assert len(lookups) == 1
    return lookups[0], response.data["result"]["label"]


def _pre_ttft_unit_snapshots(query):
    """Cache a "pre-fix" snapshot under the key this query had before."""
    (namespace, identity), _ = _read_through_snapshot_cache(query, {})
    old_identity = {field: identity[field] for field in _PRE_TTFT_UNIT_IDENTITY_FIELDS}
    return {snapshot_cache_key(namespace, old_identity): _complete_snapshot("pre-fix")}


def _ttft_query(role):
    """A trace dashboard query that reads TTFT in the given role."""
    query = {**config(), "filters": []}
    # Eval metrics default to the traces source, so they run on the trace
    # builder, whose eval span filters and breakdowns read the TTFT expression.
    eval_metric = {
        "id": "toxicity",
        "name": "toxicity",
        "type": "eval_metric",
        "aggregation": "avg",
    }
    ttft_breakdown = {"type": "system_metric", "name": "time_to_first_token"}
    if role == "metric":
        query["metrics"] = [
            {
                "id": "time_to_first_token",
                "name": "time_to_first_token",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ]
    elif role == "breakdown":
        query["breakdowns"] = [ttft_breakdown]
    elif role == "eval_metric_filter":
        ttft_filter = {
            "column_id": "time_to_first_token",
            "source": "traces",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 35,
                "col_type": "SYSTEM_METRIC",
            },
        }
        query["metrics"] = [{**eval_metric, "filters": [ttft_filter]}]
    else:
        query["metrics"] = [eval_metric]
        query["breakdowns"] = [ttft_breakdown]
    return query


@pytest.mark.parametrize(
    "role", ["metric", "breakdown", "eval_metric_filter", "eval_metric_breakdown"]
)
def test_pre_fix_ttft_snapshot_misses_after_the_unit_fix(role):
    query = _ttft_query(role)
    snapshots = _pre_ttft_unit_snapshots(query)
    # The pre-fix snapshot holds TTFT 1000x too large; it must not be served.
    (_, identity), served = _read_through_snapshot_cache(query, snapshots)
    assert served == "recomputed"
    # Imported here so the file still collects before the constant exists.
    from tracer.services.clickhouse.query_builders.dashboard import TTFT_UNIT_VERSION

    assert identity["trace_ttft_unit_version"] == TTFT_UNIT_VERSION


def test_non_ttft_snapshot_still_hits_after_the_ttft_unit_fix():
    query = {**config(), "filters": []}
    snapshots = _pre_ttft_unit_snapshots(query)
    (_, identity), served = _read_through_snapshot_cache(query, snapshots)
    # Same key as before the fix: dashboards that never read TTFT stay warm.
    assert served == "pre-fix"
    assert set(identity) == set(_PRE_TTFT_UNIT_IDENTITY_FIELDS)


@pytest.mark.parametrize("where", ["filters", "metric_filters"])
def test_ttft_filter_queries_also_get_the_ttft_snapshot_marker(where):
    from tracer.views.dashboard import _dashboard_query_names_ttft

    ttft_filter = {
        "metric_type": "system_metric",
        "metric_name": "time_to_first_token",
        "operator": "greater_than",
        "value": 35,
        "source": "traces",
    }
    query = {**config(), "filters": []}
    assert not _dashboard_query_names_ttft(query)
    if where == "filters":
        query["filters"] = [ttft_filter]
    else:
        query["metrics"] = [{**query["metrics"][0], "filters": [ttft_filter]}]
    assert _dashboard_query_names_ttft(query)


@pytest.mark.parametrize(
    "role", ["metric", "breakdown", "eval_metric_filter", "eval_metric_breakdown"]
)
def test_ttft_snapshot_cached_under_unit_version_2_misses(role):
    query = _ttft_query(role)
    (namespace, identity), _ = _read_through_snapshot_cache(query, {})
    # Unit version 2 read every span as milliseconds, so a snapshot cached then
    # shows seconds-marked spans 1000x too small. It must not be served.
    key = snapshot_cache_key(namespace, {**identity, "trace_ttft_unit_version": 2})
    _, served = _read_through_snapshot_cache(query, {key: _complete_snapshot("v2")})
    assert served == "recomputed"


def _attribute_filter(key, values):
    """A custom-attribute filter whose raw witness engages the exact replay."""
    return {
        "metric_type": "custom_attribute",
        "metric_name": key,
        "operator": "in",
        "value": list(values),
        "attribute_type": "string",
        "source": "traces",
        "canonical_filter": {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": list(values),
                "attribute_value_types": ["string"] * len(values),
            },
        },
    }


def _latest_state_replay(query):
    """Build the v2 latest-state SQL; return its narrowed keys and the SQL."""
    builder = DashboardQueryBuilderV2(query)
    sql, _ = builder._build_metric_query_for_snapshot_mode(
        builder.metrics[0], latest_state=True
    )
    narrowed = re.search(
        r"mapFilter\(\(k, v\) -> \(k IN \((?P<keys>.*?)\)\), "
        r"dashboard_candidate_source\.attrs_string\)",
        sql,
    )
    assert narrowed is not None, "the exact replay source did not engage"
    return set(re.findall(r"'([^']*)'", narrowed.group("keys"))), sql


@pytest.mark.parametrize("role", ["metric", "filter", "metric_filter", "breakdown"])
def test_v2_exact_replay_carries_the_ttft_unit_key_whenever_ttft_is_read(role):
    """The replay winner narrows attrs_string to the keys the outer query reads.

    A key it drops reads as '' there, so a seconds-marked span would silently
    read as milliseconds.
    """
    ttft = {
        "id": "time_to_first_token",
        "name": "time_to_first_token",
        "type": "system_metric",
        "aggregation": "avg",
    }
    tokens = {**ttft, "id": "tokens", "name": "tokens"}
    breakdowns = [_TTFT_SYSTEM_BREAKDOWN] if role == "breakdown" else []
    if role == "metric":
        metric = ttft
    elif role == "metric_filter":
        metric = {**tokens, "filters": [_TTFT_SYSTEM_FILTER]}
    else:
        metric = tokens
    query = config([metric], breakdowns)
    query["filters"] = [_attribute_filter("workflow_stage", ["intake", "review"])]
    if role == "filter":
        query["filters"].append(_TTFT_SYSTEM_FILTER)
    carried, sql = _latest_state_replay(query)
    assert "traceai.timing.server_ttft.unit" in carried
    # The replay source names only the bare key; this read is the outer query's.
    assert "attrs_string['traceai.timing.server_ttft.unit'] = 's'" in sql


def test_v2_exact_replay_keeps_its_keys_when_ttft_is_not_read():
    tokens = {
        "id": "tokens",
        "name": "tokens",
        "type": "system_metric",
        "aggregation": "avg",
    }
    query = config([tokens])
    query["filters"] = [_attribute_filter("workflow_stage", ["intake", "review"])]
    carried, sql = _latest_state_replay(query)
    assert carried == {"workflow_stage"}
    assert "traceai.timing.server_ttft.unit" not in sql
