import json
import uuid
from pathlib import Path

from rest_framework.test import APIRequestFactory, force_authenticate

from simulate.views.chat_simulation import ChatSendMessageView


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


def test_chat_simulation_mutations_have_body_contracts():
    expected = {
        ("POST", "/simulate/run-tests/{run_test_id}/chat-execute/"): ("EmptyRequest"),
        (
            "POST",
            "/simulate/test-executions/{test_execution_id}/chat/call-executions/batch/",
        ): "EmptyRequest",
        ("POST", "/simulate/call-executions/{call_execution_id}/chat/send-message/"): (
            "SendChatRequest"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _body_ref(_operation(path, method)) == definition_name


def test_chat_simulation_endpoints_have_response_contracts():
    expected = {
        ("GET", "/simulate/run-tests/get-id-by-name/{run_test_name}/"): (
            "RunTestNameResponse"
        ),
        ("POST", "/simulate/run-tests/{run_test_id}/chat-execute/"): (
            "RunTestChatExecutionResponse"
        ),
        (
            "POST",
            "/simulate/test-executions/{test_execution_id}/chat/call-executions/batch/",
        ): "TestExecutionChatBatchResponse",
        ("POST", "/simulate/call-executions/{call_execution_id}/chat/send-message/"): (
            "ChatSendMessageResponse"
        ),
        ("GET", "/simulate/run-tests/{run_test_id}/sdk-code/"): ("ChatSDKCodeResponse"),
    }

    for (method, path), definition_name in expected.items():
        assert _response_ref(_operation(path, method)) == definition_name


def test_chat_simulation_contract_debt_stays_burned_down():
    covered_paths = {
        "/simulate/run-tests/get-id-by-name/{run_test_name}/",
        "/simulate/run-tests/{run_test_id}/chat-execute/",
        "/simulate/test-executions/{test_execution_id}/chat/call-executions/batch/",
        "/simulate/call-executions/{call_execution_id}/chat/send-message/",
        "/simulate/run-tests/{run_test_id}/sdk-code/",
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


def test_send_chat_message_rejects_unknown_request_fields(user):
    factory = APIRequestFactory()
    call_execution_id = uuid.uuid4()
    request = factory.post(
        f"/simulate/call-executions/{call_execution_id}/chat/send-message/",
        {"initiate_chat": True, "displayName": "Future AGI"},
        format="json",
    )
    force_authenticate(request, user=user)

    response = ChatSendMessageView.as_view()(
        request,
        call_execution_id=call_execution_id,
    )

    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["message"] == "displayName: Unknown field."
    assert response.data["details"] == {"displayName": ["Unknown field."]}
