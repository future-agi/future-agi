"""OpenAPI must document the 200 bodies these P1 operations really answer.

The fi CLI is generated from the OpenAPI contract. Its contract audit
(UPSTREAM-CONTRACT-ISSUES.md, P1 rows) found operations whose documented 200
is not what the view returns:

- views that answer ``GeneralMethods.success_response(x)`` (``{status,
  result}``, HTTP 200) while the spec documents drf-yasg's bare inference, or
  a wrong inner ``result`` (dashboards, eval groups, gateway analytics,
  guardrail rules, session requests, optimization runs, projects);
- views that answer an ``ExtendedPageNumberPagination`` page (``count, next,
  previous, results, total_pages, current_page``) while the spec documents a
  bare array or a single object (agents, test executions, prompt versions);
- ``PromptTemplate.variable_names``, documented as an object although the
  platform stores and returns either a list or a dict.

Each operation declares an explicit 2xx ``responses=`` contract, so the
generated document is the same with or without the generator-level success
envelope (fi-cli 583e5babdc), which only wraps *inferred* responses.

The schema is generated in process with the same generator and mock request as
``scripts/generate-openapi-schema.sh``; no database or network is used.
"""

import json
from functools import lru_cache

from drf_yasg.app_settings import swagger_settings
from drf_yasg.codecs import OpenAPICodecJson
from drf_yasg.generators import OpenAPISchemaGenerator
from drf_yasg.management.commands.generate_swagger import Command
from rest_framework import serializers
from rest_framework.settings import api_settings

# Same mock request URL as scripts/generate-openapi-schema.sh.
_API_URL = "http://localhost:8000"

_PAGE_KEYS = {"count", "next", "previous", "results", "total_pages", "current_page"}


@lru_cache(maxsize=1)
def _contract():
    api_version = api_settings.DEFAULT_VERSION
    request = Command().get_mock_request(_API_URL, "json")
    if api_version:
        request.version = api_version
    generator = OpenAPISchemaGenerator(
        info=swagger_settings.DEFAULT_INFO, version=api_version, url=_API_URL
    )
    schema = generator.get_schema(request=request, public=True)
    return json.loads(OpenAPICodecJson(validators=[]).encode(schema))


def _responses(path, method):
    return _contract()["paths"][path][method]["responses"]


def _resolve(schema):
    ref = schema.get("$ref")
    if ref:
        return _contract()["definitions"][ref.rsplit("/", 1)[-1]]
    return schema


def _ref(name):
    return {"$ref": f"#/definitions/{name}"}


def _envelope(result_schema):
    """``success_response()``'s envelope, as the generator renders it."""

    return {
        "required": ["result"],
        "type": "object",
        "properties": {
            "status": {"title": "Status", "type": "boolean", "default": True},
            "result": result_schema,
        },
    }


def _ok(path, method):
    """The single documented success response; it must be HTTP 200."""

    responses = _responses(path, method)
    success = sorted(code for code in responses if code.startswith("2"))
    assert success == ["200"], (path, method, success)
    return responses["200"]["schema"]


def _envelope_result(path, method):
    schema = _resolve(_ok(path, method))
    properties = schema.get("properties", {})
    assert schema.get("type") == "object", (path, method, schema)
    assert set(properties) == {"status", "result"}, (path, method, set(properties))
    assert properties["status"]["type"] == "boolean", (path, method)
    assert "result" in schema.get("required", []), (path, method, schema)
    return properties["result"]


def _object(schema):
    schema = _resolve(schema)
    assert schema.get("type") == "object", schema
    return schema["properties"]


def _array_items(schema):
    schema = _resolve(schema)
    assert schema.get("type") == "array", schema
    return schema["items"]


def _assert_page(path, method, item_definition):
    schema = _resolve(_ok(path, method))
    assert schema.get("type") == "object", (path, method, schema)
    properties = schema.get("properties", {})
    assert set(properties) == _PAGE_KEYS, (path, set(properties))
    for key in ("count", "total_pages", "current_page"):
        assert properties[key]["type"] == "integer", (path, key)
    for key in ("next", "previous"):
        assert properties[key]["type"] == "string", (path, key)
        assert properties[key].get("x-nullable") is True, (path, key)
    assert _array_items(properties["results"]) == _ref(item_definition), path


# ---------------------------------------------------------------------------
# Dashboards (tracer/views/dashboard.py): every CRUD action answers
# success_response(...) with HTTP 200.
# ---------------------------------------------------------------------------


def _assert_runtime_response_contract(path, method):
    operation = _contract()["paths"][path][method]
    assert operation.get("x-runtime-response-validation") is True, (path, method)
    # These are response-only contracts; input validation stays in the view.
    assert "x-runtime-request-validation" not in operation, (path, method)


