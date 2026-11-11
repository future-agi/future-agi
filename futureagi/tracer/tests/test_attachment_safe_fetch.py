"""SSRF-safe importer transport against a real loopback TLS server.

The only injected seam is address authorization (so the loopback test server
is reachable); everything else, including TLS verification, is the real code.
"""

import datetime
import http.server
import io
import socket
import ssl
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tracer.services.attachments import safe_fetch
from tracer.services.attachments.contract import AttachmentError

pytestmark = [pytest.mark.unit]

BODY = bytes(range(256)) * 64  # 16 KiB
ETAG = '"v1"'


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",
        "255.255.255.255",
        "224.0.0.1",
        "198.18.0.1",
        "192.0.2.1",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd00:ec2::254",  # AWS IMDS over IPv6
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:169.254.169.254",
        "2002:7f00:0001::1",  # 6to4 of 127.0.0.1
        "2002:0a00:0001::1",  # 6to4 of 10.0.0.1
        "2001:0000:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
        "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
        "64:ff9b::808:808",  # NAT64 even for a public v4
        "not-an-address",
    ],
)
def test_private_reserved_and_embedded_addresses_denied(address):
    assert safe_fetch.address_allowed(address) is False


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_unicast_allowed(address):
    assert safe_fetch.address_allowed(address) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://media.example.com/a",
        "https://user:pass@media.example.com/a",
        "https://127.0.0.1/a",
        "https://[::1]/a",
        "https://evil.com/a",
        "https://media.example.com.evil.com/a",
        "https://xmedia.example.com/a",
        "https://media.example.com/a\r\nHost: x",
        "ftp://media.example.com/a",
        "https://media.example.com%2f@evil.com/",
        "",
        None,
    ],
)
def test_destination_policy(url):
    with pytest.raises(AttachmentError, match="source_unavailable"):
        safe_fetch.parse_destination(url, approved_hosts=("media.example.com",))


def test_destination_keeps_signed_query_out_of_repr():
    destination = safe_fetch.parse_destination(
        "https://cdn.media.example.com/r.wav?X-Signature=canary-sig",
        approved_hosts=("media.example.com",),
    )
    assert destination.target == "/r.wav?X-Signature=canary-sig"
    assert "canary" not in repr(destination)


def answers(*addresses):
    def getaddrinfo(host, port, *args):
        return [
            (
                socket.AF_INET6 if ":" in a else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (a, port),
            )
            for a in addresses
        ]

    return getaddrinfo


def test_any_private_answer_denies_the_host():
    with pytest.raises(AttachmentError, match="source_unavailable"):
        safe_fetch.resolve(
            "media.example.com", 443, getaddrinfo=answers("8.8.8.8", "10.0.0.1")
        )
    assert safe_fetch.resolve(
        "media.example.com", 443, getaddrinfo=answers("8.8.8.8", "1.1.1.1")
    ) == ["8.8.8.8", "1.1.1.1"]


def _certificate(tmp_path, name):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / f"{name}.pem", tmp_path / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


class Handler(http.server.BaseHTTPRequestHandler):
    mode = "ok"
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        type(self).seen.append(dict(self.headers))
        start, end = (int(x) for x in self.headers["Range"][6:].split("-"))
        chunk = BODY[start : end + 1]
        mode = type(self).mode
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "https://169.254.169.254/latest/meta-data/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if mode == "whole":
            self.send_response(200)
            self.send_header("Content-Length", str(len(BODY)))
            self.send_header("ETag", ETAG)
            self.end_headers()
            self.wfile.write(BODY)
            return
        if mode == "precondition" and "If-Match" in self.headers:
            self.send_response(412)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(206)
        total = len(BODY) + (1 if mode == "total" else 0)
        self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        length = len(chunk) - (1 if mode == "short" else 0)
        self.send_header("Content-Length", str(length))
        self.send_header("ETag", '"v2"' if mode == "etag" and start else ETAG)
        if mode == "gzip":
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        self.wfile.write(chunk[:length] + (b"EXTRA" if mode == "long" else b""))


