"""Credential-bearing discovery refuses redirects before interpreting their body.

Real Requests redirect handling runs against an in-process adapter on both
schemes. DNS, sockets, database access and credential decryption are replaced;
no provider or redirect target is contacted.
"""

import json
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from agentcc.tests.provider_discovery_test_support import (
    discovery_enabled as discovery_enabled,
)
from agentcc.views import provider_credential as view_module
from requests.adapters import BaseAdapter
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

API_KEY = "synthetic-redirect-credential-7q9w"
LOCATION_MARKER = "redirect-location-marker"
BODY_MARKER = "redirect-body-marker"
HEADER_MARKER = "redirect-header-marker"
REFUSAL_CODE = "PROVIDER_REDIRECT_REFUSED"
FORMATS = [
    pytest.param("anthropic", "anthropic", id="anthropic"),
    pytest.param("google", "google", id="google"),
    pytest.param("cohere", "openai", id="cohere"),
    pytest.param("openai", "openai", id="openai"),
]


def _models_payload(provider):
    if provider == "google":
        return {"models": [{"name": "models/z-model"}, {"name": "models/a-model"}]}
    if provider == "cohere":
        return {"models": [{"name": "z-model"}, {"name": "a-model"}]}
    return {"data": [{"id": "z-model"}, {"id": "a-model"}]}


class _DiscoveryAdapter(BaseAdapter):
    """Answer a single redirect, then success if Requests incorrectly follows."""

    def __init__(self, provider, status, destination):
        super().__init__()
        self.provider = provider
        self.status = status
        self.destination = destination
        self.sent = []
        self.responses = []
        self.location = None

    def send(self, request, **kwargs):
        self.sent.append(request)
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = self.status if len(self.sent) == 1 else 200
        payload = _models_payload(self.provider)
        if 300 <= response.status_code < 400:
            response.reason = HEADER_MARKER
            response.headers["X-Upstream-Detail"] = HEADER_MARKER
            payload["error"] = BODY_MARKER
            if self.destination:
                origin = urlsplit(request.url)
                target = (
                    "https://redirect-target.example"
                    if self.destination == "cross-host"
                    else f"http://{origin.netloc}"
                )
                # A provider can reflect a query credential into Location.
                self.location = (
                    f"{target}/redirected-models?key={API_KEY}"
                    f"&sentinel={LOCATION_MARKER}"
                )
                response.headers["Location"] = self.location
        response._content = json.dumps(payload).encode()
        response._content_consumed = True
        response.json = MagicMock(wraps=response.json)
        self.responses.append(response)
        return response

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _no_network():
    with (
        patch.object(socket, "socket", side_effect=AssertionError("socket forbidden")),
        patch.object(
            socket, "getaddrinfo", side_effect=AssertionError("DNS forbidden")
        ),
    ):
        yield


def _fetch(provider, api_format, client, credential_source, status, destination):
    base_url = "https://provider.example" if client == "safe-session" else ""
    adapter = _DiscoveryAdapter(provider, status, destination)
    session = requests.Session()
    session.trust_env = False
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    saved = credential_source == "saved"
    data = {"provider_name": provider}
    if not saved:
        data.update(api_key=API_KEY, api_format=api_format, base_url=base_url)
    request = Request(
        APIRequestFactory().post(
            "/agentcc/provider-credentials/fetch_models/", data, format="json"
        ),
        parsers=[JSONParser()],
    )
    request.organization = SimpleNamespace(id="org-redirect-test")
    view = view_module.AgentccProviderCredentialViewSet()
    view.request = request
    view.format_kwarg = None
    view.kwargs = {}
    try:
        with (
            patch.object(view_module, "logger") as logger,
            patch.object(view_module, "AgentccProviderCredential") as model,
            patch.object(
                view_module.CredentialManager,
                "decrypt",
                return_value={"api_key": API_KEY},
            ) as decrypt,
            patch.object(
                view_module, "private_provider_urls_allowed", return_value=False
            ),
            patch.object(view_module, "ensure_public_http_url") as validate,
            patch.object(
                view_module, "build_ssrf_safe_session", return_value=session
            ) as build,
            # Leave requests.get intact: only replace its session factory.
            patch.object(requests.sessions, "Session", return_value=session) as factory,
            patch.object(
                session, "request", wraps=session.request
            ) as transport_request,
        ):
            model.no_workspace_objects.filter.return_value.first.return_value = (
                SimpleNamespace(
                    encrypted_credentials=b"synthetic-sealed",
                    base_url=base_url,
                    api_format=api_format,
                    extra_config={},
                    models_list=[],
                )
                if saved
                else None
            )
            response = view.fetch_models(request)
            if saved:
                decrypt.assert_called_once_with(b"synthetic-sealed")
            else:
                decrypt.assert_not_called()
            if client == "safe-session":
                validate.assert_called_once_with(
                    base_url,
                    view_module.PROVIDER_URL_ERROR,
                    allow_private=False,
                    private_message=view_module.PROVIDER_PRIVATE_URL_ERROR,
                )
                build.assert_called_once_with(
                    "Connection to private address blocked", allow_private=False
                )
                factory.assert_not_called()
            else:
                validate.assert_not_called()
                build.assert_not_called()
                factory.assert_called_once_with()
            transport_request.assert_called_once()
            assert transport_request.call_args.kwargs["timeout"] == 15
            return response, adapter, transport_request.call_args.kwargs, logger
    finally:
        session.close()


