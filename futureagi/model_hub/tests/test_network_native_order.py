"""Transport hostnames must be refused before CPython's native resolver.

The native-order cases require a cloud runner's fail-closed libc interceptor.
It records and refuses nonnumeric names without calling the system resolver.
"""

import json
import os
import socket
from pathlib import Path

import pytest

from model_hub.tests.network_isolation import GUARD, ExternalNetworkRefused

NAME = "native-order-control.invalid"


def native_events():
    path = os.environ.get("NATIVE_RESOLVER_EVENTS")
    if not path:
        pytest.skip("requires the fail-closed native resolver control runner")
    return Path(path).read_text().splitlines()


def invoke(sock, method, address):
    if method == "sendto-flags":
        return sock.sendto(b"control", 0, address)
    if method == "sendto":
        return sock.sendto(b"control", address)
    if method == "sendmsg":
        return sock.sendmsg([b"control"], [], 0, address)
    return getattr(sock, method)(address)


@pytest.mark.parametrize(
    "method", ["connect", "connect_ex", "sendto", "sendto-flags", "sendmsg"]
)
def test_hostname_transport_is_counted_before_native_resolution(method):
    before = native_events()
    kind = socket.SOCK_STREAM if method.startswith("connect") else socket.SOCK_DGRAM
    with socket.socket(type=kind) as sock, GUARD.watch() as attempts:
        sock.settimeout(1)
        try:
            invoke(sock, method, (NAME, 443))
        except OSError:
            pass
    after = native_events()
    assert len(attempts) == 1, "hostname transport escaped Python lookup accounting"
    assert (attempts[0].host, attempts[0].port) == (NAME, 443)
    assert after == before, "hostname reached the native resolver before refusal"


@pytest.mark.parametrize(
    "method", ["getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"]
)
def test_resolver_and_connection_controls_are_refused(method):
    before = native_events()
    with GUARD.watch() as attempts:
        with pytest.raises(ExternalNetworkRefused):
            if method == "getaddrinfo":
                socket.getaddrinfo(NAME, 443)
            elif method == "create_connection":
                socket.create_connection((NAME, 443), timeout=1)
            else:
                getattr(socket, method)(NAME)
    assert len(attempts) == 1
    assert attempts[0].host == NAME
    assert native_events() == before


def test_hostname_loopback_roundtrip():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with GUARD.watch() as attempts:
            with socket.create_connection(
                ("localhost", server.getsockname()[1]), timeout=2
            ) as client:
                peer, _ = server.accept()
                with peer:
                    client.sendall(b"control")
                    assert peer.recv(7) == b"control"
    assert attempts == []


def test_refusals_are_persisted_as_individual_destination_events(tmp_path, monkeypatch):
    destination = tmp_path / "events.jsonl"
    monkeypatch.setenv("TEST_NETWORK_EVENTS", str(destination))
    with GUARD.watch() as attempts:
        with pytest.raises(ExternalNetworkRefused):
            socket.getaddrinfo(NAME, 443)
    assert len(attempts) == 1
    assert destination.exists(), "refusal counter was not persisted"
    events = [json.loads(line) for line in destination.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["host"] == NAME
    assert events[0]["port"] == 443
    assert events[0]["kind"] == "refused"
    assert events[0]["pid"] == os.getpid()


@pytest.mark.parametrize("host", ["127.0.0.1%other.invalid", "0.0.0.0%other.invalid"])
def test_ipv4_scope_suffix_cannot_be_mistaken_for_a_numeric_address(host):
    before = native_events()
    with GUARD.watch() as attempts:
        try:
            socket.getaddrinfo(host, 443)
        except OSError:
            pass
    assert len(attempts) == 1
    assert native_events() == before
