"""Falcon AI's connector calls go to addresses this deployment allows.

Every call Falcon AI makes for a connector, and every call it makes to sign a
connector in, goes through the platform's address check:

- the connection is made to an address the check allowed for the host;
- a redirect is refused, never followed;
- on Future AGI Cloud the address must be https://;
- where only public addresses are allowed, a host that no public server has
  (a single label, or an ending private networks use for their own services)
  is not looked up at all.

What the person or the model is told keeps the same fixed text on every path,
and the log names the reason's class only.

No outside network: name lookups answer from a table, every connection is
recorded and only loopback ones are made, and httpx's own transports (which
connect wherever a URL points) record and refuse every request handed to them.
"""

import asyncio
import inspect
import json
import secrets
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import httpcore
import httpx
import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from ee.falcon_ai import oauth_client, views_connectors
from ee.falcon_ai.credentials import encrypt_falcon_token
from ee.falcon_ai.mcp_proxy import MCPConnectorProxy
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.tests.test_oauth_at_rest_preflight import (
    persistent_test_key,  # noqa: F401 -- pytest fixture
)
from tfc.utils.ssrf_guard import AsyncGuardedHTTPTransport, GuardedHTTPTransport
from tfc.utils.tests.guarded_post_fakes import FakeResolver
from tfc.utils.tests.loopback_endpoints import (
    LoopbackServer,
    clear_proxy_env,
    treat_as_public,
)

PUBLIC_IP = "93.184.216.34"
LOOPBACK = frozenset({"127.0.0.1", "::1"})
TOOLS_HOST = "tools.example.org"
DENY_PRIVATE = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"
LINK_LOCAL_ALLOWED = "CUSTOM_MODEL_ALLOWED_LINK_LOCAL_CIDRS"
JSON_HEADERS = {"Content-Type": "application/json"}
TOOL = {
    "name": "lookup",
    "description": "Looks a record up",
    "inputSchema": {"type": "object"},
}
TOOL_ANSWER = "answer from the connector"
TOOL_ANSWERED = {"content": TOOL_ANSWER, "is_error": False}
TOOL_FAILED = {"content": "MCP tool call failed", "is_error": True}
REQUEST_FAILED = {"success": False, "error": "MCP request failed"}
FRESH_ACCESS = "fresh-access-value"

REFUSED = [
    pytest.param("https://127.0.0.1:8080/mcp", {}, id="loopback-address"),
    pytest.param("https://[::1]/mcp", {}, id="ipv6-loopback"),
    pytest.param("https://0.0.0.0/mcp", {}, id="unspecified-address"),
    pytest.param("https://10.0.0.7/mcp", {}, id="private-10"),
    pytest.param("https://172.16.4.2/mcp", {}, id="private-172-16"),
    pytest.param("https://192.168.1.20/mcp", {}, id="private-192-168"),
    pytest.param("https://100.64.0.9/mcp", {}, id="shared-address-space"),
    pytest.param("https://169.254.10.10/mcp", {}, id="link-local"),
    pytest.param("https://[fd00::12]/mcp", {}, id="ipv6-unique-local"),
    pytest.param("https://[::ffff:10.0.0.7]/mcp", {}, id="ipv4-mapped-ipv6"),
    pytest.param(
        "https://[64:ff9b::a00:7]/mcp", {}, id="nat64-form-of-a-private-address"
    ),
    pytest.param(
        "https://notes.example.com/mcp",
        {"notes.example.com": ["192.168.7.7"]},
        id="name-with-a-private-answer",
    ),
    pytest.param(
        "https://mixed.example.com/mcp",
        {"mixed.example.com": [PUBLIC_IP, "10.1.2.3"]},
        id="name-with-one-public-and-one-private-answer",
    ),
    pytest.param("https://unknown.example.com/mcp", {}, id="name-with-no-answer"),
]

# One of each kind, for the paths that do not run the whole table.
SOME_REFUSED = [
    pytest.param("https://10.0.0.7/mcp", {}, id="private-address"),
    pytest.param(
        "https://mixed.example.com/mcp",
        {"mixed.example.com": [PUBLIC_IP, "10.1.2.3"]},
        id="name-with-one-public-and-one-private-answer",
    ),
    pytest.param(
        "https://reports.default.svc.cluster.local/mcp",
        {"reports.default.svc.cluster.local": [PUBLIC_IP]},
        id="name-no-public-server-has",
    ),
    pytest.param(
        "http://tools.example.org/mcp",
        {TOOLS_HOST: [PUBLIC_IP]},
        id="plain-http-on-future-agi-cloud",
    ),
]

