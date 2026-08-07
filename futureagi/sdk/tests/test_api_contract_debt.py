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


def test_sdk_contract_debt_is_fully_burned_down():
    report = _debt_report()
    sdk_report = report["by_group"]["sdk"]

    assert [
        item
        for item in report["mutation_endpoints_without_body_schema"]
        if item["tags"] == ["sdk"]
    ] == []
    assert [
        item
        for item in report["operations_without_response_schema"]
        if item["tags"] == ["sdk"]
    ] == []
    assert sdk_report["operations_without_error_response_schema"] == 0
    assert sdk_report["broad_error_response_schemas"] == 0


def test_sdk_mutations_have_body_contracts():
    expected = {
        ("POST", "/sdk/api/v1/configure-evaluations/"): (
            "SDKConfigureEvaluationsRequest"
        ),
        ("POST", "/sdk/api/v1/eval/"): "SDKStandaloneEvalRequest",
        ("POST", "/sdk/api/v1/evaluate-pipeline/"): "CICDJob",
        ("POST", "/sdk/api/v1/new-eval/"): "SDKStandaloneEvalV2Request",
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_sdk_endpoints_have_response_contracts():
    expected = {
        ("POST", "/sdk/api/v1/configure-evaluations/"): (
            "SDKConfigureEvaluationsResponse"
        ),
        ("POST", "/sdk/api/v1/eval/"): "SDKStandaloneEvalResponse",
        ("GET", "/sdk/api/v1/eval/{eval_id}/"): "SDKEvalTemplateResponse",
        ("GET", "/sdk/api/v1/evaluate-pipeline/"): ("SDKCICDEvaluationRunsResponse"),
        ("POST", "/sdk/api/v1/evaluate-pipeline/"): (
            "SDKCICDEvaluationRunAcceptedResponse"
        ),
        ("GET", "/sdk/api/v1/get-evals/"): "SDKGetEvalsResponse",
        ("GET", "/sdk/api/v1/new-eval/"): "SDKStandaloneEvalV2Response",
        ("POST", "/sdk/api/v1/new-eval/"): "SDKStandaloneEvalResponse",
        ("GET", "/sdk/api/v1/simulation/analytics/"): (
            "SDKSimulationAnalyticsResponse"
        ),
        ("GET", "/sdk/api/v1/simulation/metrics/"): ("SDKSimulationMetricsResponse"),
        ("GET", "/sdk/api/v1/simulation/runs/"): "SDKSimulationRunsResponse",
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name


def test_sdk_endpoints_have_typed_error_contracts():
    expected = {
        ("POST", "/sdk/api/v1/configure-evaluations/", "400"): "SDKErrorResponse",
        ("GET", "/sdk/api/v1/eval/{eval_id}/", "500"): "SDKErrorResponse",
        ("GET", "/sdk/api/v1/simulation/metrics/", "404"): "SDKErrorResponse",
    }

    for (method, path, status_code), definition_name in expected.items():
        assert _response_ref(_operation(path, method), status_code) == definition_name