def test_dashboard_list_and_retrieve_document_the_envelope():
    for path in ("/tracer/dashboard/", "/tracer/dashboard/{id}/"):
        _assert_runtime_response_contract(path, "get")
    # list: success_response(DashboardSerializer(queryset, many=True).data)
    assert _ok("/tracer/dashboard/", "get") == _envelope(
        {"type": "array", "items": _ref("Dashboard")}
    )
    # retrieve: success_response(DashboardDetailSerializer(instance).data)
    assert _ok("/tracer/dashboard/{id}/", "get") == _envelope(_ref("DashboardDetail"))


def test_dashboard_writes_answer_200_with_the_detail_not_the_request_body():
    # create/update/partial_update validate DashboardCreateUpdateSerializer
    # but answer success_response(DashboardDetailSerializer(dashboard).data).
    _assert_runtime_response_contract("/tracer/dashboard/", "post")
    assert _ok("/tracer/dashboard/", "post") == _envelope(_ref("DashboardDetail"))
    for method in ("put", "patch"):
        _assert_runtime_response_contract("/tracer/dashboard/{id}/", method)
        assert _ok("/tracer/dashboard/{id}/", method) == _envelope(
            _ref("DashboardDetail")
        )


def test_dashboard_widget_writes_document_the_envelope_at_200():
    # create/update/partial_update answer
    # success_response(DashboardWidgetSerializer(widget).data).
    _assert_runtime_response_contract(
        "/tracer/dashboard/{dashboard_pk}/widgets/", "post"
    )
    assert _ok("/tracer/dashboard/{dashboard_pk}/widgets/", "post") == _envelope(
        _ref("DashboardWidget")
    )
    for method in ("put", "patch"):
        _assert_runtime_response_contract(
            "/tracer/dashboard/{dashboard_pk}/widgets/{id}/", method
        )
        assert _ok(
            "/tracer/dashboard/{dashboard_pk}/widgets/{id}/", method
        ) == _envelope(_ref("DashboardWidget"))


def test_dashboard_computed_fields_have_their_runtime_types():
    dashboard = _contract()["definitions"]["Dashboard"]["properties"]
    detail = _contract()["definitions"]["DashboardDetail"]["properties"]
    # get_widget_count() returns an int; get_widgets() returns widget rows.
    assert dashboard["widget_count"]["type"] == "integer"
    assert _array_items(detail["widgets"]) == _ref("DashboardWidget")


# ---------------------------------------------------------------------------
# Eval groups (model_hub/views/eval_group.py)
# ---------------------------------------------------------------------------


def test_eval_group_list_documents_the_data_page_in_the_envelope():
    # success_response({"data": rows, "total_count": n, "total_pages": p})
    result = _object(_envelope_result("/model-hub/eval-groups/", "get"))
    assert set(result) == {"data", "total_count", "total_pages"}
    assert result["total_count"]["type"] == "integer"
    assert result["total_pages"]["type"] == "integer"

    row = _object(_array_items(result["data"]))
    assert set(row) == {
        "id",
        "name",
        "description",
        "created_at",
        "required_keys",
        "evals_count",
        "is_sample",
    }
    assert row["evals_count"]["type"] == "integer"
    assert row["is_sample"]["type"] == "boolean"
    assert row["required_keys"]["type"] == "array"


def test_eval_group_retrieve_documents_the_group_with_its_members():
    # success_response({"eval_group": ..., "members": [...], "required_keys":
    # [...], "models": [...], "function_params_requirements": {...}})
    result = _object(_envelope_result("/model-hub/eval-groups/{id}/", "get"))
    assert set(result) == {
        "eval_group",
        "members",
        "required_keys",
        "models",
        "function_params_requirements",
    }
    assert result["eval_group"] == _ref("EvalGroup")
    member = _object(_array_items(result["members"]))
    assert {"eval_template_id", "name", "added_on", "added_by"} <= set(member)
    assert _array_items(result["models"])["type"] == "string"
    assert result["required_keys"]["type"] == "array"
    assert result["function_params_requirements"]["type"] == "object"


# ---------------------------------------------------------------------------
# Gateway (agentcc/views/analytics.py, agentcc/views/session.py)
# ---------------------------------------------------------------------------


def test_gateway_overview_documents_the_kpi_object():
    # success_response(get_overview_kpis(...)): an object of KPI summaries.
    result = _object(_envelope_result("/agentcc/analytics/overview/", "get"))
    kpis = {
        "total_requests",
        "total_tokens",
        "total_cost",
        "avg_latency_ms",
        "error_rate",
        "cache_hit_rate",
        "p95_latency_ms",
        "active_models",
    }
    assert set(result) == kpis | {"period_start", "period_end"}
    for name in kpis:
        kpi = _object(result[name])
        assert kpi["trend"]["type"] == "number", name
        # _compute_trend() returns None when the previous period was zero.
        assert kpi["trend"].get("x-nullable") is True, name
    assert _object(result["total_requests"])["value"]["type"] == "integer"
    assert _object(result["avg_latency_ms"])["value"]["type"] == "number"
    cost = _object(result["total_cost"])
    assert cost["value"]["type"] == "string"
    assert cost["unpriced_requests"]["type"] == "integer"


