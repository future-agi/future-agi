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
    schema = operation["responses"][status_code]["schema"]
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return schema["type"]


def test_simulate_contract_debt_is_fully_burned_down():
    report = _debt_report()
    group_report = report["by_group"]["simulate"]

    assert [
        item
        for item in report["mutation_endpoints_without_body_schema"]
        if item["tags"] == ["simulate"]
    ] == []
    assert [
        item
        for item in report["operations_without_response_schema"]
        if item["tags"] == ["simulate"]
    ] == []
    assert group_report["operations_without_error_response_schema"] == 0
    assert group_report["broad_error_response_schemas"] == 0


def test_remaining_simulate_mutations_have_body_contracts():
    expected = {
        ("POST", "/simulate/prompt-templates/{prompt_template_id}/simulations/"): (
            "CreatePromptSimulationRequest"
        ),
        (
            "PATCH",
            "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/",
        ): "PromptSimulationUpdateRequest",
        (
            "POST",
            "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/execute/",
        ): "ExecutePromptSimulationRequest",
        ("PATCH", "/simulate/run-tests/{run_test_id}/components/"): (
            "RunTestComponentsUpdate"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_remaining_simulate_endpoints_have_response_contracts():
    expected = {
        ("GET", "/simulate/prompt-simulations/scenarios/"): (
            "PromptSimulationScenariosResponse"
        ),
        ("GET", "/simulate/prompt-templates/{prompt_template_id}/simulations/"): (
            "PromptSimulationListResponse"
        ),
        ("POST", "/simulate/prompt-templates/{prompt_template_id}/simulations/"): (
            "PromptSimulationRunResponse",
            "201",
        ),
        (
            "GET",
            "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/",
        ): "PromptSimulationRunResponse",
        (
            "PATCH",
            "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/",
        ): "PromptSimulationRunResponse",
        (
            "POST",
            "/simulate/prompt-templates/{prompt_template_id}/simulations/{run_test_id}/execute/",
        ): "ExecutePromptSimulationResponse",
        ("PATCH", "/simulate/run-tests/{run_test_id}/components/"): ("RunTestResponse"),
        ("GET", "/simulate/run-tests/{run_test_id}/call-executions/"): (
            "RunTestCallExecutionsResponse"
        ),
        (
            "GET",
            "/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/get-structure/",
        ): "EvalConfigStructureResponse",
        (
            "GET",
            "/simulate/call-executions/{call_execution_id}/error-localizer-tasks/",
        ): "CallExecutionErrorLocalizerTasksResponse",
        ("GET", "/simulate/call-executions/{call_execution_id}/session-comparison/"): (
            "SessionComparisonResponse"
        ),
        ("GET", "/simulate/export/{item_id}/"): "file",
        (
            "GET",
            "/simulate/agent-definitions/{agent_id}/versions/{version_id}/eval-summary/",
        ): "EvalSummaryResponse",
    }

    for (method, path), expected_value in expected.items():
        definition_name, status_code = (
            expected_value
            if isinstance(expected_value, tuple)
            else (expected_value, "200")
        )
        assert _response_ref(_operation(path, method), status_code) == definition_name
