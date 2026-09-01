"""Focused contracts for dashboard eval/annotation presence filters."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tracer.serializers.dashboard import DashboardQuerySerializer
from tracer.services.clickhouse.query_builders.dashboard import (
    DashboardQueryBuilder,
    InvalidMetricCombinationError,
)
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.views.dashboard import _normalize_dashboard_query_filters


def _presence_filter(name: str, value: bool) -> dict:
    return {
        "column_id": name,
        "property_id": f"system_attribute:traces:{name}",
        "source": "traces",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "boolean",
            "filter_op": "equals",
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
def test_presence_filter_rejects_non_boolean_or_non_equals_without_fallback(name):
    config = _validated_query([_presence_filter(name, True)])
    config["filters"][0]["operator"] = "not_equal_to"

    with pytest.raises(InvalidMetricCombinationError, match="only the equals"):
        DashboardQueryBuilder(config).build_all_queries()