# No public server has one of these names. Each answers a public address in
# the lookup table, so only the name keeps it from being looked up.
NO_PUBLIC_SERVER = [
    pytest.param("https://localhost/mcp", id="localhost"),
    pytest.param("https://reports/mcp", id="single-label"),
    pytest.param("https://reports:8080/mcp", id="single-label-with-a-port"),
    pytest.param("https://reports.default.svc/mcp", id="svc-ending"),
    pytest.param(
        "https://reports.default.svc.cluster.local/mcp", id="cluster-local-ending"
    ),
    pytest.param("https://tools.internal/mcp", id="internal-ending"),
    pytest.param("https://printer.local/mcp", id="local-ending"),
    pytest.param("https://app.localhost/mcp", id="localhost-ending"),
]


def check(condition, message):
    # Only the message is shown on a failure, never the compared values.
    assert condition, message


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    clear_proxy_env(monkeypatch)
    monkeypatch.delenv(DENY_PRIVATE, raising=False)
    monkeypatch.delenv(LINK_LOCAL_ALLOWED, raising=False)
    MCPConnectorProxy._session_cache.clear()
    yield
    MCPConnectorProxy._session_cache.clear()


@pytest.fixture
def hosted():
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def self_hosted():
    with override_settings(CLOUD_DEPLOYMENT=""):
        yield


@pytest.fixture
def public_only_self_hosted(self_hosted, monkeypatch):
    """A self-hosted install that allows public addresses only, on which the
    loopback test server counts as public. Plain http is allowed there, so the
    test server can answer."""
    monkeypatch.setenv(DENY_PRIVATE, "true")
    treat_as_public(monkeypatch)


@pytest.fixture
def names(monkeypatch):
    """Name lookups answer from this table; an address answers as itself."""
    return FakeResolver().install(monkeypatch)


@pytest.fixture
def connects(monkeypatch, names):
    """Every connection httpcore opens, by the host it is opened to. Loopback
    addresses, and names the table answers with loopback only, are connected;
    every other connection is refused."""
    seen = []
    real_connect = httpcore.SyncBackend.connect_tcp
    real_async_connect = httpcore.AnyIOBackend.connect_tcp

    def loopback(host):
        answers = names.table.get(str(host).lower().rstrip("."), [str(host)])
        return bool(answers) and all(answer in LOOPBACK for answer in answers)

    def connect_tcp(self, host, port, *args, **kwargs):
        seen.append(host)
        if not loopback(host):
            raise httpcore.ConnectError("Only loopback connections are made here.")
        return real_connect(self, host, port, *args, **kwargs)

    async def async_connect_tcp(self, host, port, *args, **kwargs):
        seen.append(host)
        if not loopback(host):
            raise httpcore.ConnectError("Only loopback connections are made here.")
        return await real_async_connect(self, host, port, *args, **kwargs)

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect_tcp)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", async_connect_tcp)
    return seen


@pytest.fixture
def unchecked(monkeypatch):
    """httpx's own transports connect wherever a URL points. Every request one
    of them is handed is recorded here and refused; the list stays empty when
    every call goes through the address check's client."""
    handed = []
    real_handle = httpx.HTTPTransport.handle_request
    real_async_handle = httpx.AsyncHTTPTransport.handle_async_request

    def handle_request(self, request):
        if isinstance(self, GuardedHTTPTransport):
            return real_handle(self, request)
        handed.append(str(request.url))
        raise httpx.ConnectError("Refused by the test.", request=request)

    async def handle_async_request(self, request):
        if isinstance(self, AsyncGuardedHTTPTransport):
            return await real_async_handle(self, request)
        handed.append(str(request.url))
        raise httpx.ConnectError("Refused by the test.", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", handle_async_request
    )
    return handed


