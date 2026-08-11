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


def _response_ref(operation, status_code="200"):
    return operation["responses"][status_code]["schema"]["$ref"].rsplit("/", 1)[-1]


def test_integrations_error_contract_debt_is_fully_burned_down():
    report = _debt_report()["by_group"]["integrations"]

    assert report["operations_without_error_response_schema"] == 0
    assert report["broad_error_response_schemas"] == 0


def test_integrations_error_responses_use_typed_contracts():
    assert (
        _response_ref(_operation("/integrations/connections/", "GET"), "400")
        == "IntegrationErrorResponse"
    )
    assert (
        _response_ref(
            _operation("/integrations/connections/{id}/sync_now/", "POST"),
            "409",
        )
        == "IntegrationErrorResponse"
    )
    assert (
        _response_ref(_operation("/integrations/sync-logs/", "GET"), "500")
        == "IntegrationErrorResponse"
    )
