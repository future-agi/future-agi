"""HTTPS only, no redirect, public addresses only, capped, and errors that
never carry a URL."""

from __future__ import annotations

import socket
import time

import pytest
from conftest import (
    ELSEWHERE,
    GMAIL,
    GMAIL_BEARER,
    PROFILE_PATH,
    argv,
    checks,
    entry,
    env_with,
    gmail_host,
    provider_of,
    report_of,
    run,
)
from connector_smoke import transport

ORIGINAL_DIAL_PUBLIC = transport._dial_public  # conftest replaces it in every test


@pytest.mark.parametrize(
    "url",
    [
        "http://mcp.example.com/mcp",
        "https://user@mcp.example.com/mcp",
        "https://user:pw@mcp.example.com/mcp",
        "https:///mcp",
        "ftp://mcp.example.com/",
        "https://mcp.example.com:0/",
        "https://mcp.example.com:99999/",
    ],
)
def test_only_https_without_user_info(url):
    with pytest.raises(transport.TransportError) as err:
        transport.check_url(url)
    assert err.value.code == "not_https"


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.3.4",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fd00::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "224.0.0.1",
    ],
)
def test_non_public_addresses_are_never_dialled(ip, monkeypatch):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    monkeypatch.setattr(
        transport.socket,
        "getaddrinfo",
        lambda *a, **k: [(family, socket.SOCK_STREAM, 6, "", (ip, 443))],
    )

    def no_socket(*a, **k):
        raise AssertionError("a socket was opened")

    monkeypatch.setattr(transport.socket, "socket", no_socket)
    with pytest.raises(transport.TransportError) as err:
        ORIGINAL_DIAL_PUBLIC("mcp.example.com", 443, time.monotonic() + 5.0)
    assert err.value.code == "address_refused"


def test_a_public_address_is_dialled(monkeypatch):
    dialled = []

    class Sock:
        def __init__(self, *a):
            pass

        def settimeout(self, t):
            pass

        def connect(self, addr):
            dialled.append(addr)

    monkeypatch.setattr(
        transport.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
        ],
    )
    monkeypatch.setattr(transport.socket, "socket", Sock)
    ORIGINAL_DIAL_PUBLIC("mcp.example.com", 443, time.monotonic() + 5.0)
    assert dialled == [("93.184.215.14", 443)]  # the private answer was skipped