def test_gateway_guardrail_rules_document_the_rule_aggregates():
    # success_response(get_guardrail_rules(...)) == {"rules": [...]}
    result = _object(_envelope_result("/agentcc/analytics/guardrail-rules/", "get"))
    assert set(result) == {"rules"}
    rule = _object(_array_items(result["rules"]))
    assert set(rule) == {
        "rule",
        "trigger_count",
        "block_count",
        "warn_count",
        "avg_latency_ms",
    }
    assert rule["avg_latency_ms"]["type"] == "number"


def test_gateway_session_requests_document_request_log_rows():
    # success_response(AgentccRequestLogSerializer(logs, many=True).data)
    result = _envelope_result("/agentcc/sessions/{id}/requests/", "get")
    assert _array_items(result) == _ref("AgentccRequestLog")


# ---------------------------------------------------------------------------
# Optimization runs and projects
# ---------------------------------------------------------------------------


def test_optimization_run_list_documents_the_table_envelope():
    # success_response({"metadata": {"total_rows"}, "table": rows,
    # "column_config": OPTIMIZATION_RUN_TABLE_CONFIG})
    result = _object(_envelope_result("/model-hub/dataset-optimization/", "get"))
    assert set(result) == {"metadata", "table", "column_config"}
    assert _array_items(result["table"]) == _ref("DatasetOptimizationList")
    assert _object(result["metadata"])["total_rows"]["type"] == "integer"
    column = _object(_array_items(result["column_config"]))
    assert set(column) == {"id", "name", "is_visible"}
    row = _contract()["definitions"]["DatasetOptimizationList"]["properties"]
    # get_trial_count() returns the annotated Count("trials").
    assert row["trial_count"]["type"] == "integer"


def test_project_list_documents_projects_and_total_count():
    # success_response({"projects": rows, "total_count": n}); each row is
    # ProjectSerializer data plus the annotated trace_count and run_count.
    result = _object(_envelope_result("/tracer/project/", "get"))
    assert set(result) == {"projects", "total_count"}
    assert result["total_count"]["type"] == "integer"
    row = _object(_array_items(result["projects"]))
    project = _contract()["definitions"]["Project"]["properties"]
    assert set(row) == set(project) | {"trace_count", "run_count"}
    assert row["trace_count"]["type"] == "integer"
    assert row["run_count"]["type"] == "integer"


# ---------------------------------------------------------------------------
# Paginated lists that answer a bare ExtendedPageNumberPagination page
# ---------------------------------------------------------------------------


def test_agent_list_documents_the_page():
    # simulate/views/agent_definition.py: paginator.get_paginated_response(...)
    _assert_page("/simulate/agent-definitions/", "get", "AgentDefinitionListResponse")


def test_test_execution_list_documents_the_page():
    # simulate/views/run_test.py: paginator.get_paginated_response(...)
    _assert_page("/simulate/api/test-executions/", "get", "TestExecution")


def test_prompt_version_list_documents_the_history_page():
    # model_hub/views/prompt_template.py versions():
    # self.paginator.get_paginated_response(PromptHistoryExecutionSerializer(...))
    _assert_page(
        "/model-hub/prompt-templates/{id}/versions/",
        "get",
        "PromptHistoryExecution",
    )


def test_prompt_history_labels_and_output_have_their_runtime_shapes():
    history = _contract()["definitions"]["PromptHistoryExecution"]["properties"]
    # get_labels() returns a list of {id, name, type}.
    label = _object(_array_items(history["labels"]))
    assert set(label) == {"id", "name", "type"}
    # PromptVersion.output is a JSONField that defaults to a list.
    assert history["output"].get("x-json-value") is True


# ---------------------------------------------------------------------------
# PromptTemplate.variable_names: a list for new templates, a dict once a run
# has stored variable values. It is any JSON value, not an object.
# ---------------------------------------------------------------------------


def test_prompt_template_variable_names_is_documented_as_any_json_value():
    definitions = _contract()["definitions"]
    for name in ("PromptTemplate", "PromptTemplatePatch"):
        field = definitions[name]["properties"]["variable_names"]
        assert field.get("x-json-value") is True, name
        assert field.get("x-nullable") is True, name


def test_prompt_template_variable_names_keeps_its_runtime_behaviour():
    from model_hub.serializers.prompt_requests import PromptTemplatePatchSerializer
    from model_hub.serializers.prompt_template import PromptTemplateSerializer

    for serializer_class in (PromptTemplateSerializer, PromptTemplatePatchSerializer):
        field = serializer_class().fields["variable_names"]
        assert isinstance(field, serializers.JSONField)
        assert field.required is False
        assert field.allow_null is True
        assert field.read_only is False
        for value in ([], ["customer"], {"customer": ["Ada", "Grace"]}, None):
            assert field.run_validation(value) == value
            if value is not None:
                assert field.to_representation(value) == value
