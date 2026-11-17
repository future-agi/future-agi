"""Dashboard numeric TTFT filters distinguish no measurement from measured zero."""

import pytest

from tracer.services.clickhouse.query_builders.dashboard import (
    SYSTEM_METRICS,
    TTFT_UNIT_VERSION,
    DashboardQueryBuilder,
)
from tracer.services.clickhouse.query_builders.ttft import (
    TTFT_ATTRIBUTE,
    TTFT_FILTER_EXPRESSION,
    TTFT_VALUE,
)
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.filters import rewrite_v1_sql_to_v2
from tracer.services.exact_aggregation_cache import snapshot_cache_key

pytestmark = pytest.mark.unit
BUILDERS = [DashboardQueryBuilder, DashboardQueryBuilderV2]


def _filter(operator="less_than", value=500, name="time_to_first_token"):
    return {
        "metric_type": "system_metric",
        "metric_name": name,
        "operator": operator,
        "value": value,
        "source": "traces",
    }


def _query(site="global", filters=None):
    filters = [_filter()] if filters is None else filters
    metric = {"name": "latency", "type": "system_metric", "aggregation": "avg"}
    if site == "metric":
        metric["filters"] = filters
    return {
        "project_ids": ["11111111-1111-4111-8111-111111111111"],
        "time_range": {
            "custom_start": "2026-11-01T00:00:00+00:00",
            "custom_end": "2026-11-03T00:00:00+00:00",
        },
        "granularity": "day",
        "metrics": [metric],
        "filters": filters if site == "global" else [],
        "breakdowns": [],
    }


def _public_predicate(builder_type, predicate):
    if builder_type is DashboardQueryBuilderV2:
        return rewrite_v1_sql_to_v2(predicate)
    return predicate


@pytest.mark.parametrize("builder_type", BUILDERS)
@pytest.mark.parametrize("site", ["global", "metric"])
@pytest.mark.parametrize(
    ("operator", "value", "suffix", "expected_params"),
    [
        ("less_than", 500, "< %(f_0_val)s", {"f_0_val": 500}),
        ("greater_than", 500, "> %(f_0_val)s", {"f_0_val": 500}),
        ("equal_to", 0, "= %(f_0_val)s", {"f_0_val": 0}),
        ("not_equal_to", 500, "!= %(f_0_val)s", {"f_0_val": 500}),
        ("greater_than_or_equal", 200, ">= %(f_0_val)s", {"f_0_val": 200}),
        ("less_than_or_equal", 500, "<= %(f_0_val)s", {"f_0_val": 500}),
        (
            "between",
            [0, 500],
            "BETWEEN %(f_0_lo)s AND %(f_0_hi)s",
            {"f_0_lo": 0, "f_0_hi": 500},
        ),
        (
            "not_between",
            [500, 800],
            "NOT BETWEEN %(f_0_lo)s AND %(f_0_hi)s",
            {"f_0_lo": 500, "f_0_hi": 800},
        ),
    ],
    ids=[
        "lt500",
        "gt500",
        "eq0",
        "ne500",
        "ge200",
        "le500",
        "range0to500",
        "outside500to800",
    ],
)
def test_numeric_ttft_predicate_is_nullable_in_latency_query(
    builder_type, site, operator, value, suffix, expected_params
):
    builder = builder_type(_query(site, [_filter(operator, value)]))
    metric = builder.metrics[0]
    clauses, params = builder._build_where_clauses(
        "spans", "start_time", metric.get("filters", []), {}
    )
    predicate = f"{TTFT_FILTER_EXPRESSION} {suffix}"
    assert [clause for clause in clauses if TTFT_ATTRIBUTE in clause] == [predicate]
    assert params == expected_params

    sql, public_params = builder.build_metric_query(metric)
    active_where = sql.split("\nWHERE ", 1)[1].split("\nGROUP BY ", 1)[0]
    assert _public_predicate(builder_type, predicate) in active_where
    assert {key: public_params[key] for key in expected_params} == expected_params
    assert "avg(latency_ms) AS value" in sql