def _assert_original_credential(adapter, provider):
    original = adapter.sent[0]
    assert original.method == "GET"
    assert original.body is None
    assert urlsplit(original.url).scheme == "https"
    if provider == "google":
        assert parse_qs(urlsplit(original.url).query) == {"key": [API_KEY]}
        assert urlsplit(original.url).path == "/v1beta/models"
    else:
        assert urlsplit(original.url).path == "/v1/models"
        header = "x-api-key" if provider == "anthropic" else "Authorization"
        value = API_KEY if provider == "anthropic" else f"Bearer {API_KEY}"
        assert original.headers[header] == value


def _assert_refused(response, adapter, kwargs, logger):
    assert len(adapter.sent) == 1
    # Every possible follow-up, including a downgrade to the same host, is absent.
    assert not any("/redirected-models" in request.url for request in adapter.sent)
    assert not any(
        API_KEY in request.url or API_KEY in repr(dict(request.headers))
        for request in adapter.sent[1:]
    )
    assert kwargs.get("allow_redirects") is False
    assert response.status_code == 400
    assert response.data["code"] == REFUSAL_CODE
    assert response.data["result"] == view_module.PROVIDER_REDIRECT_MESSAGE
    exposed = json.dumps(response.data) + repr(logger.mock_calls)
    for value in (API_KEY, LOCATION_MARKER, BODY_MARKER, HEADER_MARKER):
        assert value not in exposed
    if adapter.location:
        assert adapter.location not in exposed
        assert urlsplit(adapter.location).netloc not in exposed
    adapter.responses[0].json.assert_not_called()
    logger.exception.assert_not_called()


@pytest.mark.parametrize("provider,api_format", FORMATS)
@pytest.mark.parametrize("client", ["safe-session", "requests-module"])
@pytest.mark.parametrize("credential_source", ["saved", "raw"])
@pytest.mark.parametrize("status", [302, 307])
@pytest.mark.parametrize("destination", ["cross-host", "downgrade"])
def test_provider_redirect_never_sends_a_second_request(
    provider, api_format, client, credential_source, status, destination
):
    response, adapter, kwargs, logger = _fetch(
        provider, api_format, client, credential_source, status, destination
    )
    _assert_original_credential(adapter, provider)
    _assert_refused(response, adapter, kwargs, logger)


@pytest.mark.parametrize("provider,api_format", FORMATS)
@pytest.mark.parametrize("client", ["safe-session", "requests-module"])
@pytest.mark.parametrize("status", [300, 304, 399])
def test_other_3xx_without_location_is_still_a_typed_refusal(
    provider, api_format, client, status
):
    response, adapter, kwargs, logger = _fetch(
        provider, api_format, client, "saved", status, None
    )
    _assert_original_credential(adapter, provider)
    _assert_refused(response, adapter, kwargs, logger)


@pytest.mark.parametrize("provider,api_format", FORMATS)
@pytest.mark.parametrize("client", ["safe-session", "requests-module"])
@pytest.mark.parametrize("credential_source", ["saved", "raw"])
def test_non_redirect_model_discovery_remains_unchanged(
    provider, api_format, client, credential_source
):
    response, adapter, _, logger = _fetch(
        provider, api_format, client, credential_source, 200, None
    )
    assert len(adapter.sent) == 1
    _assert_original_credential(adapter, provider)
    assert response.status_code == 200
    assert response.data["result"] == {"models": ["a-model", "z-model"]}
    adapter.responses[0].json.assert_called_once_with()
    assert API_KEY not in json.dumps(response.data) + repr(logger.mock_calls)