@pytest.fixture
def checked_calls(monkeypatch):
    """Calls made on an httpx client's get, post and stream. Each call is first
    bound to the real method's parameters (inspect.signature), so a call the
    real client would refuse fails here too; then the real method runs."""
    calls = []

    def url_of(name, args, kwargs):
        if "url" in kwargs:
            return str(kwargs["url"])
        return str(args[1] if name == "stream" else args[0])

    def install(cls, name):
        real = getattr(cls, name)
        signature = inspect.signature(real)
        label = f"{cls.__name__}.{name}"

        if inspect.iscoroutinefunction(real):

            async def method(self, *args, **kwargs):
                signature.bind(self, *args, **kwargs)
                calls.append((label, url_of(name, args, kwargs)))
                return await real(self, *args, **kwargs)

        else:

            def method(self, *args, **kwargs):
                signature.bind(self, *args, **kwargs)
                calls.append((label, url_of(name, args, kwargs)))
                return real(self, *args, **kwargs)

        monkeypatch.setattr(cls, name, method)

    install(httpx.Client, "get")
    install(httpx.Client, "post")
    install(httpx.Client, "stream")
    install(httpx.AsyncClient, "post")
    return calls


def rpc(body, result):
    return json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result})


def tool_answer(request):
    """A connector answering the connector protocol: its tool list, or the
    answer of a tool call."""
    body = json.loads(request.body or b"{}")
    if body.get("method") == "tools/list":
        return 200, JSON_HEADERS, rpc(body, {"tools": [TOOL]}).encode()
    content = [{"type": "text", "text": TOOL_ANSWER}]
    return 200, JSON_HEADERS, rpc(body, {"content": content}).encode()


class SessionAnswer:
    """A connector that needs a session: a call without a known session id is
    answered 400, and initialize hands out a new session id."""

    def __init__(self):
        self.sessions = []

    def __call__(self, request):
        body = json.loads(request.body or b"{}")
        method = body.get("method")
        if method == "initialize":
            session = f"session-{len(self.sessions) + 1}"
            self.sessions.append(session)
            headers = {**JSON_HEADERS, "Mcp-Session-Id": session}
            return 200, headers, rpc(body, {"protocolVersion": "2025-03-26"}).encode()
        if method == "notifications/initialized":
            return 202, {}, b""
        if request.headers.get("mcp-session-id") not in self.sessions:
            return 400, JSON_HEADERS, b'{"error": "a session is needed"}'
        return tool_answer(request)


class SignInAnswer:
    """A connector that signs in with OAuth: discovery, registration and the
    token address, next to the connector protocol at /mcp."""

    def __init__(self):
        self.base = ""

    def __call__(self, request):
        if request.target.startswith("/.well-known/oauth-authorization-server"):
            metadata = {
                "issuer": self.base,
                "authorization_endpoint": f"https://{TOOLS_HOST}/authorize",
                "token_endpoint": f"{self.base}/token",
                "registration_endpoint": f"{self.base}/register",
            }
            return 200, JSON_HEADERS, json.dumps(metadata).encode()
        if request.target == "/register":
            registered = {"client_id": "registered-client"}
            return 201, JSON_HEADERS, json.dumps(registered).encode()
        if request.target == "/token":
            tokens = {
                "access_token": FRESH_ACCESS,
                "refresh_token": "fresh-refresh-value",
                "expires_in": 3600,
            }
            return 200, JSON_HEADERS, json.dumps(tokens).encode()
        return tool_answer(request)


@contextmanager
def connector_server(names, answer=tool_answer):
    """A loopback server reached by the name tools.example.org; yields the
    server and its base address."""
    with LoopbackServer(answer) as server:
        names.add(TOOLS_HOST, "127.0.0.1")
        base = f"http://{TOOLS_HOST}:{server.port}"
        if isinstance(answer, SignInAnswer):
            answer.base = base
        yield server, base


def connector(url, **fields):
    """A connector the proxy can call without a database row."""
    values = {
        "name": "Reports tools",
        "server_url": url,
        "transport": "streamable_http",
        "auth_type": "none",
    }
    return MCPConnector(**{**values, **fields})


def saved_connector(user, workspace, url, **fields):
    return MCPConnector.no_workspace_objects.create(
        organization=user.organization,
        workspace=workspace,
        name="tools-" + secrets.token_hex(6),
        server_url=url,
        **fields,
    )


def oauth_fields(token_url, **fields):
    values = {
        "auth_type": "oauth",
        "oauth_client_id": "registered-client",
        "oauth_client_secret": encrypt_falcon_token("client-value"),
        "oauth_server_metadata": {
            "authorization_endpoint": f"https://{TOOLS_HOST}/authorize",
            "token_endpoint": token_url,
        },
    }
    return {**values, **fields}


