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


def _form_param_names(operation):
    return {
        parameter["name"]
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "formData"
    }


def _response_ref(operation, status_code="200"):
    schema = operation["responses"][status_code]["schema"]
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return schema["type"]


def test_falcon_contract_debt_is_fully_burned_down():
    report = _debt_report()
    falcon_report = report["by_group"]["falcon-ai"]

    assert [
        item
        for item in report["mutation_endpoints_without_body_schema"]
        if item["tags"] == ["falcon-ai"]
    ] == []
    assert [
        item
        for item in report["operations_without_response_schema"]
        if item["tags"] == ["falcon-ai"]
    ] == []
    assert falcon_report["operations_without_error_response_schema"] == 0
    assert falcon_report["broad_error_response_schemas"] == 0


def test_falcon_mutations_have_request_contracts():
    expected_body_refs = {
        ("POST", "/falcon-ai/conversations/"): "ConversationCreateRequest",
        ("PATCH", "/falcon-ai/conversations/{conversation_id}/"): (
            "ConversationUpdateRequest"
        ),
        ("POST", "/falcon-ai/mcp-connectors/"): "MCPConnectorCreate",
        ("PATCH", "/falcon-ai/mcp-connectors/{connector_id}/"): (
            "MCPConnectorUpdateRequest"
        ),
        ("POST", "/falcon-ai/mcp-connectors/{connector_id}/authenticate/"): (
            "FalconEmptyRequest"
        ),
        ("POST", "/falcon-ai/mcp-connectors/{connector_id}/discover/"): (
            "FalconEmptyRequest"
        ),
        ("POST", "/falcon-ai/mcp-connectors/{connector_id}/test/"): (
            "FalconEmptyRequest"
        ),
        ("PATCH", "/falcon-ai/mcp-connectors/{connector_id}/tools/"): (
            "MCPConnectorTools"
        ),
        ("POST", "/falcon-ai/memory/"): "FalconMemoryCreate",
        ("POST", "/falcon-ai/messages/{message_id}/feedback/"): "MessageFeedback",
        ("POST", "/falcon-ai/quick-analysis/"): "QuickAnalysis",
        ("POST", "/falcon-ai/skills/"): "SkillCreate",
        ("PATCH", "/falcon-ai/skills/{skill_id}/"): "SkillUpdateRequest",
    }

    for (method, path), definition_name in expected_body_refs.items():
        assert _body_ref(_operation(path, method)) == definition_name

    assert _form_param_names(_operation("/falcon-ai/files/upload/", "POST")) == {"file"}


def test_falcon_endpoints_have_response_contracts():
    expected = {
        ("GET", "/falcon-ai/conversations/"): "ConversationListResponse",
        ("POST", "/falcon-ai/conversations/", "201"): "ConversationDetailResponse",
        ("GET", "/falcon-ai/conversations/{conversation_id}/"): (
            "ConversationDetailResponse"
        ),
        ("PATCH", "/falcon-ai/conversations/{conversation_id}/"): (
            "ConversationDetailResponse"
        ),
        ("GET", "/falcon-ai/conversations/{conversation_id}/stream-status/"): (
            "StreamStatusResponse"
        ),
        ("POST", "/falcon-ai/files/upload/", "201"): "FileUploadResponse",
        ("GET", "/falcon-ai/mcp-connectors/"): "MCPConnectorListResponse",
        ("POST", "/falcon-ai/mcp-connectors/", "201"): ("MCPConnectorDetailResponse"),
        ("GET", "/falcon-ai/mcp-connectors/{connector_id}/"): (
            "MCPConnectorDetailResponse"
        ),
        ("PATCH", "/falcon-ai/mcp-connectors/{connector_id}/"): (
            "MCPConnectorDetailResponse"
        ),
        ("POST", "/falcon-ai/mcp-connectors/{connector_id}/authenticate/"): (
            "MCPConnectorAuthenticateResponse"
        ),
        ("POST", "/falcon-ai/mcp-connectors/{connector_id}/discover/"): (
            "MCPConnectorDiscoverResponse"
        ),
        ("GET", "/falcon-ai/mcp-connectors/{connector_id}/oauth/callback/"): ("string"),
        ("POST", "/falcon-ai/mcp-connectors/{connector_id}/test/"): (
            "MCPConnectorTestResponse"
        ),
        ("PATCH", "/falcon-ai/mcp-connectors/{connector_id}/tools/"): (
            "MCPConnectorDetailResponse"
        ),
        ("GET", "/falcon-ai/memory/"): "FalconMemoryListResponse",
        ("POST", "/falcon-ai/memory/"): "FalconMemoryDetailResponse",
        ("POST", "/falcon-ai/messages/{message_id}/feedback/"): (
            "MessageFeedbackResponse"
        ),
        ("POST", "/falcon-ai/quick-analysis/"): "QuickAnalysisResponse",
        ("GET", "/falcon-ai/skills/"): "SkillListResponse",
        ("POST", "/falcon-ai/skills/", "201"): "SkillDetailResponse",
        ("GET", "/falcon-ai/skills/{skill_id}/"): "SkillDetailResponse",
        ("PATCH", "/falcon-ai/skills/{skill_id}/"): "SkillDetailResponse",
    }

    for endpoint, definition_name in expected.items():
        method, path, *status = endpoint
        assert _response_ref(
            _operation(path, method), status[0] if status else "200"
        ) == (definition_name)


def test_falcon_endpoints_have_typed_error_contracts():
    expected = {
        ("DELETE", "/falcon-ai/conversations/{conversation_id}/", "403"): (
            "FalconErrorResponse"
        ),
        ("GET", "/falcon-ai/mcp-connectors/{connector_id}/oauth/callback/", "400"): (
            "string"
        ),
        ("DELETE", "/falcon-ai/skills/{skill_id}/", "404"): "FalconErrorResponse",
    }

    for endpoint, definition_name in expected.items():
        method, path, status_code = endpoint
        assert _response_ref(_operation(path, method), status_code) == definition_name
