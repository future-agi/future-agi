"""R3-H6-001: same-tab OAuth completion must offer a safe way back.

Only the completion service is synthetic; render the real callback view without
vendor requests or stored tokens.
"""

from html.parser import HTMLParser
from unittest.mock import Mock
from urllib.parse import urljoin

import pytest
from django.test import RequestFactory

from agentcc.views import mcp_connect


CALLBACK_PATH = "/agentcc/mcp/connect/callback/"
RETURN_PATH = "/dashboard/gateway/integrations"
COOKIE_NAME = "__Host-mcp-connect-callback-test"


class CallbackPage(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.elements = []
        self.text = []
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def assert_safe_return_page(response, request, expected_href=RETURN_PATH):
    body = response.content.decode()
    page = CallbackPage(body)
    links = [attrs for tag, attrs in page.elements if tag == "a"]
    assert links == [{"href": expected_href}]
    assert "Return to Integrations" in page.text
    assert "close this window" not in body.lower()
    assert not response.has_header("Location")
    assert not response.has_header("Refresh")
    assert response["Cache-Control"] == "no-store"
    assert response["Pragma"] == "no-cache"
    assert response["Referrer-Policy"] == "no-referrer"
    assert response["X-Frame-Options"] == "DENY"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Security-Policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )
    assert not any(tag in {"script", "base", "form"} for tag, _ in page.elements)
    assert not any(
        tag == "meta" and attrs.get("http-equiv", "").lower() == "refresh"
        for tag, attrs in page.elements
    )
    for value in request.GET.values():
        assert value not in body


@pytest.fixture
def callback_request(settings):
    # Existing cases explicitly cover the unconfigured, root-relative fallback.
    settings.FRONTEND_BASE_URL = ""
    settings.ALLOWED_HOSTS = ["api.example.test"]
    # All values are synthetic. None may become copy, markup or a return URL.
    return RequestFactory().get(
        CALLBACK_PATH,
        {
            "code": "synthetic-code-not-real",
            "state": "synthetic-state-not-real",
            "iss": "https://issuer.example.com/",
            "error_description": '<script>alert("callback-input")</script>',
            "next": "https://outside.example.com/collect",
            "return_to": "//outside.example.com/collect",
            "redirect_uri": "javascript:alert('callback-input')",
        },
        secure=True,
        HTTP_HOST="api.example.test",
    )


@pytest.fixture
def completion(monkeypatch):
    monkeypatch.setattr(mcp_connect.connect, "callback_hidden", Mock(return_value=False))
    complete = Mock()
    monkeypatch.setattr(mcp_connect.connect, "complete", complete)
    return complete


@pytest.mark.parametrize(
    "ok,reason,status",
    [
        pytest.param(True, "connected", 200, id="connected"),
        pytest.param(False, "denied", 400, id="denied"),
        pytest.param(False, "expired", 400, id="expired"),
        pytest.param(False, "invalid_state", 400, id="invalid-state"),
        pytest.param(False, '<script>alert("unknown-reason")</script>', 400, id="unknown"),
    ],
)
def test_callback_returns_to_integrations_without_oauth_parameters(
    completion, callback_request, ok, reason, status
):
    completion.return_value = mcp_connect.connect.CompleteResult(
        ok, reason, cookie_name=COOKIE_NAME
    )

    response = mcp_connect.connect_callback(callback_request)

    assert response.status_code == status
    assert_safe_return_page(response, callback_request)
    completion.assert_called_once_with(callback_request.GET, callback_request.COOKIES)
    cookie = response.cookies[COOKIE_NAME]
    assert cookie.value == ""
    assert cookie["max-age"] == 0
    assert cookie["path"] == "/"
    assert cookie["samesite"] == "Lax"
    body = response.content.decode()
    if ok:
        assert "Connected. You can return to Future AGI." in body
    else:
        assert "Not connected." in body
        if reason.startswith("<"):
            assert reason not in body
            assert "The sign-in did not complete." in body


@pytest.mark.parametrize("status", [403, 409, 502])
def test_refused_callback_keeps_status_and_offers_safe_return(
    completion, callback_request, status
):
    completion.side_effect = mcp_connect.connect.ConnectRefused(
        "client_changed", "synthetic-private-detail-not-for-display", status
    )

    response = mcp_connect.connect_callback(callback_request)

    assert response.status_code == status
    assert_safe_return_page(response, callback_request)
    assert "Not connected." in response.content.decode()
    assert "synthetic-private-detail-not-for-display" not in response.content.decode()
    assert not response.cookies


@pytest.mark.parametrize(
    "frontend_base_url",
    [
        pytest.param("https://app.example.test", id="console-origin"),
        pytest.param(
            "https://app.example.test/old-console/?ignored=config-query#config-fragment",
            id="discard-path-query-fragment",
        ),
    ],
)
@pytest.mark.parametrize(
    "outcome,status",
    [("connected", 200), ("denied", 400), ("refused", 403)],
)
def test_callback_returns_to_configured_console_across_origins(
    completion, callback_request, settings, frontend_base_url, outcome, status
):
    settings.FRONTEND_BASE_URL = frontend_base_url
    # A start-link override must not replace the callback's fixed return path.
    settings.AGENTCC_MCP_CONNECT_CONSOLE_URL = (
        "https://outside.example.test/start-only?connect=not-a-return-target"
    )
    if outcome == "refused":
        completion.side_effect = mcp_connect.connect.ConnectRefused(
            "client_changed", "synthetic-private-detail-not-for-display", status
        )
    else:
        completion.return_value = mcp_connect.connect.CompleteResult(
            outcome == "connected", outcome
        )

    response = mcp_connect.connect_callback(callback_request)

    assert response.status_code == status
    page = CallbackPage(response.content.decode())
    links = [attrs for tag, attrs in page.elements if tag == "a"]
    assert len(links) == 1
    expected_url = "https://app.example.test/dashboard/gateway/integrations"
    # Resolve as a browser would from the API callback, not from the console.
    callback_url = callback_request.build_absolute_uri()
    assert callback_url.startswith("https://api.example.test" + CALLBACK_PATH + "?")
    assert urljoin(callback_url, links[0]["href"]) == expected_url
    assert_safe_return_page(response, callback_request, expected_href=expected_url)
    completion.assert_called_once_with(callback_request.GET, callback_request.COOKIES)
    assert "synthetic-private-detail-not-for-display" not in response.content.decode()