def expired_sign_in():
    return {
        "oauth_access_token": encrypt_falcon_token("earlier-access-value"),
        "oauth_refresh_token": encrypt_falcon_token("refresh-value"),
        "oauth_token_expires_at": timezone.now() - timedelta(minutes=5),
    }


def proxy_without_saves(monkeypatch):
    """A proxy whose discovery results are recorded instead of saved."""
    proxy = MCPConnectorProxy()
    saved = SimpleNamespace(errors=[], discoveries=[])
    monkeypatch.setattr(
        proxy, "_save_error", lambda record, error: saved.errors.append(error)
    )
    monkeypatch.setattr(
        proxy,
        "_save_discovery",
        lambda record, tools: saved.discoveries.append(tools),
    )
    return proxy, saved


def post_view(view, user, record):
    request = APIRequestFactory().post("/connector/", {}, format="json")
    force_authenticate(request, user=user)
    request.organization = user.organization
    request.workspace = record.workspace
    return view.as_view()(request, connector_id=record.pk)


def callback(record, state):
    request = APIRequestFactory().get(
        "/callback/", {"code": "test-code", "state": state}
    )
    view = views_connectors.MCPConnectorOAuthCallbackView.as_view()
    return view(request, connector_id=record.pk)


def add_answers(names, answers):
    for host, addresses in answers.items():
        names.add(host, *addresses)


# Refused addresses: no connection, nothing handed to an unchecked client, and
# the fixed text the person or the model is given today.


@pytest.mark.parametrize(("url", "answers"), REFUSED)
def test_a_tool_call_to_an_address_this_deployment_refuses_opens_no_connection(
    url, answers, hosted, names, connects, unchecked
):
    add_answers(names, answers)

    result = MCPConnectorProxy().execute_tool_sync(connector(url), "lookup", {})

    assert unchecked == []
    assert connects == []
    assert result == TOOL_FAILED


@pytest.mark.parametrize(("url", "answers"), SOME_REFUSED)
def test_an_async_tool_call_to_an_address_this_deployment_refuses_opens_no_connection(
    url, answers, hosted, names, connects, unchecked
):
    add_answers(names, answers)

    result = asyncio.run(MCPConnectorProxy().execute_tool(connector(url), "lookup", {}))

    assert unchecked == []
    assert connects == []
    assert result == TOOL_FAILED


@pytest.mark.parametrize(("url", "answers"), SOME_REFUSED)
def test_discovering_tools_at_a_refused_address_opens_no_connection(
    url, answers, hosted, names, connects, unchecked, monkeypatch
):
    add_answers(names, answers)
    proxy, saved = proxy_without_saves(monkeypatch)

    result = proxy.discover_tools_sync(connector(url))

    assert unchecked == []
    assert connects == []
    assert result == REQUEST_FAILED
    assert saved.errors == ["MCP request failed"]
    assert saved.discoveries == []


@pytest.mark.parametrize(("url", "answers"), SOME_REFUSED)
def test_a_connection_test_at_a_refused_address_opens_no_connection(
    url, answers, hosted, names, connects, unchecked
):
    add_answers(names, answers)

    result = MCPConnectorProxy().test_connection_sync(connector(url))

    assert unchecked == []
    assert connects == []
    assert result == REQUEST_FAILED


@pytest.mark.parametrize("url", NO_PUBLIC_SERVER)
def test_under_public_only_a_name_no_public_server_has_is_not_looked_up(
    url, hosted, names, connects, unchecked
):
    names.add(urlsplit(url).hostname, PUBLIC_IP)

    result = MCPConnectorProxy().execute_tool_sync(connector(url), "lookup", {})

    assert unchecked == []
    assert names.lookups == []
    assert connects == []
    assert result == TOOL_FAILED


def test_a_public_only_self_hosted_install_does_not_look_up_such_a_name(
    self_hosted, monkeypatch, names, connects, unchecked
):
    monkeypatch.setenv(DENY_PRIVATE, "true")
    names.add("reports.internal", PUBLIC_IP)

    result = MCPConnectorProxy().execute_tool_sync(
        connector("http://reports.internal/mcp"), "lookup", {}
    )

    assert unchecked == []
    assert names.lookups == []
    assert connects == []
    assert result == TOOL_FAILED


