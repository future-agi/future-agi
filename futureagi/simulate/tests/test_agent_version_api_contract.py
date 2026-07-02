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
    return (
        operation["responses"][status_code]["schema"]["$ref"]
        .rsplit("/", 1)[-1]
    )


def test_agent_version_empty_mutations_have_request_contracts():
    expected = {
        "/simulate/agent-definitions/{agent_id}/versions/{version_id}/activate/",
        "/simulate/agent-definitions/{agent_id}/versions/{version_id}/restore/",
    }

    for path in expected:
        assert _body_ref(_operation(path, "POST")) == "EmptyRequest"


def test_agent_version_mutations_keep_response_contracts():
    expected = {
        "/simulate/agent-definitions/{agent_id}/versions/{version_id}/activate/": (
            "AgentVersionActivateResponse"
        ),
        "/simulate/agent-definitions/{agent_id}/versions/{version_id}/restore/": (
            "AgentVersionRestoreResponse"
        ),
    }

    for path, definition_name in expected.items():
        assert _response_ref(_operation(path, "POST")) == definition_name


def test_agent_version_contract_debt_stays_burned_down():
    covered_paths = {
        "/simulate/agent-definitions/{agent_id}/versions/{version_id}/activate/",
        "/simulate/agent-definitions/{agent_id}/versions/{version_id}/restore/",
    }
    report = _debt_report()

    body_debt = {
        item["path"] for item in report["mutation_endpoints_without_body_schema"]
    }

    assert body_debt.isdisjoint(covered_paths)
