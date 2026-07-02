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


def _runtime_debt_report():
    with (
        _repo_root()
        / "api_contracts"
        / "openapi"
        / "runtime-management-api-contract-debt.generated.json"
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
        ("POST", "/simulate/run-tests/{run_test_id}/execute/"): ("ExecuteRunTest"),
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
        ): "EvalExplanationSummaryRefreshResponse",
        ("GET", "/simulate/test-executions/{test_execution_id}/optimiser-analysis/"): (
            "OptimiserAnalysisResponse"
        ),
        (
            "POST",
            "/simulate/test-executions/{test_execution_id}/optimiser-analysis/refresh/",
        ): "OptimiserAnalysisRefreshResponse",
        ("POST", "/simulate/run-tests/{run_test_id}/delete-test-executions/"): (
            "TestExecutionBulkDeleteResponse"
        ),
        ("POST", "/simulate/run-tests/{run_test_id}/rerun-test-executions/"): (
            "TestExecutionRerunResponse"
        ),
        ("POST", "/simulate/run-tests/{run_test_id}/execute/"): (
            "RunTestExecutionResponse"
        ),
        ("GET", "/simulate/run-tests/active/"): "AllActiveTests",
        ("GET", "/simulate/run-tests/{run_test_id}/status/"): ("TestExecutionStatus"),
        ("GET", "/simulate/test-executions/{test_execution_id}/analytics/"): (
            "TestExecutionAnalytics"
        ),
        ("GET", "/simulate/run-tests/{run_test_id}/analytics/"): ("RunTestAnalytics"),
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name


def test_test_execution_detail_documents_query_contract():
    operation = _operation("/simulate/test-executions/{test_execution_id}/", "GET")
    params = {param["name"]: param for param in operation["parameters"]}

    assert params["filters"]["type"] == "string"
    assert params["row_groups"]["type"] == "string"
    assert params["group_keys"]["type"] == "string"
    assert params["page"]["minimum"] == 1
    assert params["limit"]["minimum"] == 1


def test_test_execution_contract_debt_stays_burned_down():
    covered_paths = {
        "/simulate/test-executions/{test_execution_id}/cancel/",
        "/simulate/test-executions/{test_execution_id}/column-order/",
        "/simulate/test-executions/{test_execution_id}/eval-explanation-summary/refresh/",
        "/simulate/test-executions/{test_execution_id}/optimiser-analysis/",
        "/simulate/test-executions/{test_execution_id}/optimiser-analysis/refresh/",
        "/simulate/run-tests/{run_test_id}/delete-test-executions/",
        "/simulate/run-tests/{run_test_id}/rerun-test-executions/",
        "/simulate/run-tests/{run_test_id}/execute/",
        "/simulate/run-tests/active/",
        "/simulate/run-tests/{run_test_id}/status/",
        "/simulate/test-executions/{test_execution_id}/analytics/",
        "/simulate/run-tests/{run_test_id}/analytics/",
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


def test_test_execution_action_contracts_are_runtime_validated():
    migrated_views = {
        ("futureagi/simulate/views/run_test.py", "TestExecutionCancelView", "post"),
        (
            "futureagi/simulate/views/run_test.py",
            "TestExecutionColumnOrderView",
            "put",
        ),
        (
            "futureagi/simulate/views/run_test.py",
            "TestExecutionBulkDeleteView",
            "post",
        ),
        (
            "futureagi/simulate/views/run_test.py",
            "RunTestEvalExplanationSummaryRefreshView",
            "post",
        ),
        (
            "futureagi/simulate/views/run_test.py",
            "TestExecutionOptimiserAnalysisRefreshView",
            "post",
        ),
        ("futureagi/simulate/views/run_test.py", "CallExecutionRerunView", "post"),
        ("futureagi/simulate/views/run_test.py", "TestExecutionRerunView", "post"),
        (
            "futureagi/simulate/views/run_test.py",
            "RunNewEvalsOnTestExecutionView",
            "post",
        ),
    }
    report = _runtime_debt_report()
    doc_only_views = {
        (item["path"], item.get("class", ""), item["function"])
        for item in report["app_wide_doc_only_input_contract_decorators"]
    }

    assert doc_only_views.isdisjoint(migrated_views)
    assert (
        report["app_wide_summary"]["runtime_backed_validated_request_decorators"] >= 378
    )
    assert report["app_wide_summary"]["doc_only_input_contract_decorators"] <= 97