def test_on_future_agi_cloud_a_plain_http_connector_is_not_called(
    hosted, names, connects, unchecked
):
    names.add(TOOLS_HOST, PUBLIC_IP)

    result = MCPConnectorProxy().execute_tool_sync(
        connector(f"http://{TOOLS_HOST}/mcp"), "lookup", {}
    )

    assert unchecked == []
    assert names.lookups == []
    assert connects == []
    assert result == TOOL_FAILED


def test_a_self_hosted_install_refuses_a_link_local_connector(
    self_hosted, names, connects, unchecked
):
    result = MCPConnectorProxy().execute_tool_sync(
        connector("http://169.254.10.10/mcp"), "lookup", {}
    )

    assert unchecked == []
    assert connects == []
    assert result == TOOL_FAILED


def test_a_self_hosted_install_connects_a_private_connector_at_the_checked_address(
    self_hosted, names, connects, unchecked
):
    names.add("reports.internal", "10.0.0.7")

    result = MCPConnectorProxy().execute_tool_sync(
        connector("http://reports.internal:8080/mcp"), "lookup", {}
    )

    assert unchecked == []
    # The connection is refused by the test, after the check allowed it.
    assert connects == ["10.0.0.7"]
    assert result == TOOL_FAILED


def test_the_log_of_a_refused_call_names_the_reason_class_and_not_the_address(
    hosted, names, connects, unchecked
):
    names.add("notes.example.com", "192.168.7.7")

    with patch("ee.falcon_ai.mcp_proxy.logger") as logger:
        result = MCPConnectorProxy().execute_tool_sync(
            connector("https://notes.example.com/mcp"), "lookup", {}
        )

    logged = str(logger.mock_calls)
    assert result == TOOL_FAILED
    assert "notes.example.com" not in logged
    assert "192.168.7.7" not in logged
    assert "EndpointBlocked" in logged


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("token_url", "answer"),
    [
        pytest.param(
            "https://tokens.example.org/token",
            "10.0.0.7",
            id="name-with-a-private-answer",
        ),
        pytest.param(
            "http://tokens.example.org/token",
            PUBLIC_IP,
            id="plain-http-on-future-agi-cloud",
        ),
    ],
)
def test_a_sign_in_with_a_refused_token_address_sends_nothing_and_saves_nothing(
    token_url, answer, hosted, names, connects, unchecked, user, workspace
):
    names.add("tokens.example.org", answer)
    state = secrets.token_urlsafe(16)
    record = saved_connector(
        user,
        workspace,
        f"https://{TOOLS_HOST}/mcp",
        **oauth_fields(
            token_url,
            oauth_state=state,
            oauth_code_verifier=secrets.token_urlsafe(48),
        ),
    )

    response = callback(record, state)

    assert unchecked == []
    assert connects == []
    assert response.status_code == 502
    assert b"Token exchange failed" in response.content
    stored = MCPConnector._base_manager.get(pk=record.pk)
    check(stored.oauth_access_token == "", "an access token was saved")
    check(stored.oauth_refresh_token == "", "a refresh token was saved")


@pytest.mark.django_db
def test_client_registration_at_a_refused_address_opens_no_connection(
    hosted, names, connects, unchecked, user, workspace
):
    names.add(TOOLS_HOST, PUBLIC_IP)
    names.add("register.example.org", "10.0.0.7")
    record = saved_connector(
        user,
        workspace,
        f"https://{TOOLS_HOST}/mcp",
        auth_type="oauth",
        oauth_server_metadata={
            "authorization_endpoint": f"https://{TOOLS_HOST}/authorize",
            "token_endpoint": f"https://{TOOLS_HOST}/token",
            "registration_endpoint": "https://register.example.org/register",
        },
    )

    response = post_view(views_connectors.MCPConnectorAuthenticateView, user, record)

    assert unchecked == []
    assert connects == []
    assert response.status_code == 502
    assert response.data["detail"] == "OAuth setup failed"
    assert "authorization_url" not in response.data