def _answers(addrs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", a) for a in addrs]


def test_each_address_gets_only_the_time_left(monkeypatch):
    """A host with four public addresses: the first refuses after 0.3 s, the
    others drop the SYN, so a connect there waits its whole timeout. Each
    address gets only what is left of the request's deadline, and once the
    deadline has passed no further address is dialled: the request ends at
    about --timeout, not four times it."""
    addrs = [(f"93.184.215.{n}", 443) for n in (14, 15, 16, 17)]
    dialled, given = [], []

    class Sock:
        def __init__(self, *a):
            self.timeout = None

        def settimeout(self, t):
            self.timeout = t

        def connect(self, addr):
            dialled.append(addr)
            given.append(self.timeout)
            if addr == addrs[0]:
                time.sleep(0.3)
                raise ConnectionRefusedError
            time.sleep(self.timeout + 0.02)  # a dropped SYN waits it all out
            raise TimeoutError

        def close(self):
            pass

    monkeypatch.setattr(transport, "_dial_public", ORIGINAL_DIAL_PUBLIC)
    monkeypatch.setattr(
        transport.socket, "getaddrinfo", lambda *a, **k: _answers(addrs)
    )
    monkeypatch.setattr(transport.socket, "socket", Sock)
    with pytest.raises(transport.TransportError) as err:
        transport.request("GET", "https://mcp.example.com/mcp", timeout=2.0)
    assert err.value.code == "timeout"
    assert dialled == addrs[:2]  # the third and fourth come after the deadline
    assert 0 < given[0] <= 2.0
    assert 0 < given[1] <= 2.0 - 0.3 + 0.01  # what was left, not a fresh 2 s


def test_no_address_is_dialled_once_the_deadline_has_passed(monkeypatch):
    """DNS that answers after the deadline: no address is dialled at all."""

    def slow_dns(*a, **k):
        time.sleep(0.4)
        return _answers([("93.184.215.14", 443)])

    def no_socket(*a, **k):
        raise AssertionError("an address was dialled after the deadline")

    monkeypatch.setattr(transport, "_dial_public", ORIGINAL_DIAL_PUBLIC)
    monkeypatch.setattr(transport.socket, "getaddrinfo", slow_dns)
    monkeypatch.setattr(transport.socket, "socket", no_socket)
    with pytest.raises(transport.TransportError) as err:
        transport.request("GET", "https://mcp.example.com/mcp", timeout=0.2)
    assert err.value.code == "timeout"


def test_errors_never_carry_the_url(tls):
    url = "https://mcp.close.com/mcp?api_key=fixture-query-secret-value"
    closed = transport.Net(ssl_context=tls[1], route=lambda h, p: ("127.0.0.1", 9))
    with pytest.raises(transport.TransportError) as err:
        transport.request("GET", url, net=closed, timeout=5)
    assert err.value.code in ("connect_error", "timeout")
    assert "fixture-query-secret-value" not in repr(err.value) + str(err.value.args)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_an_untrusted_certificate_is_refused(fake):
    import ssl

    untrusting = transport.Net(
        ssl_context=ssl.create_default_context(),
        route=lambda h, p: ("127.0.0.1", fake.port),
    )
    with pytest.raises(transport.TransportError) as err:
        transport.request(
            "GET", "https://mcp.linear.app/mcp", net=untrusting, timeout=5
        )
    assert err.value.code == "tls_error"


def test_a_redirect_is_never_followed(fake, net, route, tmp_path):
    host = gmail_host()
    host.get[PROFILE_PATH] = (302, {"Location": f"https://{ELSEWHERE}/collect"}, b"")
    fake.hosts[GMAIL] = host
    out = tmp_path / "out"
    code, _ = run(
        argv(out, "--provider", "gmail/futureagi-rest"),
        env_with({"gmail/futureagi-rest": entry("fa-ci-gmail", bearer=GMAIL_BEARER)}),
        net,
    )
    assert code == 1
    assert fake.requests(ELSEWHERE) == [] and route.refused == []
    v = checks(provider_of(report_of(out), "gmail/futureagi-rest"), "verify")[0]
    assert (v["status"], v["reason"], v["httpStatus"]) == (
        "fail",
        "redirect_refused",
        302,
    )


def test_the_byte_cap_holds(fake, net):
    from fakemcp import Host

    fake.hosts[GMAIL] = Host(
        get={
            "/big": (200, {"Content-Type": "application/octet-stream"}, b"x" * 200_000)
        }
    )
    resp = transport.request(
        "GET", f"https://{GMAIL}/big", net=net, max_bytes=100_000, timeout=10
    )
    with pytest.raises(transport.TransportError) as err:
        resp.read()
    resp.close()
    assert err.value.code == "too_large"


@pytest.mark.parametrize("read", ["read", "readline"])
def test_a_slow_drip_is_cut_at_the_deadline(fake, net, read):
    """A byte every 50 ms, no newline, for 30 s: each recv stays far inside the
    1 s socket timeout, so only the request's own deadline can stop it. It
    must end as a timeout near 1 s, never as a short read passed as a body."""
    from fakemcp import Host

    fake.hosts[GMAIL] = Host(drip={"/drip": (0.05, 600)})
    start = time.monotonic()
    resp = transport.request("GET", f"https://{GMAIL}/drip", net=net, timeout=1.0)
    try:
        with pytest.raises(transport.TransportError) as err:
            getattr(resp, read)()
    finally:
        resp.close()
    elapsed = time.monotonic() - start
    assert err.value.code == "timeout"
    assert elapsed < 8.0, elapsed  # generous: the host runs under heavy load


def test_a_request_within_its_deadline_is_untouched(fake, net):
    """The watchdog never cuts a request that ends in time, and closing one
    cancels its watchdog."""
    from fakemcp import Host

    fake.hosts[GMAIL] = Host(drip={"/drip": (0.0, 64)})
    resp = transport.request("GET", f"https://{GMAIL}/drip", net=net, timeout=5.0)
    try:
        assert resp.read() == b"x" * 64
    finally:
        resp.close()
    assert resp._dog is not None and not resp._dog.fired
    time.sleep(0.1)
    assert resp._dog._dup is None  # cancelled and released


@pytest.mark.parametrize(
    "ip, public",
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("not an ip", False),
        ("::ffff:8.8.8.8", True),
    ],
)
def test_public_address(ip, public):
    assert transport.public_address(ip) is public
