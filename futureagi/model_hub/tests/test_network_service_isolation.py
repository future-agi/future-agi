"""Configured test services cannot grant access to arbitrary network hosts."""

import socket
from types import SimpleNamespace

import pytest
from django.conf import settings

from model_hub.tests import network_isolation
from model_hub.tests.network_isolation import ExternalNetworkRefused, NetworkGuard


@pytest.fixture
def service_guard(monkeypatch):
    for name in network_isolation.SERVICE_HOST_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "DATABASES", {})
    guard = NetworkGuard()
    # Exercise the hook synchronously with a fake resolver, never native DNS.
    monkeypatch.setattr(guard, "install", lambda: None)
    calls = []
    answers = ["172.20.0.2"]
    nested = []

    def resolve(host, port):
        calls.append((host, guard.active))
        guard._hook("socket.getaddrinfo", (host, port, 0, 0, 0))
        for target in nested:
            guard._hook("socket.getaddrinfo", (target, port, 0, 0, 0))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))
            for ip in answers
        ]

    monkeypatch.setattr(network_isolation.socket, "getaddrinfo", resolve)
    return SimpleNamespace(guard=guard, calls=calls, answers=answers, nested=nested)


@pytest.mark.parametrize(
    "host",
    [
        "metadata.google.internal",
        "METADATA.GOOGLE.INTERNAL.",
        "metadata",
        "169.254.169.254",
        "[::ffff:169.254.169.254]",
        "remote-service.invalid",
        "8.8.8.8",
    ],
    ids=[
        "metadata-name",
        "metadata-fqdn",
        "metadata-alias",
        "link-local",
        "mapped-link-local",
        "unknown-name",
        "public-ip",
    ],
)
def test_configured_untrusted_hosts_are_not_resolved_or_allowed(
    service_guard, monkeypatch, host
):
    monkeypatch.setenv("PG_HOST", host)
    guard = service_guard.guard
    with guard.watch() as attempts:
        assert service_guard.calls == []
        assert not guard.allowed(host)
        for event, args in (
            ("socket.getaddrinfo", (host, 80, 0, 0, 0)),
            ("socket.connect", (None, (host, 80))),
            ("socket.sendto", (None, (host, 80))),
        ):
            with pytest.raises(ExternalNetworkRefused):
                guard._hook(event, args)
    assert len(attempts) == 3
    assert not guard.active


@pytest.mark.parametrize(
    "addresses",
    [
        ["169.254.169.254"],
        ["fe80::1"],
        ["::ffff:169.254.169.254"],
        ["172.20.0.2", "169.254.169.254"],
        ["8.8.8.8"],
    ],
    ids=[
        "link-local",
        "ipv6-link-local",
        "mapped-link-local",
        "mixed-answers",
        "public-answer",
    ],
)
def test_fixture_alias_with_unsafe_answer_is_refused_and_counted(
    service_guard, monkeypatch, addresses
):
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    service_guard.answers[:] = addresses
    guard = service_guard.guard
    with guard.watch() as attempts:
        assert service_guard.calls == [("minio", True)]
        assert not guard.allowed("minio")
        assert all(not guard.allowed(address) for address in addresses)
        assert len(attempts) >= 1
        assert all(attempt.event == "socket.getaddrinfo" for attempt in attempts)
        with pytest.raises(ExternalNetworkRefused):
            guard._hook("socket.getaddrinfo", ("minio", 9000, 0, 0, 0))
        with pytest.raises(ExternalNetworkRefused):
            guard._hook("socket.connect", (None, ("minio", 9000)))
    with pytest.raises(pytest.fail.Exception, match="network attempt"):
        network_isolation.fail_on_attempts(attempts)
    assert not guard.active


def test_fixture_resolution_is_accounted_before_watch_body(service_guard, monkeypatch):
    monkeypatch.setenv("PG_HOST", "postgres")
    service_guard.nested[:] = ["metadata.google.internal"]
    guard = service_guard.guard
    with guard.watch() as attempts:
        assert service_guard.calls == [("postgres", True)]
        assert not guard.allowed("postgres")
        assert [(a.event, a.host) for a in attempts] == [
            ("socket.getaddrinfo", "metadata.google.internal")
        ]
    assert not guard.active


def test_only_fixture_addresses_are_retained_and_rechecked(service_guard, monkeypatch):
    monkeypatch.setenv("PG_HOST", "POSTGRES.")
    guard = service_guard.guard
    with guard.watch() as attempts:
        assert service_guard.calls == [("postgres", True)]
        assert guard.allowed("postgres")
        assert guard.allowed("172.20.0.2")
        assert attempts == []
    service_guard.answers[:] = ["169.254.169.254"]
    with guard.watch() as attempts:
        assert service_guard.calls == [("postgres", True), ("postgres", True)]
        assert not guard.allowed("postgres")
        assert not guard.allowed("172.20.0.2")
        assert not guard.allowed("169.254.169.254")
        assert len(attempts) == 1
    assert not guard.active
