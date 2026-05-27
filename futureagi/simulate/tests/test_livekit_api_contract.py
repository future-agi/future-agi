import json
import uuid
from pathlib import Path

import pytest
from asgiref.sync import async_to_sync
from django.test import override_settings
from rest_framework.test import APIRequestFactory

from simulate.views.livekit_api import TranscriptsView


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


def _body_schema(operation):
    body = next(
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "body"
    )
    return body["schema"]


def _response_schema(operation, status_code="200"):
    return operation["responses"][status_code]["schema"]


def _schema_ref_name(schema):
    return schema["$ref"].rsplit("/", 1)[-1]


def test_livekit_mutations_have_explicit_body_contracts():
    expected = {
        ("POST", "/simulate/api/livekit/transcripts/{call_id}/"): (
            "LiveKitTranscriptsRequest"
        ),
        ("PATCH", "/simulate/api/livekit/call-execution/{call_id}/"): (
            "LiveKitCallExecutionUpdateRequest"
        ),
        ("POST", "/simulate/api/livekit/temporal-signal/"): (
            "LiveKitTemporalSignalRequest"
        ),
        ("POST", "/simulate/api/livekit/validate-credentials/"): (
            "ValidateLiveKitCredentialsRequest"
        ),
    }

    for (method, path), definition_name in expected.items():
        assert _schema_ref_name(_body_schema(_operation(path, method))) == (
            definition_name
        )

    webhook_body = _body_schema(_operation("/simulate/api/livekit/webhook/", "POST"))
    assert webhook_body["type"] == "object"


def test_livekit_endpoints_have_explicit_response_contracts():
    expected_refs = {
        ("PATCH", "/simulate/api/livekit/call-execution/{call_id}/"): (
            "LiveKitOkResponse"
        ),
        ("POST", "/simulate/api/livekit/temporal-signal/"): ("LiveKitOkResponse"),
        ("GET", "/simulate/api/livekit/listener-token/{call_id}/"): (
            "LiveKitListenerTokenResponse"
        ),
        ("POST", "/simulate/api/livekit/validate-credentials/"): (
            "ValidateLiveKitCredentialsResponse"
        ),
        ("POST", "/simulate/api/livekit/webhook/"): "LiveKitOkResponse",
        ("GET", "/simulate/api/livekit/call-config/{call_id}/"): (
            "LiveKitCallConfigResponse"
        ),
        ("POST", "/simulate/api/livekit/transcripts/{call_id}/", "201"): (
            "LiveKitTranscriptCreatedResponse"
        ),
        ("GET", "/simulate/api/livekit/phone-resolution/{phone_number}/"): (
            "LiveKitPhoneResolutionResponse"
        ),
    }

    for key, definition_name in expected_refs.items():
        method, path, *status_code = key
        assert (
            _schema_ref_name(
                _response_schema(
                    _operation(path, method),
                    status_code[0] if status_code else "200",
                )
            )
            == definition_name
        )


def test_livekit_contract_debt_stays_burned_down():
    covered_paths = {
        "/simulate/api/livekit/call-config/{call_id}/",
        "/simulate/api/livekit/transcripts/{call_id}/",
        "/simulate/api/livekit/phone-resolution/{phone_number}/",
        "/simulate/api/livekit/call-execution/{call_id}/",
        "/simulate/api/livekit/temporal-signal/",
        "/simulate/api/livekit/listener-token/{call_id}/",
        "/simulate/api/livekit/validate-credentials/",
        "/simulate/api/livekit/webhook/",
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


def test_livekit_runtime_contract_debt_stays_burned_down():
    report = (
        _repo_root()
        / "api_contracts"
        / "openapi"
        / "runtime-management-api-contract-debt.generated.json"
    )
    debt = json.loads(report.read_text())
    livekit_debt = [
        item
        for item in debt["app_wide_doc_only_input_contract_decorators"]
        if item["path"] == "futureagi/simulate/views/livekit_api.py"
    ]

    assert livekit_debt == []


@pytest.mark.django_db
@override_settings(INTERNAL_API_SECRET="test-secret")
def test_livekit_transcripts_rejects_unknown_request_fields():
    factory = APIRequestFactory()
    request = factory.post(
        f"/simulate/api/livekit/transcripts/{uuid.uuid4()}/",
        {
            "role": "user",
            "content": "hello",
            "start_time_ms": 0,
            "displayName": "Future AGI",
        },
        format="json",
        HTTP_AUTHORIZATION="Bearer test-secret",
    )

    response = async_to_sync(TranscriptsView.as_view())(
        request,
        call_id=str(uuid.uuid4()),
    )

    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["message"] == "displayName: Unknown field."
    assert response.data["details"] == {"displayName": ["Unknown field."]}
