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


def test_simulator_agent_mutations_have_body_contracts():
    expected = {
        ("POST", "/simulate/simulator-agents/create/"): "SimulatorAgent",
        ("PUT", "/simulate/simulator-agents/{agent_id}/edit/"): "SimulatorAgent",
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_simulator_agent_endpoints_have_response_contracts():
    expected = {
        ("GET", "/simulate/simulator-agents/"): "SimulatorAgentListResponse",
        ("POST", "/simulate/simulator-agents/create/", "201"): "SimulatorAgent",
        ("POST", "/simulate/simulator-agents/create/", "400"): (
            "SimulatorAgentValidationErrorResponse"
        ),
        ("GET", "/simulate/simulator-agents/{agent_id}/"): "SimulatorAgent",
        ("PUT", "/simulate/simulator-agents/{agent_id}/edit/"): "SimulatorAgent",
        ("PUT", "/simulate/simulator-agents/{agent_id}/edit/", "400"): (
            "SimulatorAgentValidationErrorResponse"
        ),
    }

    for operation_key, definition_name in expected.items():
        method, path, *status_code = operation_key
        assert _response_ref(
            _operation(path, method), status_code[0] if status_code else "200"
        ) == definition_name


def test_simulator_agent_contract_debt_stays_burned_down():
    covered_paths = {
        "/simulate/simulator-agents/",
        "/simulate/simulator-agents/create/",
        "/simulate/simulator-agents/{agent_id}/",
        "/simulate/simulator-agents/{agent_id}/edit/",
    }
    report = _debt_report()

    body_debt = {
        item["path"] for item in report["mutation_endpoints_without_body_schema"]
    }
    response_debt = {
        item["path"] for item in report["operations_without_response_schema"]
    }

    assert body_debt.isdisjoint(covered_paths)
    assert response_debt.isdisjoint(covered_paths)
