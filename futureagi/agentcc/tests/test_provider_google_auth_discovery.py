"""Google discovery verdicts through real Requests objects, with no network.

These pin the backend envelope consumed by AddProviderDialog.onSuccess. They
are not rendered Save/manual-model tests; those remain a separate UI obligation.
"""

import json
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from agentcc.tests.r19i_error_support import block_network
from agentcc.views import provider_credential as module

KEY = "synthetic-r19l1-replacement-key-never-use"
PRIVATE_BODY = "synthetic-r19l1-body SELECT token FROM private_keys"
PRIVATE_REASON = "synthetic-r19l1-http-reason"
ERROR_INFO_TYPE = "type.googleapis.com/google.rpc.ErrorInfo"
ERROR_DOMAIN = "googleapis.com"
AUTH_MESSAGE = (
    "Provider rejected the credential. Check its permissions or rotate it, then retry."
)


def _refusal():
    return {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": PRIVATE_BODY,
            "details": [
                {
                    "@type": ERROR_INFO_TYPE,
                    "domain": ERROR_DOMAIN,
                    "reason": "API_KEY_INVALID",
                    "metadata": {"api_key": KEY, "private_detail": PRIVATE_BODY},
                }
            ],
        }
    }


class _NoNetworkAdapter(BaseAdapter):
    def __init__(self, payload, status):
        super().__init__()
        self.payload = payload
        self.status = status
        self.sent = []

    def send(self, request, **kwargs):
        self.sent.append(request)
        response = requests.Response()
        response.status_code = self.status
        response.request = request
        response.url = request.url
        response.reason = PRIVATE_REASON
        response.encoding = "utf-8"
        response._content = (
            self.payload
            if isinstance(self.payload, bytes)
            else json.dumps(self.payload).encode("utf-8")
        )
        response.headers["Content-Type"] = "application/json"
        return response

    def close(self):
        pass


def _fetch(payload, *, status=400, provider="", api_format="google", saved=False):
    with ExitStack() as stack:
        block_network(stack)
        session = stack.enter_context(requests.Session())
        session.trust_env = False
        adapter = _NoNetworkAdapter(payload, status)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        stack.enter_context(
            patch.object(module, "http_requests", SimpleNamespace(get=session.get))
        )
        model = stack.enter_context(patch.object(module, "AgentccProviderCredential"))
        decrypt = stack.enter_context(patch.object(module.CredentialManager, "decrypt"))
        model.no_workspace_objects.filter.return_value.first.return_value = (
            SimpleNamespace(
                encrypted_credentials="synthetic-encrypted-placeholder",
                base_url="",
                api_format="google",
                extra_config={},
            )
            if saved
            else None
        )
        decrypt.return_value = {"api_key": KEY}
        logger = stack.enter_context(patch.object(module, "logger"))
        data = (
            {"provider_name": provider}
            if saved
            else {
                "api_key": KEY,
                "api_format": api_format,
                "provider_name": provider,
            }
        )
        request = Request(
            APIRequestFactory().post(
                "/agentcc/provider-credentials/fetch-models/", data, format="json"
            ),
            parsers=[JSONParser()],
        )
        request.organization = SimpleNamespace(id="synthetic-org")
        view = module.AgentccProviderCredentialViewSet()
        view.request = request
        view.kwargs = {}
        view.format_kwarg = None
        response = view.fetch_models(request)
        if saved:
            decrypt.assert_called_once_with("synthetic-encrypted-placeholder")
            model.no_workspace_objects.filter.assert_called_once_with(
                provider_name=provider,
                organization=request.organization,
                deleted=False,
            )
        else:
            decrypt.assert_not_called()
    assert len(adapter.sent) == 1
    assert isinstance(adapter.sent[0], requests.PreparedRequest)
    emitted = json.dumps(response.data, default=str) + repr(logger.mock_calls)
    private_parts = (KEY, PRIVATE_BODY, PRIVATE_REASON, adapter.sent[0].url)
    assert all(part not in emitted for part in private_parts), "Private data escaped"
    logger.exception.assert_not_called()
    return response, logger, adapter.sent[0]


def _assert_verdict(response):
    # A 200/result envelope invokes onSuccess, setting hasFetched=true with no
    # options. The existing dialog rejects typed replacement keys unless there
    # is a manual model. A non-2xx rejection would wrongly invoke onError.
    assert response.status_code == 200
    assert response.data["status"] is True
    assert response.data["result"] == {
        "models": [],
        "error": AUTH_MESSAGE,
        "code": "provider_authentication_failed",
    }
    assert "code" not in response.data


@pytest.mark.parametrize(
    "provider,api_format,mixed_details",
    [
        pytest.param("", "google", False, id="typed-google"),
        pytest.param("", "gemini", False, id="typed-gemini"),
        pytest.param("google", "openai", False, id="google-openai"),
        pytest.param("gemini", "openai", False, id="gemini-openai"),
        pytest.param("google_gemini", "openai", False, id="google-gemini-openai"),
        pytest.param("", "google", True, id="mixed-details"),
    ],
)
def test_google_invalid_key_is_a_private_discovery_verdict(
    provider, api_format, mixed_details
):
    payload = _refusal()
    if mixed_details:
        payload["error"]["details"][:0] = [None, "unrelated", {"reason": "UNKNOWN"}]
    response, logger, request = _fetch(
        payload, provider=provider, api_format=api_format
    )
    _assert_verdict(response)
    assert parse_qs(urlsplit(request.url).query) == {"key": [KEY]}
    logger.warning.assert_called_once_with(
        "fetch_models_error",
        error_type="ProviderAuthenticationRejected",
        status_code=400,
    )


