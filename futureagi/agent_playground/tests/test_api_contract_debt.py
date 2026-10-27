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


def test_agent_playground_contract_debt_is_fully_burned_down():
    report = _debt_report()
    agent_playground_report = report["by_group"]["agent-playground"]

    assert [
        item
        for item in report["mutation_endpoints_without_body_schema"]
        if item["tags"] == ["agent-playground"]
    ] == []
    assert [
        item
        for item in report["operations_without_response_schema"]
        if item["tags"] == ["agent-playground"]
    ] == []
    assert agent_playground_report["operations_without_error_response_schema"] == 0
    assert agent_playground_report["broad_error_response_schemas"] == 0


def test_agent_playground_execution_endpoints_have_contracts():
    assert (
        _body_ref(_operation("/agent-playground/graphs/from-trace/", "POST"))
        == "TraceToGraphRequest"
    )
    assert (
        _response_ref(_operation("/agent-playground/graphs/from-trace/", "POST"), "201")
        == "TraceToGraphResponse"
    )
    assert (
        _response_ref(
            _operation("/agent-playground/graphs/{graph_id}/executions/", "GET")
        )
        == "GraphExecutionListResponse"
    )
    assert (
        _response_ref(
            _operation(
                "/agent-playground/graphs/{graph_id}/executions/{execution_id}/",
                "GET",
            )
        )
        == "GraphExecutionDetailResponse"
    )
    assert (
        _response_ref(
            _operation(
                "/agent-playground/executions/{execution_id}/nodes/{node_execution_id}/",
                "GET",
            )
        )
        == "NodeExecutionDetailResponse"
    )


def test_agent_playground_error_responses_use_typed_contracts():
    assert (
        _response_ref(_operation("/agent-playground/graphs/", "GET"), "400")
        == "AgentPlaygroundErrorResponse"
    )
    assert (
        _response_ref(_operation("/agent-playground/graphs/from-trace/", "POST"), "404")
        == "AgentPlaygroundErrorResponse"
    )
    assert (
        _response_ref(
            _operation(
                "/agent-playground/graphs/{graph_id}/executions/{execution_id}/",
                "GET",
            ),
            "500",
        )
        == "AgentPlaygroundErrorResponse"
    )
