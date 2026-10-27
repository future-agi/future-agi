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


def test_call_transcript_and_branch_analysis_contracts():
    expected_responses = {
        ("GET", "/simulate/call-executions/{call_execution_id}/transcripts/"): (
            "CallTranscriptResponse"
        ),
        ("GET", "/simulate/test-executions/{test_execution_id}/transcripts/"): (
            "TestExecutionTranscriptsResponse"
        ),
        ("GET", "/simulate/call-executions/{call_execution_id}/branch-analysis/"): (
            "CallBranchAnalysisResponse"
        ),
        ("POST", "/simulate/call-executions/{call_execution_id}/branch-analysis/"): (
            "CallBranchDeviationCreateResponse"
        ),
    }

    for (method, path), definition_name in expected_responses.items():
        assert _response_ref(_operation(path, method)) == definition_name

    assert (
        _body_ref(
            _operation(
                "/simulate/call-executions/{call_execution_id}/branch-analysis/",
                "POST",
            )
        )
        == "EmptyRequest"
    )


def test_call_transcript_contract_debt_stays_burned_down():
    covered_paths = {
        "/simulate/call-executions/{call_execution_id}/transcripts/",
        "/simulate/test-executions/{test_execution_id}/transcripts/",
        "/simulate/call-executions/{call_execution_id}/branch-analysis/",
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
