"""Pre-native adapters keep local aliases numeric and record own-host events."""

import json
import socket
import sys
from types import SimpleNamespace

import pytest

from model_hub.tests import network_isolation
from model_hub.tests.network_isolation import (
    GUARD,
    ExternalNetworkRefused,
    NetworkGuard,
)
from model_hub.tests.network_socket_layer import install_socket_layer


@pytest.fixture
def local_guard(monkeypatch, tmp_path):
    guard = NetworkGuard()
    monkeypatch.setattr(guard, "install", lambda: None)
    monkeypatch.setattr(
        network_isolation, "configured_service_hosts", lambda: {"postgres"}
    )
    guard._own_names = frozenset({"fixture-machine", "fixture-machine.internal"})
    guard._own_addresses = frozenset({"127.0.0.2"})
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("TEST_NETWORK_EVENTS", str(path))
    calls = []
    native = socket.getaddrinfo

    def resolve(host, port, *args):
        calls.append(host)
        if host == "postgres":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("127.0.0.1", 0),
                )
            ]
        # In this control any native name would be a failure, not real DNS.
        socket.inet_pton(socket.AF_INET, host)
        return native(host, port, *args)

    # Record original functions for monkeypatch teardown before installing adapters.
    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        monkeypatch.setattr(socket.socket, name, getattr(socket.socket, name))
    for name in (
        "getaddrinfo",
        "create_connection",
        "gethostbyname",
        "gethostbyname_ex",
        "gethostbyaddr",
        "getnameinfo",
    ):
        monkeypatch.setattr(socket, name, getattr(socket, name))
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    install_socket_layer(guard)
    return SimpleNamespace(guard=guard, calls=calls, path=path)


@pytest.mark.parametrize("method", ["connect", "connect_ex", "sendto", "sendmsg"])
def test_fixture_name_is_resolved_once_then_transport_uses_numeric_address(
    local_guard, method
):
    datagram = method.startswith("send")
    kind = socket.SOCK_DGRAM if datagram else socket.SOCK_STREAM
    with socket.socket(type=kind) as server:
        server.settimeout(2)
        server.bind(("127.0.0.1", 0))
        if not datagram:
            server.listen(1)
        with local_guard.guard.watch() as attempts, socket.socket(type=kind) as client:
            client.settimeout(2)
            destination = ("postgres", server.getsockname()[1])
            if method == "sendto":
                client.sendto(b"control", destination)
            elif method == "sendmsg":
                client.sendmsg([b"control"], [], 0, destination)
            else:
                result = getattr(client, method)(destination)
                assert result in (None, 0)
                peer, _ = server.accept()
                peer.close()
            if datagram:
                assert server.recvfrom(7)[0] == b"control"
    assert attempts == []
    assert local_guard.calls.count("postgres") == 1
    assert set(local_guard.calls) == {"postgres", "127.0.0.1"}


def test_own_host_requires_exact_snapshot_match_and_records_events(local_guard):
    guard = local_guard.guard
    with guard.watch() as attempts:
        assert socket.gethostbyname("fixture-machine") == "127.0.0.2"
        assert (
            socket.gethostbyaddr("fixture-machine.internal")[0]
            == "fixture-machine.internal"
        )
        assert socket.gethostbyaddr("127.0.0.2")[0] in guard._own_names
        for host in (
            "fixture-machine.invalid",
            "prefix-fixture-machine",
            "FIXTURE-MACHINE",
            "fixture-machine.",
        ):
            with pytest.raises(ExternalNetworkRefused):
                socket.getaddrinfo(host, 80)
    assert len(attempts) == 4
    events = [json.loads(line) for line in local_guard.path.read_text().splitlines()]
    own = [event for event in events if event["kind"] == "own-host"]
    assert own and all(
        event["host"] in guard._own_names | guard._own_addresses for event in own
    )
    assert sum(event["kind"] == "refused" for event in events) == 4
    assert not any("fixture-machine" in host for host in local_guard.calls)


def test_audit_hook_remains_a_second_layer():
    with GUARD.watch() as attempts:
        with pytest.raises(ExternalNetworkRefused):
            sys.audit("socket.connect", None, ("192.0.2.1", 443))
    assert [(a.event, a.host, a.port) for a in attempts] == [
        ("socket.connect", "192.0.2.1", 443)
    ]


@pytest.mark.parametrize("method", ["gethostbyaddr", "getnameinfo"])
def test_reverse_lookup_refuses_unknown_name_before_native(local_guard, method):
    with local_guard.guard.watch() as attempts:
        with pytest.raises(ExternalNetworkRefused):
            if method == "getnameinfo":
                socket.getnameinfo(("reverse-control.invalid", 443), 0)
            else:
                socket.gethostbyaddr("reverse-control.invalid")
    assert len(attempts) == 1
    assert local_guard.calls == ["postgres"]
