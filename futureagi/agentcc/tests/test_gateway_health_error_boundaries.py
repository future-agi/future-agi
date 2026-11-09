"""Certificate and HTTP failures are not successful unreachable reads.

Use real loopback TLS and the HTTP transport actually called by the client.
The cloud runner supplies the control-complete 59l guard to every process.
"""

import datetime
import ipaddress
import socket
import ssl
import threading
from unittest.mock import Mock, patch

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agentcc.services.gateway_client import GatewayClient, GatewayClientError

READ_PATHS = [
    "/agentcc/gateways/",
    "/agentcc/gateways/default/",
    "/agentcc/gateways/default/config/",
]
PRIVATE_DETAIL = "synthetic-private-url-and-credential"
TLS_MESSAGE = "Gateway TLS certificate verification failed"


@pytest.fixture
def untrusted_tls_gateway(tmp_path):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "loopback-test")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(private, hashes.SHA256())
    )
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "private.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_file), str(key_file))
    seen = []
    failures = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)
        url = f"https://127.0.0.1:{listener.getsockname()[1]}"

        def serve():
            try:
                peer, _ = listener.accept()
                with peer:
                    seen.append(True)
                    peer.settimeout(5)
                    try:
                        with context.wrap_socket(peer, server_side=True):
                            failures.append("unexpectedly trusted")
                    except ssl.SSLError as error:
                        if error.reason != "TLSV1_ALERT_UNKNOWN_CA":
                            failures.append(type(error).__name__)
            except Exception as error:
                failures.append(type(error).__name__)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield GatewayClient(url)
        finally:
            thread.join(15)
            assert not thread.is_alive()
            assert seen == [True], "the real loopback TLS handshake must be reached"
            assert failures == []


@pytest.mark.integration
@pytest.mark.parametrize("path", READ_PATHS)
@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503])
def test_http_health_failure_is_not_successful_unreachable(auth_client, path, status):
    client = GatewayClient("http://127.0.0.1:9")
    response = httpx.Response(
        status,
        json={"error": PRIVATE_DETAIL},
        request=httpx.Request("GET", client.base_url + "/healthz"),
    )
    send = httpx.HTTPTransport.handle_request
    health = Mock(return_value=response)

    def transport(transport, request):
        if str(request.url) == client.base_url + "/healthz":
            return health(request)
        return send(transport, request)

    with patch(
        "agentcc.views.gateway.get_gateway_client", return_value=client
    ) as getter:
        with patch.object(httpx.HTTPTransport, "handle_request", transport):
            actual = auth_client.get(path)
    getter.assert_called_once_with()
    health.assert_called_once()
    assert actual.status_code == (404 if path == READ_PATHS[1] else 400)
    assert "unreachable" not in str(actual.data)
    assert PRIVATE_DETAIL not in str(actual.data)
    assert client.base_url not in str(actual.data)
    if path != READ_PATHS[1]:
        assert f"Gateway returned {status}" in str(actual.data)


@pytest.mark.integration
@pytest.mark.parametrize("path", READ_PATHS)
def test_real_tls_failure_is_not_successful_unreachable(
    auth_client, untrusted_tls_gateway, path
):
    with patch(
        "agentcc.views.gateway.get_gateway_client", return_value=untrusted_tls_gateway
    ) as getter:
        actual = auth_client.get(path)
    getter.assert_called_once_with()
    assert actual.status_code == (404 if path == READ_PATHS[1] else 400)
    assert "unreachable" not in str(actual.data)
    assert untrusted_tls_gateway.base_url not in str(actual.data)
    if path != READ_PATHS[1]:
        assert TLS_MESSAGE in str(actual.data)


def test_real_tls_failure_preserves_certificate_error_type(untrusted_tls_gateway):
    with pytest.raises(ssl.SSLCertVerificationError) as caught:
        untrusted_tls_gateway.health_check()
    assert caught.value.args == (TLS_MESSAGE,)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("link", ["__cause__", "__context__"])
def test_wrapped_certificate_error_is_value_free(link):
    # HTTPX -> httpcore -> SSL. Do not depend on their rendered error text.
    original = httpx.ConnectError(PRIVATE_DETAIL)
    middle = OSError(PRIVATE_DETAIL)
    setattr(original, link, middle)
    setattr(middle, link, ssl.SSLCertVerificationError(PRIVATE_DETAIL))
    with patch.object(
        httpx.HTTPTransport, "handle_request", side_effect=original
    ) as send:
        with pytest.raises(ssl.SSLCertVerificationError) as caught:
            GatewayClient("http://127.0.0.1:9", "synthetic-admin").health_check()
    send.assert_called_once()
    assert caught.value.args == (TLS_MESSAGE,)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("cause", [None, ssl.SSLEOFError("synthetic TLS reset")])
def test_transport_message_alone_does_not_mean_certificate_failure(cause):
    original = httpx.ConnectError("CERTIFICATE_VERIFY_FAILED: synthetic reset")
    original.__cause__ = cause
    with patch.object(
        httpx.HTTPTransport, "handle_request", side_effect=original
    ) as send:
        with pytest.raises(GatewayClientError) as caught:
            GatewayClient("http://127.0.0.1:9").health_check()
    send.assert_called_once()
    assert str(caught.value) == "Cannot connect to gateway"
    assert caught.value.status_code is None
    assert caught.value.__cause__ is original


def test_cyclic_transport_cause_is_still_an_outage():
    original = httpx.ConnectError(PRIVATE_DETAIL)
    original.__cause__ = original
    with patch.object(
        httpx.HTTPTransport, "handle_request", side_effect=original
    ) as send:
        with pytest.raises(GatewayClientError) as caught:
            GatewayClient("http://127.0.0.1:9").health_check()
    send.assert_called_once()
    assert str(caught.value) == "Cannot connect to gateway"
    assert caught.value.status_code is None
