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


def test_persona_duplicate_has_body_and_response_contracts():
    operation = _operation("/simulate/api/personas/duplicate/{persona_id}/", "POST")

    assert _body_ref(operation) == "PersonaDuplicateRequest"
    assert _response_ref(operation, "201") == "PersonaDuplicateResponse"
    assert _response_ref(operation, "400") == "ApiTextErrorResponse"


def test_persona_duplicate_contract_debt_stays_burned_down():
    path = "/simulate/api/personas/duplicate/{persona_id}/"
    report = _debt_report()

    body_debt = {
        item["path"] for item in report["mutation_endpoints_without_body_schema"]
    }
    response_debt = {
        item["path"] for item in report["operations_without_response_schema"]
    }

    assert path not in body_debt
    assert path not in response_debt
