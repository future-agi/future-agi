"""The host-events sender's trust for Omega EE's certificate.

Inside the cluster EE serves https://omega-os.omega.svc:8443 with a certificate from a private CA
(omega-internal-ca). The default trust store (certifi) does not hold it, so without its own CA
every delivery failed with CERTIFICATE_VERIFY_FAILED, was recorded as ee_unreachable, and a
removed member kept their Console streams until the 15-minute cap while load_config() said READY.

OMEGA_HOST_EVENTS_CA_FILE names that CA (a path, never a secret). It is checked when the channel
is configured, so a missing or unloadable file is MISCONFIGURED instead of a stream of failed
sends, and it is passed as ``verify=`` on the sender's own request only: the trust store of every
other outbound HTTPS call in the API and the workers stays as it was. Unset, nothing changes.
"""

import datetime
import ipaddress
import json
import ssl
import tempfile
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from accounts.host_events import config as channel
from accounts.host_events.config import READY, EventChannelConfig
from accounts.host_events.transport import send_batch

SECRET = b"k" * 48
# Every variable that would give requests another trust store or route than the one under test.
_AMBIENT = (
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)


def _name(common_name):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _certificate(subject, issuer, public_key, signing_key, *, ca, san=None):
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(san), critical=False
        )
    return builder.sign(signing_key, hashes.SHA256())


def _pem(certificate):
    return certificate.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(scope="module")
def pki():
    """A private CA (omega-internal-ca's stand-in) and EE's certificate for 127.0.0.1."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        ca_key = ec.generate_private_key(ec.SECP256R1())
        ca = _certificate(
            _name("omega-internal-ca"),
            _name("omega-internal-ca"),
            ca_key.public_key(),
            ca_key,
            ca=True,
        )
        ee_key = ec.generate_private_key(ec.SECP256R1())
        ee = _certificate(
            _name("omega-os"),
            ca.subject,
            ee_key.public_key(),
            ca_key,
            ca=False,
            san=[
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.DNSName("localhost"),
            ],
        )
        (root / "ca.crt").write_bytes(_pem(ca))
        (root / "ee.crt").write_bytes(_pem(ee))
        (root / "ee.key").write_bytes(
            ee_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        (root / "not-a-ca.crt").write_bytes(b"not a certificate\n")
        yield root


@pytest.fixture
def no_ambient_trust(monkeypatch):
    for name in _AMBIENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")


class _EE(BaseHTTPRequestHandler):
    """Omega EE's receiver: applies every event it is sent."""

    def do_POST(self):  # noqa: N802 - http.server's name
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        answer = json.dumps(
            {
                "receivedAtMs": 1,
                "results": [
                    {"id": event_id, "outcome": "applied"}
                    for event_id in body.get("ids", [])
                ],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)

    def log_message(
        self, *args
    ):  # the refused handshakes are expected; keep the log quiet
        pass


@pytest.fixture
def tls_ee(pki):
    server = HTTPServer(("127.0.0.1", 0), _EE)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pki / "ee.crt", pki / "ee.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}/host/events"
    finally:
        server.shutdown()
        server.server_close()


def _ready(url, **overrides):
    config = EventChannelConfig(
        state=READY,
        issuer="https://futureagi.test/omega",
        url=url,
        key_id="fa-host-events-1",
        secret=SECRET,
    )
    return replace(config, **overrides)


@pytest.fixture
def secret_file():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "secret"
        path.write_bytes(SECRET)
        path.chmod(0o600)
        yield path


def _environ(secret_file, **extra):
    return {
        "OMEGA_SQUAD_DELEGATION_ISSUER": "https://futureagi.test/omega",
        "OMEGA_HOST_EVENTS_URL": "https://omega-os.omega.svc:8443/host/events",
        "OMEGA_HOST_EVENTS_KEY_ID": "fa-host-events-1",
        "OMEGA_HOST_EVENTS_SECRET_FILE": str(secret_file),
        **extra,
    }


# -- configuration ---------------------------------------------------------------------------


def test_without_a_ca_file_the_default_trust_is_unchanged(secret_file):
    config = channel.load_config(_environ(secret_file))
    assert (config.state, config.problem) == (READY, "")
    assert config.ca_file == ""
    blank = channel.load_config(_environ(secret_file, OMEGA_HOST_EVENTS_CA_FILE="  "))
    assert (blank.state, blank.ca_file) == (READY, "")


def test_a_ca_file_is_checked_and_kept(pki, secret_file):
    config = channel.load_config(
        _environ(secret_file, OMEGA_HOST_EVENTS_CA_FILE=f" {pki / 'ca.crt'} ")
    )
    assert (config.state, config.problem) == (READY, "")
    assert config.ca_file == str(pki / "ca.crt")


def test_a_relative_missing_or_unloadable_ca_file_is_misconfigured(pki, secret_file):
    cases = {
        "ca.crt": "ca_path_not_absolute",
        str(pki / "missing.crt"): "ca_file_unreadable",
        str(pki / "not-a-ca.crt"): "ca_file_unreadable",
        str(pki): "ca_file_unreadable",
    }
    for path, problem in cases.items():
        config = channel.load_config(
            _environ(secret_file, OMEGA_HOST_EVENTS_CA_FILE=path)
        )
        assert (config.state, config.problem) == (channel.MISCONFIGURED, problem), path
        assert config.secret == b""
        # A problem is a short code: never the path.
        assert str(pki) not in repr(config)


# -- delivery --------------------------------------------------------------------------------


def _send(config):
    body = json.dumps({"ids": ["x"]}).encode()
    return send_batch(config, body, ["x"], timeout=(2.0, 2.0))


def test_ee_behind_a_private_ca_is_reached_only_with_its_ca_file(
    pki, tls_ee, no_ambient_trust
):
    # The item-157 symptom: READY, yet every send fails the certificate check.
    refused = _send(_ready(tls_ee))
    assert (refused.delivered, refused.error) == (frozenset(), "ee_unreachable")
    delivered = _send(_ready(tls_ee, ca_file=str(pki / "ca.crt")))
    assert (delivered.delivered, delivered.error) == (frozenset({"x"}), "")


def test_the_ca_file_is_passed_only_to_the_sender_and_only_when_set():
    calls = []

    class Session:
        def post(self, url, **kwargs):
            calls.append(kwargs)
            raise requests.ConnectionError("offline")

    url = "https://omega-os.omega.svc:8443/host/events"
    send_batch(_ready(url), b"{}", ["x"], session=Session())
    send_batch(_ready(url, ca_file="/etc/ca/ca.crt"), b"{}", ["x"], session=Session())
    # Unset: requests' own default (and REQUESTS_CA_BUNDLE from global.caBundle) still applies,
    # and a caller's session.verify is not overridden.
    assert "verify" not in calls[0]
    assert calls[1]["verify"] == "/etc/ca/ca.crt"