@pytest.fixture
def server(tmp_path):
    cert, key = _certificate(tmp_path, "media.example.com")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    Handler.mode, Handler.seen = "ok", []
    yield httpd.server_address[1], cert, tmp_path
    httpd.shutdown()
    httpd.server_close()


def source(port, cert, *, host="media.example.com", window=4096):
    client = ssl.create_default_context(cafile=str(cert))
    destination = safe_fetch.parse_destination(
        f"https://{host}:{port}/rec.wav?sig=canary-signature",
        approved_hosts=(host,),
    )
    return safe_fetch.RangeSource(
        destination=destination,
        total_bytes=len(BODY),
        window=window,
        timeout_seconds=10,
        bytes_per_second=1024 * 1024 * 1024,
        context=client,
        getaddrinfo=answers("127.0.0.1"),
        allow=lambda address: address == "127.0.0.1",  # loopback test seam only
    )


def read_all(reader, window=4096):
    sink, offset = io.BytesIO(), 0
    while offset < len(BODY):
        length = min(window, len(BODY) - offset)
        reader.read_range(offset, length, sink)
        offset += length
    return sink.getvalue()


def test_exact_ranges_pinned_and_header_minimal(server, monkeypatch):
    port, cert, _ = server
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9")
    monkeypatch.setenv("NO_PROXY", "")
    assert read_all(source(port, cert)) == BODY
    assert len(Handler.seen) == 4
    for headers in Handler.seen:
        lowered = {k.lower() for k in headers}
        assert "authorization" not in lowered and "cookie" not in lowered
        assert headers["Accept-Encoding"] == "identity"
    assert all(h.get("If-Match") == ETAG for h in Handler.seen[1:])


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("whole", "source_unavailable"),  # Range ignored
        ("redirect", "source_unavailable"),  # never followed
        ("short", "validation_failed"),
        ("total", "validation_failed"),  # total length changed
        ("etag", "validation_failed"),  # generation changed between ranges
        ("gzip", "validation_failed"),
        ("precondition", "conflict"),
    ],
)
def test_unsafe_or_inconsistent_responses_rejected(server, mode, code):
    port, cert, _ = server
    Handler.mode = mode
    with pytest.raises(AttachmentError) as error:
        read_all(source(port, cert))
    assert error.value.code == code
    assert "canary" not in str(error.value)
    if mode == "redirect":
        assert len(Handler.seen) == 1


def test_excess_body_is_not_drained(server):
    port, cert, _ = server
    Handler.mode = "long"
    sink = io.BytesIO()
    source(port, cert).read_range(0, 4096, sink)
    # Content-Length bounds the read; trailing bytes are never consumed.
    assert sink.getvalue() == BODY[:4096]


def test_range_larger_than_window_refused_before_any_request(server):
    port, cert, _ = server
    with pytest.raises(AttachmentError, match="validation_failed"):
        source(port, cert).read_range(0, 8192, io.BytesIO())
    assert Handler.seen == []


def test_tls_hostname_and_trust_are_verified(server, tmp_path):
    port, cert, _ = server
    other, _ = _certificate(tmp_path, "other.example.com")
    reader = source(port, other)
    with pytest.raises(AttachmentError, match="source_unavailable"):
        reader.read_range(0, 10, io.BytesIO())
    default = source(port, cert)
    default.context = None  # system trust store does not trust the test CA
    with pytest.raises(AttachmentError, match="source_unavailable"):
        default.read_range(0, 10, io.BytesIO())


def test_default_policy_refuses_loopback_even_when_dns_says_so(server):
    port, cert, _ = server
    reader = source(port, cert)
    reader.allow = safe_fetch.address_allowed
    with pytest.raises(AttachmentError, match="source_unavailable"):
        reader.read_range(0, 10, io.BytesIO())
    assert Handler.seen == []


def test_token_bucket_paces_transfers():
    clock, slept = [0.0], []
    bucket = safe_fetch.TokenBucket(
        100, clock=lambda: clock[0], sleep=lambda s: slept.append(s)
    )
    bucket.take(100)
    bucket.take(50)
    assert slept == [0.5]