@pytest.mark.parametrize("builder_type", BUILDERS)
def test_global_and_metric_filters_keep_separate_parameter_scope(builder_type):
    query = _query("global", [_filter("greater_than", 100)])
    query["metrics"][0]["filters"] = [_filter("less_than", 500)]
    query["metrics"].append(
        {"name": "tokens", "type": "system_metric", "aggregation": "avg"}
    )
    builder = builder_type(query)
    latency_sql, latency_params = builder.build_metric_query(builder.metrics[0])
    tokens_sql, tokens_params = builder.build_metric_query(builder.metrics[1])
    assert "> %(f_0_val)s" in latency_sql and "> %(f_0_val)s" in tokens_sql
    assert "< %(f_1_val)s" in latency_sql and "f_1_val" not in tokens_sql
    assert latency_params["f_0_val"] == tokens_params["f_0_val"] == 100
    assert latency_params["f_1_val"] == 500 and "f_1_val" not in tokens_params
    assert "avg(latency_ms) AS value" in latency_sql
    assert "avg(total_tokens) AS value" in tokens_sql


@pytest.mark.parametrize("builder_type", BUILDERS)
@pytest.mark.parametrize(
    ("operator", "suffix"),
    [
        ("is_set", "!= ''"),
        ("is_not_set", "= ''"),
        ("is_numeric", "!= 0"),
        ("is_not_numeric", "= 0"),
    ],
)
def test_no_value_ttft_operators_retain_their_legacy_sql(
    builder_type, operator, suffix
):
    builder = builder_type(_query(filters=[_filter(operator, None)]))
    clauses, params = builder._build_where_clauses("spans", "start_time", [], {})
    predicate = f"{TTFT_VALUE} {suffix}"
    assert [clause for clause in clauses if TTFT_ATTRIBUTE in clause] == [predicate]
    assert params == {}
    sql, _ = builder.build_metric_query(builder.metrics[0])
    assert _public_predicate(builder_type, predicate) in sql
    # Deliberately do not execute the legacy numeric-to-empty-string comparison.


@pytest.mark.parametrize("builder_type", BUILDERS)
def test_unrelated_numeric_filter_remains_unchanged(builder_type):
    builder = builder_type(_query(filters=[_filter("less_than", 123, "latency")]))
    clauses, params = builder._build_where_clauses("spans", "start_time", [], {})
    assert clauses[-1] == "latency_ms < %(f_0_val)s"
    assert params == {"f_0_val": 123}
    sql, _ = builder.build_metric_query(builder.metrics[0])
    assert "avg(latency_ms) AS value" in sql
    assert "latency_ms < %(f_0_val)s" in sql
    assert TTFT_ATTRIBUTE not in sql


@pytest.mark.parametrize("builder_type", BUILDERS)
def test_ttft_aggregation_keeps_its_existing_value_and_presence_guard(builder_type):
    query = _query(filters=[])
    query["metrics"][0]["name"] = "time_to_first_token"
    builder = builder_type(query)
    sql, _ = builder.build_metric_query(builder.metrics[0])
    assert SYSTEM_METRICS["time_to_first_token"] == ("spans", TTFT_VALUE)
    assert _public_predicate(builder_type, f"avg({TTFT_VALUE}) AS value") in sql
    assert _public_predicate(
        builder_type, f"mapContains(span_attr_num, '{TTFT_ATTRIBUTE}')"
    ) in sql.split("\nWHERE ", 1)[1]
    assert _public_predicate(builder_type, TTFT_FILTER_EXPRESSION) not in sql


@pytest.mark.parametrize("site", ["global", "metric"])
def test_pre_nullable_ttft_filter_snapshot_is_not_reused(site):
    # Reuse the existing view-level cache harness, not a copied cache-key rule.
    from tracer.tests.test_dashboard_period_aggregate_sweep import (
        _complete_snapshot,
        _read_through_snapshot_cache,
    )

    query = _query(site)
    (namespace, identity), _ = _read_through_snapshot_cache(query, {})
    assert identity["trace_ttft_unit_version"] == TTFT_UNIT_VERSION
    old_key = snapshot_cache_key(namespace, {**identity, "trace_ttft_unit_version": 3})
    _, served = _read_through_snapshot_cache(
        query, {old_key: _complete_snapshot("pre-nullable-filter")}
    )
    assert served == "recomputed"
    assert snapshot_cache_key(namespace, identity) != old_key
