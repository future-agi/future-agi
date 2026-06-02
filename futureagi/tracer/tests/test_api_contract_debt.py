import json
from pathlib import Path


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _debt_report():
    with (
        _repo_root()
        / "api_contracts"
        / "openapi"
        / "management-api-contract-debt.generated.json"
    ).open() as f:
        return json.load(f)


def test_tracer_contract_debt_is_fully_burned_down():
    report = _debt_report()
    tracer_report = report["by_group"]["tracer"]

    assert tracer_report["mutation_endpoints_without_body_schema"] == 0
    assert tracer_report["operations_without_response_schema"] == 0
    assert tracer_report["operations_without_error_response_schema"] == 0
    assert tracer_report["broad_error_response_schemas"] == 0
