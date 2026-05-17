import json
from pathlib import Path


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (
        _repo_root() / "api_contracts" / "openapi" / "swagger.json"
    ).open() as f:
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


def test_small_core_api_endpoints_have_contracts():
    assert (
        _response_ref(_operation("/ai-tools/tools/", "GET"))
        == "ToolDiscoveryResponse"
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
        _response_ref(_operation("/api/health/clickhouse/", "GET"))
        == "ClickHouseHealthResponse"
    )
