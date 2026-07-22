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
    schema = operation["responses"][status_code]["schema"]
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return schema["type"]


def test_small_core_api_tags_stay_debt_free():
    report = _debt_report()
    tags = {"ai-tools", "api"}

    assert [
        item
        for item in report["mutation_endpoints_without_body_schema"]
        if item["tags"][0] in tags
    ] == []
    assert [
        item
        for item in report["operations_without_response_schema"]
        if item["tags"][0] in tags
    ] == []


def test_small_core_api_error_contract_debt_is_burned_down():
    report = _debt_report()
    groups = {"ai-tools", "api", "call-websocket", "health", "v1"}

    for group in groups:
        group_report = report["by_group"][group]
        assert group_report["operations_without_error_response_schema"] == 0
        assert group_report["broad_error_response_schemas"] == 0


def test_small_core_api_endpoints_have_contracts():
    assert (
        _response_ref(_operation("/ai-tools/tools/", "GET")) == "ToolDiscoveryResponse"
    )
    assert (
        _body_ref(_operation("/api/public/ingestion", "POST"))
        == "LangfuseIngestionRequest"
    )
    assert (
        _response_ref(_operation("/api/public/ingestion", "POST"), "207")
        == "LangfuseIngestionResponse"
    )
    assert (
        _response_ref(_operation("/api/public/ingestion", "POST"), "403")
        == "ApiDetailErrorResponse"
    )
    assert (
        _response_ref(_operation("/api/deployment-info/", "GET"), "500")
        == "ApiTextErrorResponse"
    )
    assert (
        _response_ref(_operation("/api/public/health", "GET"), "401")
        == "ApiDetailErrorResponse"
    )
    assert (
        _response_ref(_operation("/api/public/traces", "GET"), "403")
        == "ApiDetailErrorResponse"
    )
    assert (
        _response_ref(_operation("/api/traces/span-attribute-keys/", "GET"), "503")
        == "ApiTextErrorResponse"
    )
    assert (
        _response_ref(_operation("/api/health/clickhouse/", "GET"))
        == "ClickHouseHealthResponse"
    )
    assert _response_ref(_operation("/health/", "GET"), "500") == "ApiTextErrorResponse"
    assert (
        _response_ref(_operation("/ai-tools/tools/", "GET"), "403")
        == "ApiDetailErrorResponse"
    )
    assert (
        _response_ref(_operation("/v1/health", "GET"), "500") == "ApiTextErrorResponse"
    )
    assert (
        _response_ref(_operation("/call-websocket/", "POST"), "400")
        == "CallWebsocketErrorResponse"
    )
