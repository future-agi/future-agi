"""Real bundled roots reject a private CA; the product's custom-CA paths accept it.

Only a throwaway TLS server on 127.0.0.1 is contacted. Unlike the existing
webhook PKI tests, certifi.where is never replaced. safe_fetch deliberately
has no environment CA override and blocks loopback: exercise its urllib3
transport settings directly, not a weakened address guard.
"""

import datetime as dt
import ipaddress
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import requests
import urllib3
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from tfc.utils import ssrf_guard, ssrf_post
from tfc.utils.storage_client import storage_http_client
from webhooks.config import extra_ca_file


@pytest.fixture
def private_tls(tmp_path, monkeypatch):
    for name in (
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "FI_WEBHOOKS_EXTRA_CA_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    # Keep env-aware provider transports off any operator-configured proxy.
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "T6 throwaway CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path, cert_path, key_path = (
        tmp_path / name for name in ("ca.pem", "server.pem", "server.key")
    )
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.do_GET()

        def log_message(self, *_args):
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield (
            f"https://127.0.0.1:{server.server_port}/",
            str(ca_path),
            server.server_port,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_safe_fetch_transport_rejects_private_ca_with_real_certifi(private_tls):
    _, _, port = private_tls
    with ssrf_guard._open_pinned_pool(
        "https",
        "127.0.0.1",
        "127.0.0.1",
        port,
        2,
    ) as pool:
        with pytest.raises(
            urllib3.exceptions.SSLError, match="CERTIFICATE_VERIFY_FAILED"
        ):
            pool.request("GET", "/")


def test_storage_uses_ssl_cert_file(private_tls, monkeypatch):
    url, ca_path, _ = private_tls
    with storage_http_client() as client:
        with pytest.raises(
            urllib3.exceptions.SSLError, match="CERTIFICATE_VERIFY_FAILED"
        ):
            client.request("GET", url, retries=False, timeout=2)
    monkeypatch.setenv("SSL_CERT_FILE", ca_path)
    with storage_http_client() as client:
        response = client.request("GET", url, retries=False, timeout=2)
        assert (response.status, response.data) == (200, b"ok")


def test_webhook_extra_ca_is_added_to_real_certifi(private_tls, monkeypatch):
    _, ca_path, port = private_tls

    def post():
        return ssrf_post.safe_post(
            scheme="https",
            host="127.0.0.1",
            port=port,
            target="/",
            addresses=("127.0.0.1",),
            headers=(),
            body=b"{}",
            deadline=time.monotonic() + 5,
            ca_certs=extra_ca_file(hosted=False),
        )

    assert post().error == "tls_error"
    monkeypatch.setenv("FI_WEBHOOKS_EXTRA_CA_FILE", ca_path)
    result = post()
    assert (result.status, result.body, result.error) == (200, b"ok", None)
    assert extra_ca_file(hosted=True) is None
    # This operator setting is additive, not a replacement for public roots.
    original = set(ssrf_post.tls_context().get_ca_certs(binary_form=True))
    augmented = set(ssrf_post.tls_context(ca_path).get_ca_certs(binary_form=True))
    assert original < augmented


def test_provider_transports_honor_environment_ca_bundles(private_tls, monkeypatch):
    """Requests/HTTPX defaults used by outbound clients; no provider is called."""
    url, ca_path, _ = private_tls
    with pytest.raises(requests.exceptions.SSLError, match="CERTIFICATE_VERIFY_FAILED"):
        requests.get(url, timeout=2)
    with httpx.Client() as client:
        with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
            client.get(url, timeout=2)
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", ca_path)
    monkeypatch.setenv("SSL_CERT_FILE", ca_path)
    assert requests.get(url, timeout=2).content == b"ok"
    with httpx.Client() as client:
        assert client.get(url, timeout=2).content == b"ok"
