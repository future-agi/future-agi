import inspect
import json
import uuid
from pathlib import Path

import pytest
from asgiref.sync import async_to_sync
from django.test import RequestFactory, override_settings
from rest_framework.test import APIRequestFactory

from simulate.views.livekit_api import (
    CallConfigView,
    CallExecutionUpdateView,
    LiveCallListenerTokenView,
    LiveKitWebhookView,
    PhoneResolutionView,
    TemporalSignalView,
    TranscriptsView,
    ValidateLiveKitCredentialsView,
)

ANONYMOUS_LIVEKIT_CALL_ID = "00000000-0000-4000-8000-000000001023"
ANONYMOUS_LIVEKIT_PHONE_NUMBER = "+15551234567"


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


async def _await_response(response):
    return await response


def _dispatch_view(view_class, request, **kwargs):
    response = view_class.as_view()(request, **kwargs)
    if inspect.isawaitable(response):
        response = async_to_sync(_await_response)(response)
    return response


def _json_request(factory, method, path, body=None):
    request_method = getattr(factory, method.lower())
    if body is None:
        return request_method(path)
    return request_method(path, body, format="json")


def _django_json_request(factory, method, path, body=None):
    request_method = getattr(factory, method.lower())
    if body is None:
        return request_method(path)
    return request_method(path, data=json.dumps(body), content_type="application/json")


def _client_json_request(client, method, path, body=None):
    request_method = getattr(client, method.lower())
    if body is None:
        return request_method(path)
    return request_method(path, data=json.dumps(body), content_type="application/json")


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


@pytest.mark.parametrize(
    ("method", "path", "view_class", "kwargs", "body"),
    [
        (
            "get",
            f"/simulate/api/livekit/call-config/{ANONYMOUS_LIVEKIT_CALL_ID}/",
            CallConfigView,
            {"call_id": ANONYMOUS_LIVEKIT_CALL_ID},
            None,
        ),
        (
            "patch",
            f"/simulate/api/livekit/call-execution/{ANONYMOUS_LIVEKIT_CALL_ID}/",
            CallExecutionUpdateView,
            {"call_id": ANONYMOUS_LIVEKIT_CALL_ID},
            {},
        ),
        (
            "get",
            f"/simulate/api/livekit/phone-resolution/{ANONYMOUS_LIVEKIT_PHONE_NUMBER}/",
            PhoneResolutionView,
            {"phone_number": ANONYMOUS_LIVEKIT_PHONE_NUMBER},
            None,
        ),
        (
            "post",
            "/simulate/api/livekit/temporal-signal/",
            TemporalSignalView,
            {},
            {},
        ),
        (
            "post",
            f"/simulate/api/livekit/transcripts/{ANONYMOUS_LIVEKIT_CALL_ID}/",
            TranscriptsView,
            {"call_id": ANONYMOUS_LIVEKIT_CALL_ID},
            {},
        ),
    ],
)
@override_settings(INTERNAL_API_SECRET="test-secret")
def test_livekit_internal_routes_reject_missing_worker_bearer_before_work(
    method,
    path,
    view_class,
    kwargs,
    body,
):
    factory = RequestFactory()
    request = _django_json_request(factory, method, path, body)

    response = _dispatch_view(view_class, request, **kwargs)

    assert response.status_code == 401
    assert "Missing Bearer token" in str(response.data)


@override_settings(INTERNAL_API_SECRET="")
def test_livekit_internal_routes_reject_missing_worker_bearer_before_secret_config():
    factory = RequestFactory()
    request = _django_json_request(
        factory,
        "get",
        f"/simulate/api/livekit/call-config/{ANONYMOUS_LIVEKIT_CALL_ID}/",
    )

    response = _dispatch_view(
        CallConfigView,
        request,
        call_id=ANONYMOUS_LIVEKIT_CALL_ID,
    )

    assert response.status_code == 401
    assert "Missing Bearer token" in str(response.data)
    assert "INTERNAL_API_SECRET not configured" not in str(response.data)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "get",
            f"/simulate/api/livekit/call-config/{ANONYMOUS_LIVEKIT_CALL_ID}/",
            None,
        ),
        (
            "patch",
            f"/simulate/api/livekit/call-execution/{ANONYMOUS_LIVEKIT_CALL_ID}/",
            {},
        ),
        (
            "get",
            f"/simulate/api/livekit/phone-resolution/{ANONYMOUS_LIVEKIT_PHONE_NUMBER}/",
            None,
        ),
        ("post", "/simulate/api/livekit/temporal-signal/", {}),
        (
            "post",
            f"/simulate/api/livekit/transcripts/{ANONYMOUS_LIVEKIT_CALL_ID}/",
            {},
        ),
    ],
)
@override_settings(INTERNAL_API_SECRET="test-secret")
def test_livekit_internal_url_routes_reject_missing_bearer_without_html_500(
    client,
    method,
    path,
    body,
):
    response = _client_json_request(client, method, path, body)

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["message"] == "Missing Bearer token"


@pytest.mark.parametrize(
    ("method", "path", "view_class", "kwargs", "body"),
    [
        (
            "get",
            f"/simulate/api/livekit/listener-token/{ANONYMOUS_LIVEKIT_CALL_ID}/",
            LiveCallListenerTokenView,
            {"call_id": ANONYMOUS_LIVEKIT_CALL_ID},
            None,
        ),
        (
            "post",
            "/simulate/api/livekit/validate-credentials/",
            ValidateLiveKitCredentialsView,
            {},
            {},
        ),
    ],
)
def test_livekit_user_routes_reject_anonymous_before_work(
    method,
    path,
    view_class,
    kwargs,
    body,
):
    factory = APIRequestFactory()
    request = _json_request(factory, method, path, body)

    response = _dispatch_view(view_class, request, **kwargs)

    assert response.status_code in (401, 403)
    assert "Authentication credentials" in str(response.data)


@override_settings(
    LIVEKIT_API_KEY="test-livekit-key",
    LIVEKIT_API_SECRET="test-livekit-secret",
)
def test_livekit_webhook_rejects_missing_authorization_before_verification():
    factory = APIRequestFactory()
    request = factory.post("/simulate/api/livekit/webhook/", {}, format="json")

    response = _dispatch_view(LiveKitWebhookView, request)

    assert response.status_code == 401
    assert response.data["code"] == "not_authenticated"
    assert response.data["message"] == "Missing Authorization header"


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