@pytest.mark.django_db
def test_oauth_discovery_at_a_refused_address_opens_no_connection(
    hosted, names, connects, unchecked, user, workspace
):
    names.add(TOOLS_HOST, "10.0.0.7")
    record = saved_connector(
        user, workspace, f"https://{TOOLS_HOST}/mcp", auth_type="oauth"
    )

    response = post_view(views_connectors.MCPConnectorAuthenticateView, user, record)

    assert unchecked == []
    assert connects == []
    assert response.status_code == 502
    assert response.data["detail"] == "OAuth setup failed"


@pytest.mark.django_db
def test_a_tool_call_that_renews_its_sign_in_does_not_reach_a_refused_token_address(
    hosted, names, connects, unchecked, user, workspace
):
    names.add(TOOLS_HOST, PUBLIC_IP)
    names.add("tokens.example.org", "10.0.0.7")
    record = saved_connector(
        user,
        workspace,
        f"https://{TOOLS_HOST}/mcp",
        **oauth_fields("https://tokens.example.org/token", **expired_sign_in()),
    )

    result = MCPConnectorProxy().execute_tool_sync(record, "lookup", {})

    assert unchecked == []
    assert connects == []
    assert result == TOOL_FAILED


# Redirects are refused, never followed.


@pytest.mark.django_db
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_oauth_discovery_does_not_follow_a_redirect(
    status, self_hosted, names, connects, user, workspace
):
    with LoopbackServer() as target:
        location = f"{target.base}/.well-known/oauth-authorization-server"
        moved = LoopbackServer(lambda request: (status, {"Location": location}, b""))
        with moved as server:
            names.add(TOOLS_HOST, "127.0.0.1")
            record = saved_connector(
                user,
                workspace,
                f"http://{TOOLS_HOST}:{server.port}/mcp",
                auth_type="oauth",
            )
            response = post_view(
                views_connectors.MCPConnectorAuthenticateView, user, record
            )
        reached = list(target.requests)

    assert reached == []
    assert response.status_code == 502
    assert response.data["detail"] == "OAuth setup failed"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_connector_that_answers_with_a_redirect_gets_the_fixed_text(
    status, self_hosted, names, connects
):
    with LoopbackServer(tool_answer) as target:
        location = f"{target.base}/mcp"
        moved = LoopbackServer(lambda request: (status, {"Location": location}, b""))
        with moved as server:
            names.add(TOOLS_HOST, "127.0.0.1")
            result = MCPConnectorProxy().execute_tool_sync(
                connector(f"http://{TOOLS_HOST}:{server.port}/mcp"), "lookup", {}
            )
        reached = list(target.requests)

    assert reached == []
    assert result == TOOL_FAILED


# The sign-in link is built for an https:// address with a host; a self-hosted
# install also accepts http://.


@pytest.mark.django_db
@pytest.mark.parametrize(
    "address",
    [
        pytest.param(f"http://{TOOLS_HOST}/authorize", id="plain-http"),
        pytest.param(f"ftp://{TOOLS_HOST}/authorize", id="not-a-web-address"),
        pytest.param("https:///authorize", id="no-host"),
        pytest.param(f"{TOOLS_HOST}/authorize", id="no-scheme"),
    ],
)
def test_on_future_agi_cloud_a_sign_in_link_is_built_only_for_an_https_address(
    address, hosted, names, connects, unchecked, user, workspace
):
    record = saved_connector(
        user,
        workspace,
        f"https://{TOOLS_HOST}/mcp",
        **oauth_fields(
            f"https://{TOOLS_HOST}/token",
            oauth_server_metadata={
                "authorization_endpoint": address,
                "token_endpoint": f"https://{TOOLS_HOST}/token",
            },
        ),
    )

    with patch.object(oauth_client, "register_client") as register:
        response = post_view(
            views_connectors.MCPConnectorAuthenticateView, user, record
        )

    assert response.status_code == 502
    assert response.data["detail"] == "OAuth setup failed"
    assert "authorization_url" not in response.data
    assert not register.called
    assert unchecked == []
    assert connects == []


@pytest.mark.django_db
def test_a_self_hosted_install_builds_a_sign_in_link_for_an_http_address(
    self_hosted, names, connects, user, workspace
):
    address = f"http://{TOOLS_HOST}/authorize"
    record = saved_connector(
        user,
        workspace,
        f"https://{TOOLS_HOST}/mcp",
        **oauth_fields(
            f"https://{TOOLS_HOST}/token",
            oauth_server_metadata={
                "authorization_endpoint": address,
                "token_endpoint": f"https://{TOOLS_HOST}/token",
            },
        ),
    )

    response = post_view(views_connectors.MCPConnectorAuthenticateView, user, record)

    assert response.status_code == 200
    assert response.data["authorization_url"].startswith(f"{address}?")
    assert connects == []


