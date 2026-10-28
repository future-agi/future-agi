"""Wire-level checks for bounded, pinned public-destination requests."""

import json
import time
from unittest.mock import Mock

import pytest

from tfc.tests import outbound_fixture
from tfc.utils import outbound_destination as destination

outbound_wire = outbound_fixture.outbound_wire


def test_public_request_preserves_host_method_query_and_body(outbound_wire):
    response = destination.request(
        method="POST",
        url=outbound_wire.url + "/echo",
        params={"q": "a b"},
        json={"value": 3},
        headers={"hOsT": "different.fixture.test", "accept-encoding": "gzip"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "method": "POST",
        "path": "/echo?q=a+b",
        "host": f"public.fixture.test:{outbound_wire.port}",
        "body": '{"value": 3}',
    }
    assert outbound_wire.received[0][2]["Accept-Encoding"] == "identity"
    events = [
        json.loads(line) for line in outbound_wire.events.read_text().splitlines()
    ]
    assert events == [
        ["dns", "public.fixture.test"],
        ["connect", ["93.184.216.34", outbound_wire.port]],
    ]


def test_mixed_dns_answers_are_all_checked(outbound_wire):
    outbound_wire.mode = "mixed"
    with pytest.raises(destination.DestinationError, match="address_class"):
        destination.request(method="GET", url=outbound_wire.url)
    assert not outbound_wire.received
    assert "connect" not in outbound_wire.events.read_text()


def test_redirect_is_not_followed(outbound_wire):
    with pytest.raises(destination.DestinationError, match="redirect"):
        destination.request(method="GET", url=outbound_wire.url + "/redirect")
    assert [request[1] for request in outbound_wire.received] == ["/redirect"]


@pytest.mark.parametrize(
    "path,reason", [("/large", "body_limit"), ("/compressed", "content_encoding")]
)
def test_streaming_response_limits(outbound_wire, path, reason):
    with pytest.raises(destination.DestinationError, match=reason):
        destination.request(method="GET", url=outbound_wire.url + path)


@pytest.mark.parametrize(
    "mode,path", [("slow-dns", "/"), ("public", "/slow-headers"), ("public", "/drip")]
)
def test_wall_deadline_includes_dns_headers_and_drip(outbound_wire, mode, path):
    outbound_wire.mode = mode
    started = time.monotonic()
    with pytest.raises(destination.DestinationError, match="deadline"):
        destination.request(method="GET", url=outbound_wire.url + path, timeout=0.5)
    assert time.monotonic() - started < 1.5


def test_https_uses_hostname_for_sni_and_certificate_verification(monkeypatch):
    # Exercise stdlib HTTPSConnection.connect, without performing a TLS network call.
    context = Mock()
    sock = Mock()
    response = Mock(status=200)
    response.getheader.side_effect = lambda key, default=None: default
    response.getheaders.return_value = []
    response.read.return_value = b""
    monkeypatch.setattr(
        destination.ssl, "create_default_context", lambda **kwargs: context
    )
    monkeypatch.setattr(
        destination,
        "_resolve",
        lambda *args: destination.ipaddress.ip_address("93.184.216.34"),
    )
    monkeypatch.setattr(destination, "_connect", lambda *args: sock)
    monkeypatch.setattr(
        destination.http.client.HTTPSConnection, "getresponse", lambda self: response
    )
    destination._exchange(
        {
            "method": "GET",
            "url": "https://public.fixture.test/value",
            "timeout": 1,
            "max_bytes": 20,
        }
    )
    context.wrap_socket.assert_called_once_with(
        sock, server_hostname="public.fixture.test"
    )


@pytest.mark.parametrize(
    "address",
    [
        "100.127.255.254",
        "[fe80::abcd]",
        "[fec0::abcd]",
        "[::ffff:a00:1]",
        "[64:ff9b::a00:1]",
        "[2002:a00:1::1]",
        "[2001:0::1]",
        "168.63.129.16",
        "[::ffff:0:7f00:1]",
    ],
)
def test_additional_address_forms_are_refused(address, monkeypatch):
    spawn = Mock(side_effect=AssertionError("preflight must precede worker startup"))
    monkeypatch.setattr(destination.subprocess, "Popen", spawn)
    with pytest.raises(destination.DestinationError, match="^address_class$"):
        destination.request(method="GET", url=f"http://{address}/")
    spawn.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1./",
        "http://0x5db8d822/",
        "http://public.fixture.test:0/",
        "http://[fe80::1%25en0]/",
        "http://public.fixture.test\\@local/",
        "http://public.fixture.test\n/",
    ],
)
def test_noncanonical_authorities_are_refused(url, monkeypatch):
    spawn = Mock(side_effect=AssertionError("preflight must precede worker startup"))
    monkeypatch.setattr(destination.subprocess, "Popen", spawn)
    with pytest.raises(destination.DestinationError, match="^url_"):
        destination.request(method="GET", url=url)
    spawn.assert_not_called()
