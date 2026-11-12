"""A saved provider key never comes back in a fetch_models answer, and rotate
stores credential strings trimmed, as create does.

fetch_models is open to every org member (it is not in
``gateway_admin_actions``) and sends the saved key to the provider in a
header. requests refuses some header values with ``InvalidHeader`` and
http.client refuses others with a plain ``ValueError``; both messages quote the
whole header value, ``Bearer <saved key>`` included, and the view's
``except ValueError`` handler used to answer with that message. A key with a
trailing newline or a leading space (rotate stored request data untrimmed) was
therefore readable by any member of the org.

No network: the provider is a requests transport adapter that runs the header
checks urllib3's connection runs (http.client's ``putheader``) on a connection
it never opens, then answers 401. No database: the model manager, the
credential cipher and the config push are patched.
"""

import http.client
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from agentcc.tests.provider_discovery_test_support import (
    discovery_enabled as discovery_enabled,
)
from agentcc.views import provider_credential as view_module
from agentcc.views.provider_credential import AgentccProviderCredentialViewSet
from requests.adapters import BaseAdapter
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

# Random-looking, so no 5-character window of it occurs in a fixed message.
# synthetic test value (R-LIT pattern)
SECRET = ''.join(('sk-Z', 'q8vT', '3nW5', 'yK1b', 'R7mL', '2xC9', 'dF4h', 'J6gP', '0sA'))


class _HeaderCheckingAdapter(BaseAdapter):
    """The provider: checks headers as the real transport would, sends nothing."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, request, **kwargs):
        conn = http.client.HTTPConnection("127.0.0.1", 9)
        try:
            conn.putrequest(
                request.method,
                request.path_url,
                skip_host=True,
                skip_accept_encoding=True,
            )
            for name, value in request.headers.items():
                conn.putheader(name, value)
            # No endheaders(): the connection is never opened.
        finally:
            conn.close()
        self.sent.append(request)
        response = requests.Response()
        response.status_code = 401
        response.url = request.url
        response.request = request
        response._content = b'{"error": {"message": "invalid api key"}}'
        return response

    def close(self):
        pass


def _session():
    session = requests.Session()
    session.trust_env = False
    adapter = _HeaderCheckingAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session, adapter


def _drf_request(data):
    factory = APIRequestFactory()
    request = Request(
        factory.post(
            "/agentcc/provider-credentials/fetch_models/", data, format="json"
        ),
        parsers=[JSONParser()],
    )
    request.organization = SimpleNamespace(id="org-1")
    return request


def _view(request):
    view = AgentccProviderCredentialViewSet()
    view.request = request
    view.format_kwarg = None
    view.kwargs = {}
    return view


def _fetch(data, *, saved=None, session_get=None, logger=None):
    """POST fetch_models with ``saved`` as the org's stored credential."""
    session, adapter = _session()
    get = session_get or session.get
    request = _drf_request(data)
    with (
        patch.object(view_module, "logger", logger or MagicMock()),
        patch.object(view_module, "AgentccProviderCredential") as model,
        patch.object(
            view_module.CredentialManager,
            "decrypt",
            return_value={"api_key": saved["api_key"]} if saved else {},
        ),
        patch.object(view_module, "ensure_public_http_url"),
        patch.object(view_module, "build_ssrf_safe_session", return_value=session),
        # A credential saved without a base URL is fetched through the
        # requests module itself.
        patch.object(view_module, "http_requests", SimpleNamespace(get=get)),
    ):
        model.no_workspace_objects.filter.return_value.first.return_value = (
            SimpleNamespace(
                encrypted_credentials=b"sealed",
                base_url=saved.get("base_url", ""),
                api_format=saved.get("api_format", "openai"),
                extra_config={},
                models_list=[],
            )
            if saved
            else None
        )
        response = _view(request).fetch_models(request)
    return response, adapter


def _assert_no_fragment(response, key):
    body = json.dumps(response.data, default=str)
    core = key.strip()
    assert len(core) > 4
    leaked = [core[i : i + 5] for i in range(len(core) - 4) if core[i : i + 5] in body]
    assert not leaked, f"response body carries part of the key: {leaked}"


