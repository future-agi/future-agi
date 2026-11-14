"""Focused contracts for dashboard eval/annotation presence filters."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tracer.serializers.dashboard import DashboardQuerySerializer
from tracer.services.clickhouse.query_builders.dashboard import (
    DashboardQueryBuilder,
    InvalidMetricCombinationError,
)
from tracer.services.clickhouse.query_builders.filters import (
    parse_boolean_meta_filter,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.views.dashboard import _normalize_dashboard_query_filters


def _presence_filter(name: str, value: bool, filter_op: str = "equals") -> dict:
    return {
        "column_id": name,
        "property_id": f"system_attribute:traces:{name}",
        "source": "traces",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "boolean",
            "filter_op": filter_op,
            "filter_value": value,
        },
    }


def _query(filters: list[dict]) -> dict:
    return {
        "workflow": "observability",
        "project_ids": [str(uuid4())],
        "time_range": {"preset": "7D"},
        "granularity": "day",
        "metrics": [
            {
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ],
        "filters": filters,
    }


def _validated_query(filters: list[dict]) -> dict:
    serializer = DashboardQuerySerializer(data=_query(filters))
    assert serializer.is_valid(), serializer.errors
    normalized = _normalize_dashboard_query_filters(serializer.validated_data)
    # Query scope is authorized/materialized by the view after body validation.
    normalized["organization_id"] = str(uuid4())
    normalized["workspace_id"] = str(uuid4())
    if any(item.get("column_id") == "has_annotation" for item in filters):
        normalized["annotation_label_ids_by_project"] = {
            normalized["project_ids"][0]: [str(uuid4()), str(uuid4())]
        }
    return normalized


@pytest.mark.parametrize("required", [True, False])
def test_has_eval_boolean_filter_is_registry_bound_and_compiled(required):
    config = _validated_query([_presence_filter("has_eval", required)])

    normalized = config["filters"][0]
    assert normalized["property_id"] == "system_attribute:traces:has_eval"
    assert normalized["metric_name"] == "has_eval"
    sql, _params, _metric = DashboardQueryBuilderV2(config).build_all_queries()[0]

    membership = " NOT IN " if not required else " IN "
    assert membership in sql
    assert "FROM tracer_eval_logger" in sql
    assert " AS eval_scan" in sql
    assert "LIMIT 1 BY eval_scan.id" in sql
    assert "dashboard_presence_traces" in sql
    assert "tuple(toString(spans.project_id), toString(spans.trace_id))" in sql


@pytest.mark.parametrize("required", [True, False])
def test_has_annotation_boolean_filter_is_registry_bound_and_compiled(required):
    config = _validated_query([_presence_filter("has_annotation", required)])

    normalized = config["filters"][0]
    assert normalized["property_id"] == "system_attribute:traces:has_annotation"
    assert normalized["metric_name"] == "has_annotation"
    sql, _params, _metric = DashboardQueryBuilderV2(config).build_all_queries()[0]

    membership = " NOT IN " if not required else " IN "
    assert membership in sql
    assert "FROM model_hub_score AS annotation_presence FINAL" in sql
    assert "annotation_presence.tracer_project_id =" in sql
    assert "annotation_presence_trace.project_id" in sql
    assert "annotation_presence_span.trace_id" in sql
    assert "annotation_presence.organization_id =" in sql
    assert "HAVING uniqExact(annotation_presence.label_id) =" in sql
    assert 2 in _params.values()
    assert "tuple(toString(spans.project_id), toString(spans.trace_id))" in sql


def test_has_annotation_empty_authoritative_label_set_is_vacuously_complete():
    config = _validated_query([_presence_filter("has_annotation", True)])
    project_id = str(config["project_ids"][0])
    config["annotation_label_ids_by_project"] = {project_id: []}

    sql, params, _metric = DashboardQueryBuilderV2(config).build_all_queries()[0]

    assert "annotation_empty_label_traces" in sql
    assert "FROM model_hub_score AS annotation_presence FINAL" not in sql
    assert params["annotation_presence_empty_projects"] == (project_id,)


def test_has_annotation_refuses_missing_authoritative_label_metadata():
    config = _validated_query([_presence_filter("has_annotation", True)])
    config.pop("annotation_label_ids_by_project")

    with pytest.raises(
        InvalidMetricCombinationError,
        match="completeness metadata is unavailable",
    ):
        DashboardQueryBuilderV2(config).build_all_queries()


def test_f7_conjoins_custom_eval_annotation_values_and_both_presence_filters():
    eval_id = str(uuid4())
    annotation_id = str(uuid4())
    filters = [
        {
            "column_id": "customer.tier",
            "property_id": "custom_attribute:customer.tier",
            "source": "traces",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "gold",
            },
        },
        {
            "column_id": eval_id,
            "source": "traces",
            "output_type": "SCORE",
            "filter_config": {
                "col_type": "EVAL_METRIC",
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 0.7,
            },
        },
        {
            "column_id": annotation_id,
            "property_id": f"annotation:{annotation_id}",
            "source": "traces",
            "output_type": "categorical",
            "filter_config": {
                "col_type": "ANNOTATION",
                "filter_type": "categorical",
                "filter_op": "equals",
                "filter_value": "accepted",
            },
        },
        _presence_filter("has_eval", True),
        _presence_filter("has_annotation", True),
    ]
    config = _validated_query(filters)

    sql, params, _metric = DashboardQueryBuilderV2(config).build_all_queries()[0]

    assert "mapContains(attrs_string, %(latest_filter_key_0)s)" in sql
    assert params["latest_filter_key_0"] == "customer.tier"
    assert "FROM usage_apicalllog AS usage_s_eval_filter_scan_" in sql
    assert "FROM model_hub_score AS annotation_s_filter_" in sql
    assert "FROM tracer_eval_logger" in sql
    assert " AS eval_scan" in sql
    assert "FROM model_hub_score AS annotation_presence FINAL" in sql
    assert sql.count("tuple(toString(spans.project_id), toString(spans.trace_id))") == 2
    assert 0.7 in params.values()
    assert "accepted" in params.values()


@pytest.mark.parametrize("name", ["has_eval", "has_annotation"])
@pytest.mark.parametrize("required", [True, False])
def test_presence_filter_not_equals_negates_the_requested_value(name, required):
    config = _validated_query([_presence_filter(name, required, "not_equals")])

    sql, _params, _metric = DashboardQueryBuilderV2(config).build_all_queries()[0]

    assert (" IN " if not required else " NOT IN ") in sql


@pytest.mark.parametrize("name", ["has_eval", "has_annotation"])
@pytest.mark.parametrize(
    ("operator", "expected"), [("is_null", "0 = 1"), ("is_not_null", "1 = 1")]
)
def test_presence_filter_presence_operators_compile_a_total_flag(
    name, operator, expected
):
    # has_eval / has_annotation are derived per row and never NULL, so
    # is_not_null constrains nothing and is_null matches nothing.
    config = _validated_query([_presence_filter(name, True, operator)])

    sql, _params, _metric = DashboardQueryBuilderV2(config).build_all_queries()[0]

    assert expected in sql
    assert "dashboard_presence_traces" not in sql


@pytest.mark.parametrize("name", ["has_eval", "has_annotation"])
def test_presence_filter_rejects_an_uncompilable_operation(name):
    config = _validated_query([_presence_filter(name, True)])
    config["filters"][0]["operator"] = "str_contains"

    with pytest.raises(InvalidMetricCombinationError, match="supports only equals"):
        DashboardQueryBuilder(config).build_all_queries()


# --- one rule, four compilers -------------------------------------------
# The dashboard builder used to carry its own copy of the boolean value rule
# (which operators carry a value, how a value coerces, that not_equals
# negates). The copies were edited in parallel, which is how the dashboard
# and the list routes drifted apart on these operators in the first place.
# These tests fail if either side stops going through
# resolve_boolean_meta_value, which is the only thing that keeps them equal.


def _presence_payload(name: str, value, operator: str) -> dict:
    return {
        "metric_type": "system_metric",
        "metric_name": name,
        "operator": operator,
        "value": value,
    }


@pytest.mark.unit
@pytest.mark.parametrize("name", ["has_eval", "has_annotation"])
@pytest.mark.parametrize("operator", ["equals", "not_equals"])
@pytest.mark.parametrize("value", [True, False, "true", "false", "TRUE", " False "])
def test_the_dashboard_and_the_list_compilers_share_one_boolean_value_rule(
    name, operator, value
):
    assert DashboardQueryBuilder._presence_filter_value(
        _presence_payload(name, value, operator), name
    ) == parse_boolean_meta_filter(name, value, operator)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("operator", "value", "fragment"),
    [
        ("str_contains", True, "supports only equals"),
        ("equals", "maybe", "requires a boolean value"),
        ("equals", 1, "requires a boolean value"),
        ("equals", None, "requires a boolean value"),
    ],
)
def test_a_rejected_boolean_shape_carries_one_message_and_two_error_classes(
    operator, value, fragment
):
    # Same rule, same message; only the class differs, because the dashboard
    # surfaces it per widget and the list readers map theirs to HTTP 400.
    payload = _presence_payload("has_eval", value, operator)

    with pytest.raises(InvalidMetricCombinationError) as dashboard_error:
        DashboardQueryBuilder._presence_filter_value(payload, "has_eval")
    with pytest.raises(UnsupportedFilterShapeError) as list_error:
        parse_boolean_meta_filter("has_eval", value, operator)

    assert fragment in str(dashboard_error.value)
    assert str(dashboard_error.value) == str(list_error.value)


@pytest.mark.unit
@pytest.mark.parametrize(("operator", "resolved"), [("is", True), ("is_not", False)])
def test_sharing_the_rule_does_not_hand_the_dashboard_the_list_aliases(
    operator, resolved
):
    # The two compilers normalise with different alias tables and the shared
    # rule must not normalise again: the list routes accept these legacy
    # aliases, the dashboard payload vocabulary does not, and sharing the
    # value rule must not quietly widen the dashboard's to match.
    assert parse_boolean_meta_filter("has_eval", True, operator) is resolved

    with pytest.raises(InvalidMetricCombinationError, match="supports only equals"):
        DashboardQueryBuilder._presence_filter_value(
            _presence_payload("has_eval", True, operator), "has_eval"
        )
