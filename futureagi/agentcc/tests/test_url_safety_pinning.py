"""ALERT-ACCESS-1 (TEN-1028-04): build_ssrf_safe_session connects to the
address it checked.

Model discovery (open to Members), the webhook test and webhook delivery send
through this session. At the base its adapter checked the host's addresses and
then let urllib3 resolve the name again to connect, so a DNS answer that
changed between the two lookups (a public address for the check, loopback for
the connect) reached the second address.

Synthetic only: DNS answers are stubbed, every socket connect is refused
in-process and no request leaves the test.
"""

import io
import ipaddress
import socket
from unittest.mock import patch

import pytest
import requests
import urllib3
from urllib3.connectionpool import HTTPConnectionPool

from agentcc.services.url_safety import build_ssrf_safe_session

BLOCKED = "Connection to private address blocked"
PUBLIC = "93.184.216.34"
SECOND_PUBLIC = "93.184.216.35"


def _answer(addr, port):
    family = socket.AF_INET6 if ":" in addr else socket.AF_INET
    number = int(port) if isinstance(port, int) or str(port or "").isdigit() else 0
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (addr, number))


def _dns(answers):
    """A getaddrinfo stub: IP literals answer themselves; a name in
    ``answers`` answers its addresses in order, one list per lookup (the last
    list repeats); any other name does not resolve. Returns (stub, lookups)."""
    lookups = []

    def getaddrinfo(host, port=None, *args, **kwargs):
        lookups.append(host)
        try:
            return [_answer(str(ipaddress.ip_address(host)), port)]
        except ValueError:
            pass
        if host not in answers:
            raise socket.gaierror(socket.EAI_NONAME, "no such host")
        rounds = answers[host]
        addrs = rounds[min(lookups.count(host), len(rounds)) - 1]
        return [_answer(addr, port) for addr in addrs]

    return getaddrinfo, lookups


def _ok(body=b'{"data": []}', status=200, headers=None):
    return urllib3.HTTPResponse(
        body=io.BytesIO(body),
        status=status,
        headers={"Content-Type": "application/json", **(headers or {})},
        preload_content=False,
    )


@pytest.fixture
def refuse_connects():
    """Every socket connect is recorded and refused: nothing leaves the test."""
    connects = []

    def connect(sock, address):
        connects.append(address)
        raise OSError("synthetic: no network in this test")

    with patch.object(socket.socket, "connect", connect):
        yield connects


def test_the_connect_goes_to_the_address_that_was_checked(refuse_connects):
    # The first answer is public and passes the check; every later answer is
    # loopback. The connect must use the first, never ask again.
    getaddrinfo, lookups = _dns({"api.rebind.test": [[PUBLIC], ["127.0.0.1"]]})
    session = build_ssrf_safe_session(BLOCKED)

    with patch("socket.getaddrinfo", getaddrinfo):
        with pytest.raises(requests.exceptions.ConnectionError):
            session.get("https://api.rebind.test/v1/models", timeout=5)

    assert refuse_connects == [(PUBLIC, 443)]
    assert lookups.count("api.rebind.test") == 1


@pytest.mark.parametrize(
    ("url", "scheme", "port", "host_header", "tls_name"),
    [
        (
            "https://api.pinned.test/v1/models",
            "https",
            443,
            "api.pinned.test",
            "api.pinned.test",
        ),
        (
            "https://api.pinned.test:8443/v1/models",
            "https",
            8443,
            "api.pinned.test:8443",
            "api.pinned.test",
        ),
        (
            "http://api.pinned.test:8080/v1/models",
            "http",
            8080,
            "api.pinned.test:8080",
            None,
        ),
    ],
)
def test_the_pool_dials_the_address_and_speaks_to_the_name(
    url, scheme, port, host_header, tls_name
):
    getaddrinfo, _ = _dns({"api.pinned.test": [[PUBLIC]]})
    seen = []

    def urlopen(pool, method, url, body=None, headers=None, **kwargs):
        seen.append(
            {
                "host": pool.host,
                "port": pool.port,
                "scheme": pool.scheme,
                "sni": pool.conn_kw.get("server_hostname"),
                "assert_hostname": getattr(pool, "assert_hostname", None),
                "proxy": pool.proxy,
                "headers": dict(headers or {}),
                "url": url,
            }
        )
        return _ok()

    session = build_ssrf_safe_session(BLOCKED)
    with (
        patch("socket.getaddrinfo", getaddrinfo),
        patch.object(HTTPConnectionPool, "urlopen", autospec=True, side_effect=urlopen),
    ):
        response = session.get(url, timeout=5)

    assert response.status_code == 200
    (call,) = seen
    assert call["host"] == PUBLIC
    assert (call["scheme"], call["port"]) == (scheme, port)
    assert call["url"] == "/v1/models"
    assert call["headers"]["Host"] == host_header
    assert call["proxy"] is None
    if tls_name:
        # The certificate is checked for the name, and the name is the SNI.
        assert call["sni"] == tls_name
        assert call["assert_hostname"] == tls_name


