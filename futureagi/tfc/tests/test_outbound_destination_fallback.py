"""Public-address connection fallback without another resolution or request replay."""

import json
import socket
import time
from unittest.mock import Mock

import pytest

from tfc.tests import outbound_fixture
from tfc.utils import outbound_destination as destination

outbound_wire = outbound_fixture.outbound_wire


@pytest.mark.parametrize(
    "mode,first", [("fallback", "93.184.216.34"), ("fallback-v6", "2606:4700::1111")]
)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_public_address_fallback_uses_frozen_resolution(
    outbound_wire, mode, first, method
):
    outbound_wire.mode = mode
    response, error = None, None
    try:
        response = destination.request(
            method=method, url=outbound_wire.url + "/echo", json={"value": 3}
        )
    except destination.DestinationError as exc:
        error = str(exc)
    assert response is not None, error
    assert response.status_code == 200
    assert response.json() == {
        "method": method,
        "path": "/echo",
        "host": f"public.fixture.test:{outbound_wire.port}",
        "body": '{"value": 3}',
    }
    assert len(outbound_wire.received) == 1
    assert [
        json.loads(line) for line in outbound_wire.events.read_text().splitlines()
    ] == [
        ["dns", "public.fixture.test"],
        ["connect", [first, outbound_wire.port]],
        ["connect", ["93.184.216.35", outbound_wire.port]],
    ]


def test_all_answers_are_validated_before_fallback(outbound_wire):
    outbound_wire.mode = "fallback-mixed"
    with pytest.raises(destination.DestinationError, match="^address_class$"):
        destination.request(method="POST", url=outbound_wire.url)
    assert not outbound_wire.received
    assert "connect" not in outbound_wire.events.read_text()


def test_all_public_addresses_unavailable(outbound_wire):
    outbound_wire.mode = "all-unavailable"
    with pytest.raises(destination.DestinationError, match="^ConnectionRefusedError$"):
        destination.request(method="GET", url=outbound_wire.url)
    assert not outbound_wire.received
    assert [
        json.loads(line) for line in outbound_wire.events.read_text().splitlines()
    ] == [
        ["dns", "public.fixture.test"],
        ["connect", ["93.184.216.34", outbound_wire.port]],
        ["connect", ["93.184.216.35", outbound_wire.port]],
    ]


def test_failed_response_does_not_replay_post(outbound_wire):
    outbound_wire.mode = "fallback"
    with pytest.raises(destination.DestinationError, match="^RemoteDisconnected$"):
        destination.request(method="POST", url=outbound_wire.url + "/drop-response")
    assert len(outbound_wire.received) == 1
    events = [
        json.loads(line) for line in outbound_wire.events.read_text().splitlines()
    ]
    assert [kind for kind, _ in events] == ["dns", "connect", "connect"]


def test_fallback_connect_stays_within_wall_deadline(outbound_wire):
    outbound_wire.mode = "slow-connect"
    started = time.monotonic()
    with pytest.raises(destination.DestinationError, match="^deadline$"):
        destination.request(method="GET", url=outbound_wire.url, timeout=0.5)
    assert time.monotonic() - started < 1.5
    assert not outbound_wire.received


@pytest.mark.parametrize("exhausted", [False, True])
def test_fallback_connection_budget_includes_dns_and_previous_attempt(
    monkeypatch, exhausted
):
    clock = [0.0]
    attempts = []
    sock = Mock()

    def resolve(host, port, **kwargs):
        clock[0] = 0.25
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in ("93.184.216.34", "93.184.216.35")
        ]

    def connect(address, port, timeout):
        attempts.append((str(address), timeout))
        if len(attempts) == 1:
            clock[0] += 1 if exhausted else 0.125
            raise ConnectionRefusedError("synthetic first address unavailable")
        return sock

    response = Mock(status=200)
    response.getheader.side_effect = lambda key, default=None: default
    response.getheaders.return_value = []
    response.read.return_value = b""
    resolver = Mock(side_effect=resolve)
    monkeypatch.setattr(destination.socket, "getaddrinfo", resolver)
    monkeypatch.setattr(destination.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(destination, "_connect", connect)
    monkeypatch.setattr(
        destination.http.client.HTTPConnection, "getresponse", lambda self: response
    )
    result, error = None, None
    try:
        result = destination._exchange(
            {
                "method": "POST",
                "url": "http://public.fixture.test/",
                "timeout": 1,
                "max_bytes": 20,
            }
        )
    except (OSError, destination.DestinationError) as exc:
        error = str(exc)
    resolver.assert_called_once_with("public.fixture.test", 80, type=socket.SOCK_STREAM)
    assert attempts[0] == ("93.184.216.34", 0.75)
    if exhausted:
        assert error == "deadline"
        assert len(attempts) == 1
        sock.sendall.assert_not_called()
    else:
        assert error is None
        assert result["status"] == 200
        assert attempts[1] == ("93.184.216.35", 0.625)
        sock.close.assert_called_once()
