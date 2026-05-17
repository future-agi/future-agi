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


def test_test_execution_mutations_have_body_contracts():
    expected = {
        ("POST", "/simulate/test-executions/{test_execution_id}/cancel/"): (
            "EmptyRequest"
        ),
        ("PUT", "/simulate/test-executions/{test_execution_id}/column-order/"): (
            "TestExecutionColumnOrder"
        ),
        (
            "POST",
            "/simulate/test-executions/{test_execution_id}/eval-explanation-summary/refresh/",
        ): "EmptyRequest",
        (
            "POST",
            "/simulate/test-executions/{test_execution_id}/optimiser-analysis/refresh/",
        ): "EmptyRequest",
        ("POST", "/simulate/run-tests/{run_test_id}/delete-test-executions/"): (
            "TestExecutionBulkDelete"
        ),
        ("POST", "/simulate/run-tests/{run_test_id}/rerun-test-executions/"): (
            "TestExecutionRerun"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_test_execution_endpoints_have_response_contracts():
    expected = {
        ("POST", "/simulate/test-executions/{test_execution_id}/cancel/"): (
            "CancelTestExecutionResponse"
        ),
        ("PUT", "/simulate/test-executions/{test_execution_id}/column-order/"): (
            "TestExecutionColumnOrderResponse"
        ),
        (
            "POST",
            "/simulate/test-executions/{test_execution_id}/eval-explanation-summary/refresh/",
        ): "ApiSuccessResponse",
        ("GET", "/simulate/test-executions/{test_execution_id}/optimiser-analysis/"): (
            "ApiSuccessResponse"
        ),
        (
            "POST",
            "/simulate/test-executions/{test_execution_id}/optimiser-analysis/refresh/",
        ): "ApiSuccessResponse",
        ("POST", "/simulate/run-tests/{run_test_id}/delete-test-executions/"): (
            "TestExecutionBulkDeleteResponse"
        ),
        ("POST", "/simulate/run-tests/{run_test_id}/rerun-test-executions/"): (
            "TestExecutionRerunResponse"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name


def test_test_execution_contract_debt_stays_burned_down():
    covered_paths = {
        "/simulate/test-executions/{test_execution_id}/cancel/",
        "/simulate/test-executions/{test_execution_id}/column-order/",
        "/simulate/test-executions/{test_execution_id}/eval-explanation-summary/refresh/",
        "/simulate/test-executions/{test_execution_id}/optimiser-analysis/",
        "/simulate/test-executions/{test_execution_id}/optimiser-analysis/refresh/",
        "/simulate/run-tests/{run_test_id}/delete-test-executions/",
        "/simulate/run-tests/{run_test_id}/rerun-test-executions/",
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
