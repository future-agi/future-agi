"""Offline compiler contracts, not SQL execution or complete voice UI parity."""

import re
import socket
from copy import deepcopy

import pytest

from tracer.services.clickhouse.query_builders.dashboard import (
    SYSTEM_METRICS,
    DashboardQueryBuilder,
    InvalidMetricCombinationError,
)
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.simulation_dashboard import (
    SimulationQueryBuilder,
)
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    canonical_system_definitions,
    definition_metric,
)
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.utils.property_registry import (
    parse_property_registry_id,
    validate_property_filter_binding,
    validate_property_metric_binding,
)

pytestmark = pytest.mark.unit
NAME = "agent_talk_percentage"
IDENTITY = "system_attribute:traces:agent_talk_percentage"
PROJECT = "11111111-1111-4111-8111-111111111111"
SIBLING = "22222222-2222-4222-8222-222222222222"
FOREIGN = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("offline dashboard compiler test attempted network access")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture(
    params=[DashboardQueryBuilder, DashboardQueryBuilderV2], ids=["legacy", "v2"]
)
def builder(request):
    return request.param


@pytest.fixture(
    params=[(PROJECT,), (PROJECT, SIBLING)], ids=["fixed", "authorized-projects"]
)
def config(request):
    # The caller authorizes this finite project set; the compiler must preserve it.
    return {
        "project_ids": list(request.param),
        "granularity": "day",
        "time_range": {
            "custom_start": "2026-09-01T00:00:00Z",
            "custom_end": "2026-09-02T00:00:00Z",
        },
        "filters": [],
        "breakdowns": [],
    }


def _metric(name=NAME, aggregation="avg", source="traces"):
    definition = next(
        item
        for item in canonical_system_definitions()
        if item.primary_source == source and item.name == name
    )
    catalog = definition_metric(definition)
    identity = f"system_attribute:{source}:{name}"
    assert catalog["property_id"] == identity
    assert parse_property_registry_id(identity)["metric_name"] == name
    assert catalog["role"] == "metric"
    validate_property_metric_binding(
        identity, metric_name=name, metric_type="system_metric", source=source
    )
    return {
        "id": name,
        "name": name,
        "display_name": catalog["display_name"],
        "property_id": identity,
        "type": "system_metric",
        "source": source,
        "aggregation": aggregation,
    }


def _expected_expression(builder):
    shared = ClickHouseFilterBuilder.VOICE_PUBLIC_ROOT_SYSTEM_METRIC_EXPRS[NAME]
    # Check the final derived value, not the raw ratio or an aggregate result.
    expression = f"if(isFinite({shared}), {shared}, null)"
    if builder is DashboardQueryBuilderV2:
        expression = expression.replace("span_attr_num", "attrs_number")
    return expression


def _build(builder, metric, config):
    before = deepcopy((metric, config))
    instance = builder(config)
    sql, params = instance.build_metric_query(metric)
    assert (metric, config) == before
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys()
    assert "project_id IN %(project_ids)s" in sql
    assert params["project_ids"] == config["project_ids"]
    assert FOREIGN not in str(params) and FOREIGN not in sql
    assert "start_time >= %(start_date)s" in sql
    assert "start_time < %(end_date)s" in sql
    deleted = (
        "is_deleted" if builder is DashboardQueryBuilderV2 else "_peerdb_is_deleted"
    )
    assert f"{deleted} = 0" in sql
    return instance, sql, params


def test_agent_talk_reuses_shared_root_math_with_final_finite_guard():
    shared = ClickHouseFilterBuilder.VOICE_PUBLIC_ROOT_SYSTEM_METRIC_EXPRS[NAME]
    raw = ClickHouseFilterBuilder.VOICE_SYSTEM_METRIC_EXPRS[NAME]
    assert raw in shared
    assert "parent_span_id IS NULL OR parent_span_id = ''" in shared
    assert "observation_type = 'conversation'" in shared
    assert "mapContains(span_attr_num, 'call.talk_ratio')" in shared
    assert "span_attr_num['call.talk_ratio'] >= 0" in shared  # Zero stays a value.
    assert "(span_attr_num['call.talk_ratio'] + 1) * 100, 2)" in shared
    assert "isFinite" not in shared  # Do not mutate the shared voice contract.
    assert SYSTEM_METRICS[NAME] == (
        "spans",
        _expected_expression(DashboardQueryBuilder),
    )


@pytest.mark.parametrize(
    "aggregation,template",
    [
        ("avg", "avg({col})"),
        ("count", "count()"),
        ("p95", "quantileExact(0.95)({col})"),
        ("sum", "sum({col})"),
    ],
)
def test_agent_talk_metric_has_local_eligibility_and_no_second_rescale(
    builder, config, aggregation, template
):
    metric = _metric(aggregation=aggregation)
    instance, sql, _ = _build(builder, metric, config)
    expression = _expected_expression(builder)
    assert f"{template.format(col=expression)} AS value" in sql
    assert f"isNotNull({expression})" in sql.split("WHERE", 1)[1]
    assert "custom_metric_attr_key" not in sql
    result = instance.format_results([(instance.metric_info(metric), [])])["metrics"][0]
    assert metric["property_id"] == IDENTITY
    assert result["id"] == NAME and result["unit"] == "%"


