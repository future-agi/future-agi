import ipaddress
import socket
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import httpx
import pytest


def pytest_configure(config):
    # pytest's prepend mode can bind `conftest` to this scoped module while
    # shared permission helpers still import the root client by that name.
    # Re-export the registered class itself: do not reload root conftest or
    # duplicate its client registry, workspace injection, or teardown behavior.
    root_file = Path(__file__).resolve().parents[2] / "conftest.py"
    root = next(
        (
            plugin
            for plugin in config.pluginmanager.get_plugins()
            if getattr(plugin, "__file__", None)
            and Path(plugin.__file__).resolve() == root_file
        ),
        None,
    )
    if root is None:
        raise RuntimeError("root test conftest must be registered before agentcc")
    globals()["WorkspaceAwareAPIClient"] = root.WorkspaceAwareAPIClient


@pytest.fixture(autouse=True)
def ready_cap_ledger(monkeypatch):
    """Existing cap lifecycle controls use a ready ledger without any network."""
    from agentcc.services import cap_ledger_status

    reader = cap_ledger_status.get_ledger_status
    monkeypatch.setattr(
        cap_ledger_status,
        "get_ledger_status",
        lambda org_id, *, fresh=False, require_autocommit=False: "ready",
    )
    return reader


@pytest.fixture(autouse=True)
def offline_validation_dns(monkeypatch):
    """DNS classification fixtures belong at the validator, not the socket guard.

    These unit tests use IANA example names as public URL fixtures and .invalid
    names as unresolved fixtures. Neither needs real DNS. Keep the process-wide
    socket module (and its fail-closed transport/resolver guard) untouched; an
    accidental delivery to the fixture's public address must still be refused.
    Tests that need other answers override this seam with provider_dns below.
    """
    from agentcc.services import url_safety
    from tfc.utils import ssrf_guard

    original = socket.getaddrinfo
    answered = MagicMock()

    def resolve(host, port=None, *args, **kwargs):
        # Pinning/rebinding tests provide their own explicit resolver sequence.
        if socket.getaddrinfo is not original:
            return socket.getaddrinfo(host, port, *args, **kwargs)
        if host in ("localhost", "127.0.0.1", "::1"):
            return original(host, port, *args, **kwargs)
        answered(host, port)
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            name = str(host).lower().rstrip(".")
            if name != "example.com" and not name.endswith(".example.com"):
                raise socket.gaierror(
                    socket.EAI_NONAME, "offline validation fixture"
                ) from None
            address = ipaddress.ip_address("93.184.216.34")
        return [
            (
                socket.AF_INET6 if address.version == 6 else socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (str(address), port or 0),
            )
        ]

    for module in (url_safety, ssrf_guard):
        monkeypatch.setattr(
            module,
            "socket",
            SimpleNamespace(
                **{
                    name: getattr(socket, name)
                    for name in dir(socket)
                    if name != "getaddrinfo"
                },
                getaddrinfo=resolve,
            ),
        )
    return answered


@pytest.fixture
def session_auth_client(user, workspace):
    """Use browser provenance for customer key creation, never forced auth."""
    from accounts.tests.personal_keys import session_client_for
    from tfc.middleware.workspace_context import clear_workspace_context

    client = session_client_for(user, user.organization, workspace)
    try:
        yield client
    finally:
        clear_workspace_context()


@pytest.fixture
def provider_dns(offline_validation_dns):
    """Stub the DNS lookups url_safety makes: ``provider_dns(answers)``.

    IP literals resolve to themselves, the names in ``answers`` to their
    addresses, and ``*.invalid`` fails. Any other name fails as well, unless
    ``passthrough`` sends it to the real resolver, for request tests that
    also reach localhost and the test services. Returns a mock of the
    lookups the stub answered, which leaves out the ones passed on.
    """
    from agentcc.services import url_safety

    real_getaddrinfo = url_safety.socket.getaddrinfo
    answered = MagicMock()

    def install(answers, *, passthrough=False):
        def fake_getaddrinfo(host, *args, **kwargs):
            try:
                addrs = [str(ipaddress.ip_address(host))]
            except ValueError:
                known = isinstance(host, str) and host in answers
                invalid = isinstance(host, str) and host.endswith(".invalid")
                if passthrough and not known and not invalid:
                    return real_getaddrinfo(host, *args, **kwargs)
                if not known:
                    answered(host, *args, **kwargs)
                    raise socket.gaierror(socket.EAI_NONAME, "no such host") from None
                addrs = answers[host]
            answered(host, *args, **kwargs)
            return [
                (
                    socket.AF_INET6 if ":" in addr else socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (addr, 0),
                )
                for addr in addrs
            ]

        for target in (
            "agentcc.services.url_safety.socket.getaddrinfo",
            "tfc.utils.ssrf_guard.socket.getaddrinfo",
        ):
            stack.enter_context(patch(target, fake_getaddrinfo))
        return answered

    with ExitStack() as stack:
        yield install


def _address(url):
    """(host, port) of ``url``, with the scheme's default port filled in."""
    parts = urlsplit(str(url))
    return parts.hostname, parts.port or {"http": 80, "https": 443}.get(parts.scheme)


@pytest.fixture
def gateway_unreachable():
    """The gateway as the test stack has it: not running.

    Every httpx request to the gateway's configured address
    (``gateway_client.AGENTCC_GATEWAY_INTERNAL_URL``, which every gateway
    client calls) fails with ``httpx.ConnectError``, as it does while the
    gateway is down, before any DNS lookup or socket; a request anywhere else
    goes through unchanged. Yields the (method, path) of each refused
    request, so a test asserts that its path asked the gateway.
    """
    from agentcc.services import gateway_client

    asked = []
    send = httpx.HTTPTransport.handle_request

    def handle_request(transport, request):
        url = request.url
        here = (url.host, url.port or {"http": 80, "https": 443}.get(url.scheme))
        if here == _address(gateway_client.AGENTCC_GATEWAY_INTERNAL_URL):
            asked.append((request.method, url.path))
            raise httpx.ConnectError(
                "the gateway is not running in tests", request=request
            )
        return send(transport, request)

    with patch.object(httpx.HTTPTransport, "handle_request", handle_request):
        yield asked


@pytest.fixture(autouse=True)
def mcp_subfeatures_on_for_existing_scenarios(settings, request):
    """Existing scenario suites exercise enabled features; gate tests own their flags."""
    if request.module.__name__.endswith("test_mcp_subfeature_flags"):
        return
    settings.AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED = True
    settings.AGENTCC_MCP_CLIENT_OAUTH_ENABLED = True
    settings.AGENTCC_MCP_K2_APPS_ENABLED = True


@pytest.fixture(autouse=True)
def mcp_tool_call_scenarios_on(settings, request):
    """Enabled call/export scenarios opt in; default/flag tests own their bits."""
    if request.module.__name__.rsplit(".", 1)[-1] in {
        "test_mcp_tool_calls_api",
        "test_mcp_tool_calls_explorer",
        "test_siem_privacy",
        "test_guardrail_section_emptied",
    }:
        settings.AGENTCC_MCP_TOOL_CALL_EXPLORER_ENABLED = True
        settings.AGENTCC_MCP_TOOL_CALL_DOWNLOAD_ENABLED = True
