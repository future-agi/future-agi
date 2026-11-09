import json
from pathlib import Path


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _debt_report():
    with (
        _repo_root()
        / "api_contracts"
        / "openapi"
        / "management-api-contract-debt.generated.json"
    ).open() as f:
        return json.load(f)


def _operation(path, method):
    return _swagger()["paths"][path][method.lower()]


def _body_ref(operation):
    body = next(
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "body"
    )
    return body["schema"]["$ref"].rsplit("/", 1)[-1]


def _response_ref(operation, status_code="200"):
    return operation["responses"][status_code]["schema"]["$ref"].rsplit("/", 1)[-1]


def _success_payload(schema):
    """The payload schema: ``result`` inside an inline success envelope.

    GeneralMethods.success_response() answers ``{"status": true, "result":
    ...}``, which the generator documents inline around the inferred schema.
    """
    properties = (schema or {}).get("properties") or {}
    if (properties.get("status") or {}).get(
        "type"
    ) == "boolean" and "result" in properties:
        return properties["result"]
    return schema or {}


def _success_response_ref(operation):
    for status_code in ("200", "201"):
        response = operation.get("responses", {}).get(status_code)
        schema = _success_payload(response.get("schema")) if response else {}
        ref = schema.get("$ref")
        if ref:
            return ref.rsplit("/", 1)[-1]
    raise AssertionError(f"No success response schema found: {operation}")


def test_agentcc_contract_debt_is_fully_burned_down():
    report = _debt_report()

    assert (
        report["by_group"]["agentcc"]["operations_without_error_response_schema"] == 0
    )
    assert report["by_group"]["agentcc"]["broad_error_response_schemas"] == 0
    assert [
        item
        for item in report["mutation_endpoints_without_body_schema"]
        if item["tags"] == ["agentcc"]
    ] == []
    assert [
        item
        for item in report["operations_without_response_schema"]
        if item["tags"] == ["agentcc"]
    ] == []


