from __future__ import annotations

import json
from pathlib import Path


def _swagger() -> dict:
    root = Path(__file__).resolve().parents[3]
    return json.loads((root / "api_contracts/openapi/swagger.json").read_text())


def test_unified_property_catalog_openapi_is_cursor_complete() -> None:
    swagger = _swagger()
    metrics = swagger["paths"]["/tracer/dashboard/metrics/"]["get"]
    metric_params = {item["name"] for item in metrics["parameters"]}
    assert metric_params == {
        "workflow",
        "project_ids",
        "agent_definition_id",
        "per_eval_config",
        "exclude_custom_attributes",
        "search",
        "category",
        "role",
        "source",
        "page",
        "page_size",
        "cursor_mode",
        "cursor",
    }
    assert metrics["x-runtime-request-validation"] is True
    role_param = next(item for item in metrics["parameters"] if item["name"] == "role")
    assert set(role_param["enum"]) == {"metric", "dimension"}
    assert {"200", "400", "500", "503"} <= set(metrics["responses"])

    metric_result = swagger["definitions"]["DashboardMetricsCatalogResult"]
    assert {
        "total",
        "total_is_exact",
        "has_more",
        "next_cursor",
        "catalog_epoch",
        "catalog_revision",
        "activation_fingerprint",
        "query_complete",
        "query_exact",
        "query_status",
        "query_provenance",
    } <= set(metric_result["properties"])
    assert metric_result["properties"]["total"]["x-nullable"] is True
    assert metric_result["properties"]["next_cursor"]["x-nullable"] is True
    item = swagger["definitions"]["DashboardMetricCatalogItem"]["properties"]
    assert {"attribute_types", "attribute_types_exact"} <= set(item)


def test_unified_property_value_openapi_has_no_phantom_paginator() -> None:
    swagger = _swagger()
    values = swagger["paths"]["/tracer/dashboard/filter_values/"]["get"]
    value_params = {item["name"]: item for item in values["parameters"]}
    assert "page" not in value_params
    assert "limit" not in value_params
    assert {
        "traces",
        "spans",
        "sessions",
        "users",
        "voice_calls",
        "prompts",
        "datasets",
        "dataset_column",
        "simulation",
        "both",
        "all",
    } == set(value_params["source"]["enum"])
    result = swagger["definitions"]["DashboardFilterValuesResult"]["properties"]
    assert {
        "attribute_types",
        "attribute_types_exact",
        "catalog_epoch",
        "catalog_revision",
        "activation_fingerprint",
        "query_provenance",
    } <= set(result)
    assert result["next_cursor"]["x-nullable"] is True