# Sessions are kept per connector.


def test_two_connectors_at_the_same_address_do_not_share_a_session(
    self_hosted, names, connects
):
    answer = SessionAnswer()
    with connector_server(names, answer) as (server, base):
        first, second = connector(f"{base}/mcp"), connector(f"{base}/mcp")
        proxy = MCPConnectorProxy()
        first_result = proxy.execute_tool_sync(first, "lookup", {})
        seen = len(server.requests)
        second_result = proxy.execute_tool_sync(second, "lookup", {})
        sent = {item.headers.get("mcp-session-id") for item in server.requests[seen:]}

    assert first_result == TOOL_ANSWERED
    assert second_result == TOOL_ANSWERED
    assert "session-1" not in sent
    assert answer.sessions == ["session-1", "session-2"]


# Kept as they are: each of these holds before and after the change.


@pytest.mark.parametrize(
    ("auth_type", "header", "expected"),
    [
        pytest.param("api_key", "X-Api-Key", "{value}", id="api-key-header"),
        pytest.param("bearer", "Authorization", "Bearer {value}", id="bearer-header"),
    ],
)
def test_a_connector_is_reached_with_its_key_or_bearer_header(
    auth_type, header, expected, self_hosted, names, connects
):
    value = "key-" + secrets.token_hex(8)
    with connector_server(names) as (server, base):
        record = connector(
            f"{base}/mcp",
            auth_type=auth_type,
            auth_header_name=header,
            auth_header_value=encrypt_falcon_token(value),
        )
        result = MCPConnectorProxy().execute_tool_sync(record, "lookup", {})
        sent = server.requests[0].headers

    assert result == TOOL_ANSWERED
    check(sent.get(header.lower()) == expected.format(value=value), "header not sent")
    assert sent["host"] == f"{TOOLS_HOST}:{server.port}"


def test_a_connector_that_needs_a_session_is_initialized_once_and_reuses_it(
    self_hosted, names, connects
):
    answer = SessionAnswer()
    with connector_server(names, answer) as (server, base):
        record = connector(f"{base}/mcp")
        proxy = MCPConnectorProxy()
        results = [proxy.execute_tool_sync(record, "lookup", {}) for _ in range(2)]
        methods = [json.loads(item.body).get("method") for item in server.requests]

    assert results == [TOOL_ANSWERED, TOOL_ANSWERED]
    assert answer.sessions == ["session-1"]
    assert methods == [
        "tools/call",
        "initialize",
        "notifications/initialized",
        "tools/call",
        "tools/call",
    ]


def test_a_server_error_is_tried_three_times(self_hosted, names, connects):
    unavailable = LoopbackServer(lambda request: (503, JSON_HEADERS, b"{}"))
    with unavailable as server:
        names.add(TOOLS_HOST, "127.0.0.1")
        result = MCPConnectorProxy().execute_tool_sync(
            connector(f"http://{TOOLS_HOST}:{server.port}/mcp"), "lookup", {}
        )
        attempts = len(server.requests)

    assert result == TOOL_FAILED
    assert attempts == 3


@pytest.mark.django_db
def test_an_unreadable_credential_answers_auth_required_with_no_connection(
    self_hosted, names, connects, unchecked, user, workspace
):
    record = saved_connector(
        user,
        workspace,
        f"https://{TOOLS_HOST}/mcp",
        auth_type="bearer",
        auth_header_value="enc::invalid-test-only",
    )

    response = post_view(views_connectors.MCPConnectorTestView, user, record)

    assert response.status_code == 409
    assert response.data["code"] == "CONNECTOR_AUTH_REQUIRED"
    assert connects == []
    assert unchecked == []


# Each path reaches an allowed connector through the address check's client,
# at the address the check allowed.


def test_a_tool_call_goes_through_the_address_check_to_the_checked_address(
    public_only_self_hosted, names, connects, unchecked, checked_calls
):
    with connector_server(names) as (server, base):
        url = f"{base}/mcp"
        result = MCPConnectorProxy().execute_tool_sync(connector(url), "lookup", {})

    assert unchecked == []
    assert connects == ["127.0.0.1"]
    assert checked_calls == [("Client.post", url)]
    assert result == TOOL_ANSWERED