def test_agentcc_gateway_actions_have_request_contracts():
    expected = {
        ("POST", "/agentcc/gateways/{id}/health_check/"): "AgentccEmptyRequest",
        ("POST", "/agentcc/gateways/{id}/reload/"): "AgentccEmptyRequest",
        ("POST", "/agentcc/gateways/{id}/update-config/"): (
            "GatewayConfigPatchRequest"
        ),
        ("POST", "/agentcc/gateways/{id}/update-provider/"): (
            "GatewayProviderUpdateRequest"
        ),
        ("POST", "/agentcc/gateways/{id}/remove-provider/"): "GatewayNameRequest",
        ("POST", "/agentcc/gateways/{id}/toggle-guardrail/"): (
            "GatewayToggleGuardrailRequest"
        ),
        ("POST", "/agentcc/gateways/{id}/update-guardrail/"): (
            "GatewayNamedConfigRequest"
        ),
        ("POST", "/agentcc/gateways/{id}/test-playground/"): (
            "GatewayPlaygroundTestRequest"
        ),
        ("POST", "/agentcc/gateways/{id}/test-playground-stream/"): (
            "GatewayPlaygroundTestRequest"
        ),
        ("POST", "/agentcc/gateways/{id}/set-budget/"): "GatewayBudgetSetRequest",
        ("POST", "/agentcc/gateways/{id}/remove-budget/"): (
            "GatewayBudgetRemoveRequest"
        ),
        ("POST", "/agentcc/gateways/{id}/submit-batch/"): ("GatewayBatchSubmitRequest"),
        ("POST", "/agentcc/gateways/{id}/cancel-batch/"): "GatewayBatchRequest",
        ("POST", "/agentcc/gateways/{id}/update-mcp-server/"): (
            "GatewayMCPServerUpdateRequest"
        ),
        ("POST", "/agentcc/gateways/{id}/remove-mcp-server/"): (
            "GatewayMCPServerRemoveRequest"
        ),
        ("POST", "/agentcc/gateways/{id}/update-mcp-guardrails/"): (
            "GatewayMCPGuardrailsUpdateRequest"
        ),
        ("POST", "/agentcc/gateways/{id}/test-mcp-tool/"): (
            "GatewayMCPToolTestRequest"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_agentcc_gateway_and_webhook_endpoints_have_response_contracts():
    expected = {
        ("GET", "/agentcc/gateways/"): "GatewayListResponse",
        ("GET", "/agentcc/gateways/{id}/"): "GatewayDetailResponse",
        ("POST", "/agentcc/gateways/{id}/health_check/"): "GatewayHealthResponse",
        ("GET", "/agentcc/gateways/{id}/config/"): "GatewayConfigResponse",
        ("POST", "/agentcc/gateways/{id}/reload/"): "GatewayMutationResponse",
        ("POST", "/agentcc/gateways/{id}/update-config/"): (
            "GatewayConfigMutationResponse"
        ),
        ("POST", "/agentcc/org-configs/"): "AgentccOrgConfigMutationResponse",
        ("GET", "/agentcc/gateways/{id}/mcp-tools/"): ("AgentccListResultResponse"),
        ("GET", "/agentcc/gateways/{id}/mcp-status/"): ("GatewayMCPStatusResponse"),
        ("GET", "/agentcc/guardrail-configs/pii-entities/"): ("PIIEntitiesResponse"),
        ("GET", "/agentcc/guardrail-configs/topics/"): "TopicCategoriesResponse",
        ("POST", "/agentcc/guardrail-configs/validate-cel/"): ("ValidateCELResponse"),
        ("GET", "/agentcc/api-keys/bulk/"): "APIKeyBulkResponse",
        ("GET", "/agentcc/org-configs/bulk/"): "OrgConfigBulkResponse",
        ("GET", "/agentcc/spend-summary/"): "SpendSummaryResponse",
        ("POST", "/agentcc/webhook/logs/"): "WebhookIngestResponse",
        ("POST", "/agentcc/webhook/shadow-results/"): "WebhookIngestResponse",
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name


def test_cap_write_warning_values_match_the_response_contract():
    from jsonschema import validate

    swagger = _swagger()
    # Both write endpoints can return the writer's boolean transition warning.
    warning = {"code": "managed_models_change", "from": False, "to": True}
    for definition in ("GatewayConfigMutationResult", "AgentccOrgConfigMutationResult"):
        schema = swagger["definitions"][definition]["properties"]["warnings"]["items"]
        validate(warning, schema)


def test_agentcc_guardrail_policy_generated_routes_have_contracts():
    expected_bodies = {
        ("POST", "/agentcc/guardrail-policies/"): "AgentccGuardrailPolicy",
        ("POST", "/agentcc/guardrail-policies/sync/"): "AgentccGuardrailPolicy",
        ("PUT", "/agentcc/guardrail-policies/{id}/"): "AgentccGuardrailPolicy",
        ("PATCH", "/agentcc/guardrail-policies/{id}/"): "AgentccGuardrailPolicy",
        ("POST", "/agentcc/guardrail-policies/{id}/apply/"): ("AgentccGuardrailPolicy"),
    }
    # A policy reads back with its stored scope (key and project included, from
    # an earlier release), so reads and the write responses use the read
    # definition; request bodies keep the global-only write definition.
    expected_responses = {
        ("POST", "/agentcc/guardrail-policies/"): "AgentccGuardrailPolicyRead",
        ("POST", "/agentcc/guardrail-policies/sync/"): "AgentccGuardrailPolicy",
        ("GET", "/agentcc/guardrail-policies/{id}/"): "AgentccGuardrailPolicyRead",
        ("PUT", "/agentcc/guardrail-policies/{id}/"): "AgentccGuardrailPolicyRead",
        ("PATCH", "/agentcc/guardrail-policies/{id}/"): "AgentccGuardrailPolicyRead",
        ("POST", "/agentcc/guardrail-policies/{id}/apply/"): ("AgentccGuardrailPolicy"),
    }

    for (method, path), definition_name in expected_bodies.items():
        assert _body_ref(_operation(path, method)) == definition_name
    for (method, path), definition_name in expected_responses.items():
        assert _success_response_ref(_operation(path, method)) == definition_name