def test_each_redirect_hop_is_pinned_and_named_for_itself():
    getaddrinfo, _ = _dns(
        {"first.pinned.test": [[PUBLIC]], "second.pinned.test": [[SECOND_PUBLIC]]}
    )
    seen = []

    def urlopen(pool, method, url, body=None, headers=None, **kwargs):
        seen.append((pool.host, dict(headers or {}).get("Host"), url))
        if len(seen) == 1:
            return _ok(b"", 302, {"Location": "https://second.pinned.test/next"})
        return _ok()

    session = build_ssrf_safe_session(BLOCKED)
    with (
        patch("socket.getaddrinfo", getaddrinfo),
        patch.object(HTTPConnectionPool, "urlopen", autospec=True, side_effect=urlopen),
    ):
        response = session.get("https://first.pinned.test/start", timeout=5)

    assert response.status_code == 200
    assert seen == [
        (PUBLIC, "first.pinned.test", "/start"),
        (SECOND_PUBLIC, "second.pinned.test", "/next"),
    ]


def test_a_redirect_to_an_internal_address_is_refused_before_it_is_dialed():
    getaddrinfo, _ = _dns(
        {"first.pinned.test": [[PUBLIC]], "inner.pinned.test": [["10.0.0.5"]]}
    )
    seen = []

    def urlopen(pool, method, url, body=None, headers=None, **kwargs):
        seen.append(pool.host)
        return _ok(b"", 302, {"Location": "http://inner.pinned.test/admin"})

    session = build_ssrf_safe_session(BLOCKED)
    with (
        patch("socket.getaddrinfo", getaddrinfo),
        patch.object(HTTPConnectionPool, "urlopen", autospec=True, side_effect=urlopen),
    ):
        with pytest.raises(ConnectionError, match=BLOCKED):
            session.get("https://first.pinned.test/start", timeout=5)

    assert seen == [PUBLIC]


def test_environment_proxies_are_not_used(monkeypatch):
    # A proxy would resolve the name itself, after the check.
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, "http://proxy.pinned.test:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    getaddrinfo, _ = _dns(
        {"api.pinned.test": [[PUBLIC]], "proxy.pinned.test": [[SECOND_PUBLIC]]}
    )
    seen = []

    def urlopen(pool, method, url, body=None, headers=None, **kwargs):
        seen.append((pool.host, pool.proxy))
        return _ok()

    session = build_ssrf_safe_session(BLOCKED)
    with (
        patch("socket.getaddrinfo", getaddrinfo),
        patch.object(HTTPConnectionPool, "urlopen", autospec=True, side_effect=urlopen),
    ):
        session.get("https://api.pinned.test/v1/models", timeout=5)

    assert seen == [(PUBLIC, None)]


def test_an_internal_answer_is_refused_before_any_connect(refuse_connects):
    getaddrinfo, _ = _dns({"api.inner.test": [["169.254.169.254"]]})
    session = build_ssrf_safe_session(BLOCKED)

    with patch("socket.getaddrinfo", getaddrinfo):
        with pytest.raises(ConnectionError, match=BLOCKED):
            session.get("http://api.inner.test/latest/meta-data", timeout=5)

    assert refuse_connects == []