def test_an_async_tool_call_goes_through_the_address_check_to_the_checked_address(
    public_only_self_hosted, names, connects, unchecked, checked_calls
):
    with connector_server(names) as (server, base):
        url = f"{base}/mcp"
        result = asyncio.run(
            MCPConnectorProxy().execute_tool(connector(url), "lookup", {})
        )

    assert unchecked == []
    assert connects == ["127.0.0.1"]
    assert checked_calls == [("AsyncClient.post", url)]
    assert result == TOOL_ANSWERED


def test_discovering_tools_goes_through_the_address_check(
    public_only_self_hosted, names, connects, unchecked, checked_calls, monkeypatch
):
    proxy, saved = proxy_without_saves(monkeypatch)
    with connector_server(names) as (server, base):
        url = f"{base}/mcp"
        result = proxy.discover_tools_sync(connector(url))

    assert unchecked == []
    assert connects == ["127.0.0.1"]
    assert checked_calls == [("Client.post", url)]
    assert result == {"success": True, "tools": [TOOL], "count": 1}
    assert saved.discoveries == [[TOOL]]


def test_a_connection_test_goes_through_the_address_check(
    public_only_self_hosted, names, connects, unchecked, checked_calls
):
    with connector_server(names) as (server, base):
        url = f"{base}/mcp"
        result = MCPConnectorProxy().test_connection_sync(connector(url))

    assert unchecked == []
    assert connects == ["127.0.0.1"]
    assert checked_calls == [("Client.post", url)]
    assert result == {"success": True, "status_code": 200}


@pytest.mark.django_db
def test_oauth_discovery_and_registration_go_through_the_address_check(
    public_only_self_hosted, names, connects, unchecked, checked_calls, user, workspace
):
    with connector_server(names, SignInAnswer()) as (server, base):
        record = saved_connector(user, workspace, f"{base}/mcp", auth_type="oauth")
        response = post_view(
            views_connectors.MCPConnectorAuthenticateView, user, record
        )

    assert unchecked == []
    assert connects == ["127.0.0.1", "127.0.0.1"]
    assert checked_calls == [
        ("Client.get", f"{base}/.well-known/oauth-authorization-server"),
        ("Client.post", f"{base}/register"),
    ]
    assert response.status_code == 200
    assert response.data["authorization_url"].startswith(
        f"https://{TOOLS_HOST}/authorize?"
    )


@pytest.mark.django_db
def test_a_sign_in_goes_through_the_address_check(
    public_only_self_hosted, names, connects, unchecked, checked_calls, user, workspace
):
    state = secrets.token_urlsafe(16)
    with connector_server(names, SignInAnswer()) as (server, base):
        record = saved_connector(
            user,
            workspace,
            f"{base}/mcp",
            **oauth_fields(
                f"{base}/token",
                oauth_state=state,
                oauth_code_verifier=secrets.token_urlsafe(48),
            ),
        )
        response = callback(record, state)

    assert unchecked == []
    assert connects == ["127.0.0.1", "127.0.0.1"]
    assert checked_calls == [
        ("Client.post", f"{base}/token"),
        ("Client.post", f"{base}/mcp"),
    ]
    assert response.status_code == 200
    assert b"Connected successfully" in response.content


@pytest.mark.django_db
def test_renewing_a_sign_in_goes_through_the_address_check(
    public_only_self_hosted, names, connects, unchecked, checked_calls, user, workspace
):
    with connector_server(names, SignInAnswer()) as (server, base):
        record = saved_connector(
            user,
            workspace,
            f"{base}/mcp",
            **oauth_fields(f"{base}/token", **expired_sign_in()),
        )
        result = MCPConnectorProxy().execute_tool_sync(record, "lookup", {})

    assert unchecked == []
    assert connects == ["127.0.0.1", "127.0.0.1"]
    assert checked_calls == [
        ("Client.post", f"{base}/token"),
        ("Client.post", f"{base}/mcp"),
    ]
    assert result == TOOL_ANSWERED
    tool_request = server.requests[-1].headers
    check(
        tool_request.get("authorization") == f"Bearer {FRESH_ACCESS}",
        "the renewed sign-in was not used",
    )