def test_saved_google_invalid_key_keeps_the_empty_discovery_verdict():
    response, logger, request = _fetch(_refusal(), provider="google", saved=True)
    _assert_verdict(response)
    assert parse_qs(urlsplit(request.url).query) == {"key": [KEY]}
    logger.warning.assert_called_once()


def _unrecognized_payload(case):
    payload = _refusal()
    error = payload["error"]
    detail = error["details"][0]
    replacements = {
        "non-json": (PRIVATE_BODY + " API_KEY_INVALID").encode("utf-8"),
        "root-null": None,
        "root-list": [payload],
        "root-string": PRIVATE_BODY,
        "error-missing": {},
        "error-null": {"error": None},
        "error-list": {"error": [error]},
        "details-null": {"error": {"details": None}},
        "details-object": {"error": {"details": detail}},
        "details-string": {"error": {"details": "API_KEY_INVALID"}},
        "detail-null": {"error": {"details": [None]}},
        "detail-string": {"error": {"details": ["API_KEY_INVALID"]}},
        "top-level-reason": {"reason": "API_KEY_INVALID", "error": {}},
    }
    if case in replacements:
        return replacements[case]
    if case == "details-missing":
        del error["details"]
    elif case == "message-only":
        error.pop("details")
        error["message"] = "API_KEY_INVALID " + PRIVATE_BODY
    elif case == "metadata-reason":
        detail.pop("reason")
        detail["metadata"]["reason"] = "API_KEY_INVALID"
    else:
        field, variant = case.split("-", 1)
        field = "@type" if field == "type" else field
        if variant == "missing":
            detail.pop(field)
        else:
            detail[field] = {
                "type-other": "type.invalid/other.ErrorInfo",
                "type-suffix": ERROR_INFO_TYPE + "/unrelated",
                "domain-other": "unrelated.invalid",
                "reason-unknown": "API_DISABLED",
                "reason-lowercase": "api_key_invalid",
                "reason-list": ["API_KEY_INVALID"],
            }[case]
    return payload


@pytest.mark.parametrize(
    "case",
    [
        "non-json",
        "root-null",
        "root-list",
        "root-string",
        "error-missing",
        "error-null",
        "error-list",
        "details-missing",
        "details-null",
        "details-object",
        "details-string",
        "detail-null",
        "detail-string",
        "type-missing",
        "type-other",
        "type-suffix",
        "domain-missing",
        "domain-other",
        "reason-missing",
        "reason-unknown",
        "reason-lowercase",
        "reason-list",
        "message-only",
        "top-level-reason",
        "metadata-reason",
    ],
)
def test_unrecognized_google_400_is_not_a_key_verdict(case):
    response, logger, _ = _fetch(_unrecognized_payload(case))
    assert response.status_code == 400
    assert response.data["code"] == "provider_request_rejected"
    assert isinstance(response.data["result"], str)
    logger.warning.assert_called_once_with(
        "fetch_models_error", error_type="HTTPError", status_code=400
    )


@pytest.mark.parametrize("provider", ["openai", "anthropic", "cohere", "custom"])
def test_google_reason_on_other_protocols_is_not_a_key_verdict(provider):
    response, logger, _ = _fetch(_refusal(), provider=provider, api_format="openai")
    assert response.status_code == 400
    assert response.data["code"] == "provider_request_rejected"
    logger.warning.assert_called_once_with(
        "fetch_models_error", error_type="HTTPError", status_code=400
    )


@pytest.mark.parametrize("status", [401, 403, 404, 409, 429, 500, 502, 503, 504])
def test_google_error_info_does_not_override_other_http_statuses(status):
    response, logger, _ = _fetch(_refusal(), status=status)
    if status in (401, 403):
        _assert_verdict(response)
    else:
        assert response.status_code == (
            503 if status >= 500 else 429 if status == 429 else 400
        )
        assert response.data["code"] == (
            "provider_unavailable"
            if status >= 500
            else "provider_endpoint_not_found"
            if status == 404
            else "provider_request_rejected"
        )
        assert isinstance(response.data["result"], str)
    logger.warning.assert_called_once_with(
        "fetch_models_error", error_type="HTTPError", status_code=status
    )


@pytest.mark.parametrize("case", ["empty", "catalog"])
def test_successful_google_discovery_keeps_its_models(case):
    expected = [] if case == "empty" else ["synthetic-a", "synthetic-b"]
    payload = _refusal()
    payload["models"] = [{"name": "models/" + name} for name in reversed(expected)]
    response, logger, _ = _fetch(payload, status=200)
    assert response.status_code == 200
    assert response.data["status"] is True
    assert response.data["result"] == {"models": expected}
    logger.warning.assert_not_called()