@pytest.mark.parametrize(
    "provider_name, api_format, base_url, key",
    [
        # http.client refuses the trailing newline with a ValueError whose
        # message is the whole header value.
        pytest.param(
            "openai",
            "openai",
            "https://api.provider.example/v1",
            SECRET + "\n",
            id="openai-trailing-newline",
        ),
        pytest.param(
            "openai", "openai", "", SECRET + "\n", id="openai-default-host-newline"
        ),
        pytest.param(
            "anthropic",
            "anthropic",
            "",
            SECRET + "\n",
            id="anthropic-trailing-newline",
        ),
        # requests refuses a leading space in x-api-key with InvalidHeader.
        pytest.param(
            "anthropic", "anthropic", "", " " + SECRET, id="anthropic-leading-space"
        ),
        # A carriage return: InvalidHeader on every header branch.
        pytest.param(
            "cohere", "openai", "", SECRET + "\r\n", id="cohere-carriage-return"
        ),
        # "Bearer  <key>" passes both checks and reaches the provider; it must
        # still never come back.
        pytest.param("openai", "openai", "", " " + SECRET, id="openai-leading-space"),
    ],
)
def test_fetch_models_never_echoes_a_malformed_saved_key(
    provider_name, api_format, base_url, key
):
    response, adapter = _fetch(
        {"provider_name": provider_name},
        saved={"api_key": key, "base_url": base_url, "api_format": api_format},
    )

    _assert_no_fragment(response, key)
    assert response.status_code == 400, response.data
    message = json.dumps(response.data)
    assert "malformed" in message and "rotate" in message.lower()
    # Refused before any request is built: the key never left.
    assert adapter.sent == []


def test_fetch_models_never_echoes_a_malformed_key_in_the_request():
    response, adapter = _fetch(
        {
            "api_key": SECRET + "\n",
            "base_url": "https://api.provider.example/v1",
            "api_format": "openai",
        }
    )

    _assert_no_fragment(response, SECRET)
    assert response.status_code == 400, response.data
    assert "malformed" in json.dumps(response.data)
    assert adapter.sent == []


def test_fetch_models_maps_invalid_header_to_a_fixed_message():
    """InvalidHeader is a ValueError; it is answered with a fixed message,
    never with its own text (which quotes the header value)."""

    def raise_invalid_header(url, headers=None, **kwargs):
        value = (headers or {}).get("Authorization", "")
        raise requests.exceptions.InvalidHeader(
            "Invalid leading whitespace, reserved character(s), or return "
            f"character(s) in header value: {value!r}"
        )

    response, _ = _fetch(
        {"provider_name": "openai"},
        saved={"api_key": SECRET, "base_url": "", "api_format": "openai"},
        session_get=raise_invalid_header,
    )

    _assert_no_fragment(response, SECRET)
    assert response.status_code == 400, response.data
    assert "malformed" in json.dumps(response.data)


def test_fetch_models_with_a_well_formed_saved_key_still_reaches_the_provider():
    response, adapter = _fetch(
        {"provider_name": "openai"},
        saved={
            "api_key": SECRET,
            "base_url": "https://api.provider.example/v1",
            "api_format": "openai",
        },
    )

    # A 401 remains a discovery verdict for the existing dialog's Save guard,
    # with actionable fixed text rather than the provider's response/URL.
    _assert_no_fragment(response, SECRET)
    assert response.status_code == 200, response.data
    assert response.data["result"]["models"] == []
    assert response.data["result"]["code"] == "provider_authentication_failed"
    assert "rotate" in response.data["result"]["error"]
    assert len(adapter.sent) == 1
    assert adapter.sent[0].headers["Authorization"] == f"Bearer {SECRET}"


def _assert_no_fragment_in_logs(logger, key):
    logged = " ".join(repr(c) for c in logger.mock_calls)
    assert logger.mock_calls, "fetch_models logged nothing"
    leaked = [key[i : i + 5] for i in range(len(key) - 4) if key[i : i + 5] in logged]
    assert not leaked, f"the log carries part of the key: {leaked}"


def _connection_error(url, params=None, **kwargs):
    # What urllib3/requests raise when the provider cannot be reached: the
    # message carries the request path, query string included.
    raise requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): "
        f"Max retries exceeded with url: /v1beta/models?key={(params or {}).get('key')} "
        "(Caused by NewConnectionError('Failed to establish a new connection'))"
    )


def _read_timeout(url, params=None, **kwargs):
    raise requests.exceptions.ReadTimeout(
        "HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): "
        f"Read timed out. (read timeout=15) url: /v1beta/models?key={(params or {}).get('key')}"
    )


