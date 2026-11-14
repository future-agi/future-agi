"""Registry-bound token execution, without clients, ORM or application startup.

Filters use the dashboard's normalized internal shape after validating the
corresponding public registry binding. This is compiler, not HTTP/SQL execution,
coverage; dataset tokens retain their separate namespace and dimension limits.
"""

import re
import socket
from copy import deepcopy

import pytest

from tracer.services.clickhouse.query_builders.dashboard import (
    DashboardQueryBuilder,
    InvalidMetricCombinationError,
)
from tracer.services.clickhouse.query_builders.dataset_dashboard import (
    DatasetQueryBuilder,
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
PROJECT = "11111111-1111-4111-8111-111111111111"
TOKENS = (
    ("prompt_tokens", "prompt_tokens"),
    ("completion_tokens", "completion_tokens"),
    ("total_tokens", "total_tokens"),
    ("input_tokens", "prompt_tokens"),
    ("output_tokens", "completion_tokens"),
    ("tokens", "total_tokens"),
)


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("offline token compiler test attempted network access")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture(
    params=[DashboardQueryBuilder, DashboardQueryBuilderV2], ids=["legacy", "v2"]
)
def builder(request):
    return request.param


def _metric(name, source="traces"):
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
        "aggregation": "sum",
    }


def _config():
    return {
        "project_ids": [PROJECT],
        "granularity": "day",
        "time_range": {
            "custom_start": "2026-09-01T00:00:00Z",
            "custom_end": "2026-09-02T00:00:00Z",
        },
        "filters": [],
        "breakdowns": [],
    }


def _build(builder, metric, config):
    before = deepcopy((metric, config))
    instance = builder(config)
    sql, params = instance.build_metric_query(metric)
    assert (metric, config) == before
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys()
    assert params["project_ids"] == [PROJECT]
    return instance, sql, params


@pytest.mark.parametrize("name,column", TOKENS)
def test_registry_bound_token_metric_uses_native_column_and_keeps_identity(
    builder, name, column
):
    metric = _metric(name)
    instance, sql, _ = _build(builder, metric, _config())
    assert f"sum({column}) AS value" in sql
    assert "custom_metric_attr_key" not in sql
    assert (
        "parent_span_id" not in sql
    )  # Tokens aggregate all scoped spans, not roots only.
    result = instance.format_results([(instance.metric_info(metric), [])])["metrics"][0]
    assert result["id"] == name
    assert result["unit"] == "tokens"


@pytest.mark.parametrize("placement", ["global", "per-metric"])
@pytest.mark.parametrize("name,column", TOKENS)
def test_registry_bound_token_filter_uses_native_numeric_column(
    builder, name, column, placement
):
    identity = _metric(name)["property_id"]
    validate_property_filter_binding(
        identity, column_id=name, column_type="SYSTEM_METRIC", source="traces"
    )
    condition = {
        "property_id": identity,
        "metric_type": "system_metric",
        "metric_name": name,
        "source": "traces",
        "operator": "greater_than",
        "value": 17,
        "canonical_filter": {
            "column_id": name,
            "property_id": identity,
            "source": "traces",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 17,
            },
        },
    }
    metric, config = _metric("cost"), _config()
    (metric if placement == "per-metric" else config)["filters"] = [condition]
    _, sql, params = _build(builder, metric, config)
    assert f"{column} > %(f_0_val)s" in sql
    assert params["f_0_val"] == 17


@pytest.mark.parametrize("name,column", TOKENS)
def test_registry_bound_token_breakdown_uses_native_numeric_column(
    builder, name, column
):
    token = _metric(name)
    config = _config()
    config["breakdowns"] = [
        {key: token[key] for key in ("name", "property_id", "type", "source")}
    ]
    _, sql, _ = _build(builder, _metric("cost"), config)
    assert column in sql.split("FROM", 1)[0]
    assert "AS breakdown_value" in sql and "GROUP BY" in sql
    assert "custom_metric_attr_key" not in sql


@pytest.mark.parametrize(
    "name,expression",
    [
        ("prompt_tokens", "sum(prompt_tokens)"),
        ("completion_tokens", "sum(completion_tokens)"),
        (
            "total_tokens",
            "sum(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0))",
        ),
    ],
)
def test_dataset_token_identity_keeps_its_cell_expression_and_dimension_limits(
    name, expression
):
    metric, config = _metric(name, "datasets"), _config()
    before = deepcopy((metric, config))
    sql, _ = DatasetQueryBuilder(config).build_metric_query(metric)
    assert f"{expression} AS value" in sql
    assert "FROM model_hub_cell AS c FINAL" in sql
    assert (metric, config) == before
    with pytest.raises(ValueError, match="not compatible with source"):
        validate_property_metric_binding(
            metric["property_id"],
            metric_name=name,
            metric_type="system_metric",
            source="traces",
        )
    config["breakdowns"] = [
        {key: metric[key] for key in ("name", "property_id", "type", "source")}
    ]
    with pytest.raises(
        InvalidMetricCombinationError, match="Unsupported dataset breakdown dimension"
    ):
        DatasetQueryBuilder(config).build_metric_query(metric)


@pytest.mark.parametrize("role", ["metric", "filter", "breakdown"])
def test_unknown_registry_bound_trace_system_field_still_fails_closed(builder, role):
    name = "not_a_supported_token_metric"
    identity = f"system_attribute:traces:{name}"
    metric, config = _metric("cost"), _config()
    if role == "metric":
        metric.update(id=name, name=name, property_id=identity)
    elif role == "filter":
        config["filters"] = [
            {
                "metric_name": name,
                "property_id": identity,
                "metric_type": "system_metric",
                "source": "traces",
                "operator": "greater_than",
                "value": 17,
            }
        ]
    else:
        config["breakdowns"] = [
            {
                "name": name,
                "property_id": identity,
                "type": "system_metric",
                "source": "traces",
            }
        ]
    with pytest.raises(
        InvalidMetricCombinationError,
        match=f"Unsupported cataloged system {role}: {name}",
    ):
        builder(config).build_metric_query(metric)
