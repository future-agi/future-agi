"""The one SSRF-guarded fetcher for every Django-side MCP fetch (M25x,
decision 39): the upstream's OAuth discovery, token and registration calls,
and the CIMD fetch M24x adds.

Each test is one property. No test reaches a network: DNS is answered by
``FakeDNS`` and every connection by ``FakePools``, which records the address
the fetcher pinned and the host name it checks the certificate against.
"""

from unittest.mock import patch

import pytest

from agentcc.services import mcp_safe_fetch as safe

PUBLIC = "93.184.215.14"
PUBLIC_2 = "93.184.215.15"


class FakeResponse:
    def __init__(self, status=200, body=b"{}", headers=None):
        self.status = status
        self._body = body
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.released = False

    def stream(self, amount):
        for i in range(0, len(self._body), amount):
            yield self._body[i : i + amount]

    def release_conn(self):
        self.released = True


class FakePools:
    """Stands in for the pinned connection pool. ``answers`` maps a URL path
    to the FakeResponse it returns; ``opened`` records every connection as
    (scheme, pinned ip, port, host checked); ``requests`` every request."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.opened = []
        self.requests = []

    def open(self, scheme, ip, host, port, timeout):
        self.opened.append((scheme, ip, port, host))
        pools = self

        class _Pool:
            def request(self, method, path, body=None, headers=None, **kwargs):
                pools.requests.append(
                    {"method": method, "path": path, "body": body, "headers": headers}
                )
                answer = pools.answers.get(path, FakeResponse())
                return answer() if callable(answer) else answer

            def close(self):
                pass

        return _Pool()


class FakeDNS:
    def __init__(self, table):
        self.table = table
        self.lookups = []

    def __call__(self, host, port, *args, **kwargs):
        self.lookups.append(host)
        addresses = self.table.get(host)
        if addresses is None:
            raise safe.socket.gaierror("no such host")
        return [(None, None, None, "", (ip, 0)) for ip in addresses]


@pytest.fixture
def net(monkeypatch):
    """DNS and connections, both faked; the private allow list empty."""
    monkeypatch.delenv(safe.PRIVATE_ALLOW_ENV, raising=False)
    dns = FakeDNS(
        {
            "as.example.com": [PUBLIC],
            "other.example.com": [PUBLIC_2],
            "intranet.example.com": ["10.1.2.3"],
            "mixed.example.com": [PUBLIC, "10.1.2.3"],
            "loop.example.com": ["127.0.0.1"],
            "meta.example.com": ["169.254.169.254"],
            "mock.internal": ["10.9.0.7"],
        }
    )
    pools = FakePools()
    with (
        patch.object(safe.socket, "getaddrinfo", dns),
        patch.object(safe, "_open_pool", pools.open),
    ):
        yield dns, pools


def _refused(url, reason, **kwargs):
    with pytest.raises(safe.EgressRefused) as caught:
        safe.fetch(url, **kwargs)
    assert caught.value.reason == reason, caught.value.reason
    return caught.value


# --- Property: never an internal or metadata address -------------------------


@pytest.mark.parametrize(
    ("ip", "reason"),
    [
        ("127.0.0.1", "not_allowed"),
        ("::1", "not_allowed"),
        ("10.1.2.3", "not_allowed"),
        ("192.168.1.10", "not_allowed"),
        ("172.16.0.5", "not_allowed"),
        ("100.64.0.1", "not_allowed"),
        ("fd12::1", "not_allowed"),
        ("169.254.169.254", "forbidden"),
        ("fe80::1", "forbidden"),
        ("0.0.0.0", "forbidden"),
        ("224.0.0.1", "forbidden"),
        ("100.100.100.200", "forbidden"),
        ("fd00:ec2::254", "forbidden"),
        ("168.63.129.16", "forbidden"),
        ("::ffff:127.0.0.1", "not_allowed"),
        ("::ffff:169.254.169.254", "forbidden"),
        ("64:ff9b::a9fe:a9fe", "forbidden"),
        ("64:ff9b::a01:203", "not_allowed"),
    ],
)
def test_safe_fetch_refuses_internal_addresses(net, ip, reason):
    dns, pools = net
    dns.table["target.example.com"] = [ip]

    _refused("https://target.example.com/.well-known/x", reason)

    assert pools.opened == []


def test_safe_fetch_refuses_private_metadata_and_redirect_to_private(net):
    dns, pools = net

    _refused("https://intranet.example.com/x", "not_allowed")
    _refused("https://meta.example.com/x", "forbidden")
    # A metadata name is refused before any lookup.
    _refused("https://metadata.google.internal/computeMetadata/v1/", "forbidden")
    assert "metadata.google.internal" not in dns.lookups
    # A public host that redirects to a private one: the hop is checked again.
    pools.answers["/start"] = FakeResponse(
        302, b"", {"Location": "https://intranet.example.com/x"}
    )
    _refused("https://as.example.com/start", "not_allowed", allow_redirects=True)

    assert [opened[1] for opened in pools.opened] == [PUBLIC]


def test_safe_fetch_checks_every_address_a_name_resolves_to(net):
    _, pools = net

    _refused("https://mixed.example.com/x", "not_allowed")

    assert pools.opened == []


def test_safe_fetch_refuses_a_name_that_does_not_resolve(net):
    _refused("https://nowhere.example.com/x", "unresolvable")


# --- Property: the operator list opens private addresses, never metadata -----


def test_safe_fetch_private_allow_list_never_metadata(net, monkeypatch):
    _, pools = net
    monkeypatch.setenv(
        safe.PRIVATE_ALLOW_ENV,
        "10.0.0.0/8, 169.254.0.0/16, 169.254.169.254, metadata.google.internal, "
        "meta.example.com, loop.example.com:8443",
    )

    assert safe.fetch("https://intranet.example.com/x").status == 200
    _refused("https://meta.example.com/x", "forbidden")
    _refused("https://metadata.google.internal/x", "forbidden")
    # A host-name entry with a port opens only that port.
    _refused("https://loop.example.com/x", "not_allowed")
    assert safe.fetch("https://loop.example.com:8443/x").status == 200

    assert [opened[1] for opened in pools.opened] == ["10.1.2.3", "127.0.0.1"]


def test_safe_fetch_host_entry_matches_the_name_dialled_not_its_address(
    net, monkeypatch
):
    dns, _ = net
    dns.table["alias.example.com"] = ["10.9.0.7"]
    monkeypatch.setenv(safe.PRIVATE_ALLOW_ENV, "mock.internal")

    assert safe.fetch("https://mock.internal/x").status == 200
    _refused("https://alias.example.com/x", "not_allowed")


def test_safe_fetch_ignores_an_entry_it_cannot_parse(net, monkeypatch):
    monkeypatch.setenv(safe.PRIVATE_ALLOW_ENV, "10.0.0.0/33, not a host!, :99999")

    _refused("https://intranet.example.com/x", "not_allowed")


# --- Property: HTTPS, except to an address the operator listed ---------------


def test_safe_fetch_requires_https_to_public_hosts(net):
    _, pools = net

    _refused("http://as.example.com/x", "https_required")
    _refused("ftp://as.example.com/x", "invalid_url")
    _refused("https:///nohost", "invalid_url")

    assert pools.opened == []


def test_safe_fetch_plain_http_only_to_a_listed_address(net, monkeypatch):
    _, pools = net
    monkeypatch.setenv(safe.PRIVATE_ALLOW_ENV, "mock.internal:8080")

    assert safe.fetch("http://mock.internal:8080/x").status == 200
    _refused("http://mock.internal:9090/x", "not_allowed")

    assert pools.opened == [("http", "10.9.0.7", 8080, "mock.internal")]


# --- Property: the checked address is the one connected to -------------------


def test_safe_fetch_pins_the_checked_address(net):
    dns, pools = net

    safe.fetch("https://as.example.com/.well-known/oauth-authorization-server")

    assert dns.lookups == ["as.example.com"]
    assert pools.opened == [("https", PUBLIC, 443, "as.example.com")]
    assert pools.requests[0]["headers"]["Host"] == "as.example.com"


# --- Property: redirects are refused unless asked for, and re-checked --------


def test_safe_fetch_does_not_follow_a_redirect_by_default(net):
    _, pools = net
    pools.answers["/start"] = FakeResponse(
        302, b"", {"Location": "https://other.example.com/y"}
    )

    response = safe.fetch("https://as.example.com/start")

    assert response.status == 302
    assert [opened[3] for opened in pools.opened] == ["as.example.com"]


def test_safe_fetch_follows_a_redirect_when_asked_and_drops_credentials(net):
    _, pools = net
    pools.answers["/start"] = FakeResponse(
        302, b"", {"Location": "https://other.example.com/y"}
    )

    response = safe.fetch(
        "https://as.example.com/start",
        headers={"Authorization": "Bearer test-not-a-real-token", "Accept": "x"},
        allow_redirects=True,
    )

    assert response.status == 200
    assert response.url == "https://other.example.com/y"
    first, second = pools.requests
    assert first["headers"]["Authorization"] == "Bearer test-not-a-real-token"
    assert "Authorization" not in second["headers"]
    assert second["headers"]["Accept"] == "x"


def test_safe_fetch_never_follows_a_redirect_for_a_post(net):
    with pytest.raises(ValueError):
        safe.fetch(
            "https://as.example.com/token", method="POST", data={}, allow_redirects=True
        )


def test_safe_fetch_stops_after_the_redirect_limit(net):
    _, pools = net
    pools.answers["/loop"] = FakeResponse(
        302, b"", {"Location": "https://as.example.com/loop"}
    )

    _refused("https://as.example.com/loop", "redirect", allow_redirects=True)

    assert len(pools.requests) == safe.MAX_REDIRECTS + 1


# --- Property: size and time are bounded --------------------------------------


def test_safe_fetch_refuses_a_body_over_the_limit(net):
    _, pools = net
    pools.answers["/big"] = FakeResponse(200, b"x" * 2048)

    _refused("https://as.example.com/big", "too_large", max_bytes=1024)
    assert safe.fetch("https://as.example.com/big", max_bytes=4096).content == (
        b"x" * 2048
    )


def test_safe_fetch_refuses_a_declared_length_over_the_limit(net):
    _, pools = net
    pools.answers["/big"] = FakeResponse(200, b"", {"Content-Length": "999999"})

    _refused("https://as.example.com/big", "too_large", max_bytes=1024)


def test_safe_fetch_reports_a_transport_failure_without_the_address(net):
    _, pools = net

    def boom():
        raise safe.urllib3.exceptions.ConnectTimeoutError(None, f"{PUBLIC} timed out")

    pools.answers["/slow"] = boom

    refused = _refused("https://as.example.com/slow", "transport")
    assert PUBLIC not in str(refused)


# --- The request it sends ------------------------------------------------------


def test_safe_fetch_posts_a_form_and_reads_json(net):
    _, pools = net
    pools.answers["/token"] = FakeResponse(200, b'{"access_token": "test-at"}')

    response = safe.fetch(
        "https://as.example.com/token",
        method="POST",
        data={"grant_type": "authorization_code", "code": "c 1"},
    )

    assert response.json() == {"access_token": "test-at"}
    sent = pools.requests[0]
    assert sent["method"] == "POST"
    assert sent["body"] == b"grant_type=authorization_code&code=c+1"
    assert sent["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


def test_safe_fetch_posts_json(net):
    _, pools = net

    safe.fetch(
        "https://as.example.com/register",
        method="POST",
        json_body={"redirect_uris": ["https://x/cb"]},
    )

    sent = pools.requests[0]
    assert sent["body"] == b'{"redirect_uris": ["https://x/cb"]}'
    assert sent["headers"]["Content-Type"] == "application/json"


def test_safe_fetch_json_refuses_a_body_that_is_not_json(net):
    _, pools = net
    pools.answers["/x"] = FakeResponse(200, b"<html>")

    with pytest.raises(ValueError):
        safe.fetch("https://as.example.com/x").json()