@pytest.mark.parametrize("placement", ["global", "per-metric"])
@pytest.mark.parametrize(
    "operator,value,suffix",
    [
        ("equal_to", 0, "= %(f_0_val)s"),
        ("greater_than", 75, "> %(f_0_val)s"),
        ("is_set", None, "IS NOT NULL"),
        ("is_not_set", None, "IS NULL"),
    ],
)
def test_agent_talk_filter_keeps_nullable_root_expression_and_bound_value(
    builder, config, placement, operator, value, suffix
):
    validate_property_filter_binding(
        IDENTITY, column_id=NAME, column_type="SYSTEM_METRIC", source="traces"
    )
    condition = {
        "property_id": IDENTITY,
        "metric_type": "system_metric",
        "metric_name": NAME,
        "source": "traces",
        "operator": operator,
        "value": value,
    }
    metric = _metric("cost", "count")
    (metric if placement == "per-metric" else config)["filters"] = [condition]
    _, sql, params = _build(builder, metric, config)
    expression = _expected_expression(builder)
    assert f"{expression} {suffix}" in sql
    assert f"isNotNull({expression})" not in sql  # In particular, retain is_not_set.
    assert "count() AS value" in sql
    if value is not None:
        assert params["f_0_val"] == value


def test_agent_talk_breakdown_does_not_drop_other_metric_null_bucket(builder, config):
    metric = _metric()
    config["breakdowns"] = [
        {key: metric[key] for key in ("name", "property_id", "type", "source")}
    ]
    _, sql, _ = _build(builder, _metric("cost", "count"), config)
    expression = _expected_expression(builder)
    assert f"{expression} AS breakdown_value" in sql
    assert "count() AS value" in sql
    assert "GROUP BY time_bucket, breakdown_value" in sql
    assert f"isNotNull({expression})" not in sql


@pytest.mark.parametrize(
    "name,aggregation,expected",
    [
        ("cost", "avg", "avg(cost)"),
        ("cost", "count", "count()"),
        ("input_tokens", "sum", "sum(prompt_tokens)"),
        ("latency", "avg", "avg(latency_ms)"),
        (
            "error_rate",
            "avg",
            "(avg(CASE WHEN status='ERROR' THEN 1.0 ELSE 0.0 END)) * 100",
        ),
    ],
)
def test_other_metric_domains_and_rate_scaling_are_unchanged(
    builder, config, name, aggregation, expected
):
    _, sql, _ = _build(builder, _metric(name, aggregation), config)
    assert f"{expected} AS value" in sql
    assert "call.talk_ratio" not in sql
    assert "isFinite" not in sql
    assert ("parent_span_id" in sql) == (name == "latency")


@pytest.mark.parametrize("latest_state", [False, True], ids=["raw", "latest"])
def test_v2_grouping_keeps_agent_population_separate_from_cost(config, latest_state):
    config["metrics"] = [_metric(aggregation="count"), _metric("cost", "count")]
    assert (
        DashboardQueryBuilderV2(config).build_compatible_metric_group_query(
            latest_state=latest_state
        )
        is None
    )
    config["metrics"] = [_metric(), _metric(aggregation="count")]
    group = DashboardQueryBuilderV2(config).build_compatible_metric_group_query(
        latest_state=latest_state
    )
    assert group is not None
    assert f"isNotNull({_expected_expression(DashboardQueryBuilderV2)})" in group.sql
    assert group.params["project_ids"] == config["project_ids"]


@pytest.mark.parametrize("source", ["datasets", "simulation"])
def test_trace_agent_talk_identity_does_not_bind_other_sources(source):
    with pytest.raises(ValueError, match="not compatible with source"):
        validate_property_metric_binding(
            IDENTITY, metric_name=NAME, metric_type="system_metric", source=source
        )


def test_legacy_raw_ratio_is_not_promoted_to_catalog_system_metric(builder, config):
    metric = _metric()
    metric.update(
        id="call.talk_ratio",
        name="call.talk_ratio",
        property_id="system_attribute:traces:call.talk_ratio",
    )
    with pytest.raises(
        InvalidMetricCombinationError, match="Unsupported cataloged system metric"
    ):
        builder(config).build_metric_query(metric)


def test_simulation_talk_ratio_retains_its_separate_formula(config):
    metric = _metric("talk_ratio", source="simulation")
    sql, _ = SimulationQueryBuilder(config).build_metric_query(metric)
    assert metric["property_id"] == "system_attribute:simulation:talk_ratio"
    assert (
        "avg(if(talk_ratio IS NULL OR talk_ratio <= 0, CAST(NULL, 'Nullable(Float64)'), (talk_ratio / (talk_ratio + 1)) * 100)) AS value"
        in sql
    )
    assert "isFinite" not in sql and "call.talk_ratio" not in sql