@pytest.mark.parametrize(
    "provider_name, api_format, base_url, session_get, error_type, status_code",
    [
        # The provider answers 401: raise_for_status() says "for url:
        # https://.../v1beta/models?key=<saved key>".
        pytest.param(
            "gemini", "gemini", "", None, "HTTPError", 401, id="gemini-http-error"
        ),
        pytest.param(
            "google",
            "google",
            "https://gemini.provider.example",
            None,
            "HTTPError",
            401,
            id="google-saved-base-http-error",
        ),
        # The Google preset saved in the openai format is still listed at
        # Google's host, key in the query string.
        pytest.param(
            "google", "openai", "", None, "HTTPError", 401, id="google-openai-format"
        ),
        pytest.param(
            "gemini",
            "gemini",
            "",
            _connection_error,
            "ConnectionError",
            None,
            id="connection-error",
        ),
        pytest.param(
            "gemini",
            "gemini",
            "",
            _read_timeout,
            "ReadTimeout",
            None,
            id="read-timeout",
        ),
    ],
)
def test_fetch_models_never_logs_a_saved_key_sent_in_the_query_string(
    provider_name, api_format, base_url, session_get, error_type, status_code
):
    """The Gemini branch sends the key as ?key=; requests puts the URL in its
    HTTPError, ConnectionError and Timeout messages, and the view's generic
    handler logged str(e). The answer was already generic; the log must be
    too: the error's type and the HTTP status, never its text."""
    logger = MagicMock()
    response, adapter = _fetch(
        {"provider_name": provider_name},
        saved={"api_key": SECRET, "base_url": base_url, "api_format": api_format},
        session_get=session_get,
        logger=logger,
    )

    _assert_no_fragment(response, SECRET)
    expected_status = {"HTTPError": 200, "ConnectionError": 503, "ReadTimeout": 504}
    assert response.status_code == expected_status[error_type], response.data
    if error_type == "HTTPError":
        assert response.data["result"]["models"] == []
        assert response.data["result"]["code"] == "provider_authentication_failed"
        assert "rotate" in response.data["result"]["error"]
    else:
        assert response.data["code"] in {
            "upstream_connection_failed",
            "upstream_timeout",
        }
    _assert_no_fragment_in_logs(logger, SECRET)
    if session_get is None:
        # The key did reach the provider, in the query string.
        assert len(adapter.sent) == 1 and SECRET in adapter.sent[0].url
    call = logger.warning.call_args
    assert call.args == ("fetch_models_error",)
    assert call.kwargs.get("error_type") == error_type
    assert call.kwargs.get("status_code") == status_code


@pytest.mark.parametrize(
    "submitted, stored",
    [
        pytest.param(
            {"api_key": "  " + SECRET + "\n"}, {"api_key": SECRET}, id="api-key"
        ),
        pytest.param(
            {
                "aws_access_key_id": "\tAKIAEXAMPLEKEY01 ",
                "aws_secret_access_key": " " + SECRET + "\r\n",
                "aws_session_token": " session-fixture\n",
                "api_key": " " + SECRET + "\t",
            },
            {
                "aws_access_key_id": "AKIAEXAMPLEKEY01",
                "aws_secret_access_key": SECRET,
                "aws_session_token": "session-fixture",
                "api_key": SECRET,
            },
            id="every-string-value",
        ),
    ],
)
def test_rotate_stores_trimmed_credentials_like_create(submitted, stored):
    request = Request(
        APIRequestFactory().post(
            "/agentcc/provider-credentials/1/rotate/",
            {"credentials": submitted},
            format="json",
        ),
        parsers=[JSONParser()],
    )
    request.organization = SimpleNamespace(id="org-1", pk="org-1")
    instance = SimpleNamespace(
        base_url="",
        provider_name="openai",
        organization=request.organization,
        encrypted_credentials=b"",
        models_list=[],
        extra_config={},
        api_format="openai",
        last_rotated_at=None,
        save=MagicMock(),
    )
    view = _view(request)
    with (
        patch.object(view_module.transaction, "atomic"),
        patch.object(view_module, "lock_org"),
        patch.object(view, "get_object", return_value=instance),
        patch.object(
            view_module.CredentialManager,
            "encrypt",
            side_effect=lambda value: json.dumps(value).encode(),
        ) as encrypt,
        patch.object(view, "_push_config_to_gateway", return_value=True),
        patch.object(view_module, "AgentccProviderCredentialSerializer") as serializer,
    ):
        serializer.return_value.data = {}
        response = view.rotate(request, pk="1")

    assert response.status_code == 200, response.data
    encrypt.assert_called_once_with(stored)
    assert json.loads(instance.encrypted_credentials) == stored
