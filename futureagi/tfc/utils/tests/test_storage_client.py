import base64
import datetime
import http.client
import importlib
import json
import select
import socket
import ssl
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
import urllib3
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from minio import Minio

PROXY_ENV_KEYS = tuple(
    name
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
    for name in (key, key.lower())
)

STORAGE_ENV_KEYS = (
    "STORAGE_BACKEND",
    "S3_ENDPOINT",
    "S3_ENDPOINT_URL",
    "S3_SECURE",
    "S3_REGION",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "MINIO_URL",
    "MINIO_REGION",
    "GCS_HMAC_ACCESS_KEY",
    "GCS_HMAC_SECRET_KEY",
    "AWS_DEFAULT_REGION",
    *PROXY_ENV_KEYS,
)


def reload_storage_client(monkeypatch, **env):
    for key in STORAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    sys.modules.pop("tfc.utils.storage_client", None)
    return importlib.import_module("tfc.utils.storage_client")


def test_minio_object_url_uses_public_minio_url(monkeypatch):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="minio",
        S3_ENDPOINT_URL="http://minio:9000",
        MINIO_URL="http://localhost:9005",
    )

    assert (
        storage_client.get_object_url("futureagi", "tempcust/image.png")
        == "http://localhost:9005/futureagi/tempcust/image.png"
    )


def test_minio_object_url_falls_back_to_default_when_minio_url_unset(monkeypatch):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="minio",
        S3_ENDPOINT_URL="http://minio:9000",
    )

    assert (
        storage_client.get_object_url("futureagi", "tempcust/image.png")
        == "http://localhost:9005/futureagi/tempcust/image.png"
    )


def test_s3_object_url_ignores_minio_url(monkeypatch):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="s3",
        MINIO_URL="http://localhost:9005",
        AWS_DEFAULT_REGION="us-east-1",
    )

    assert (
        storage_client.get_object_url("futureagi", "tempcust/image.png")
        == "https://futureagi.s3.us-east-1.amazonaws.com/tempcust/image.png"
    )


def test_gcs_object_url_ignores_s3_and_minio_urls(monkeypatch):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="gcs",
        S3_ENDPOINT_URL="http://minio:9000",
        MINIO_URL="http://localhost:9005",
    )

    assert (
        storage_client.get_object_url("futureagi", "tempcust/image.png")
        == "https://storage.googleapis.com/futureagi/tempcust/image.png"
    )


def test_gcs_client_uses_minio_region(monkeypatch):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="gcs",
        MINIO_REGION="europe-west3",
        GCS_HMAC_ACCESS_KEY="access",
        GCS_HMAC_SECRET_KEY="secret",
    )
    captured = {}

    def fake_minio(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(storage_client, "Minio", fake_minio)
    storage_client.reset_storage_client()
    storage_client.get_storage_client()

    assert captured["args"][0] == "storage.googleapis.com"
    assert captured["kwargs"]["region"] == "europe-west3"


def test_gcs_client_falls_back_to_auto_region(monkeypatch):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="gcs",
        GCS_HMAC_ACCESS_KEY="access",
        GCS_HMAC_SECRET_KEY="secret",
    )
    captured = {}

    def fake_minio(*args, **kwargs):
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(storage_client, "Minio", fake_minio)
    storage_client.reset_storage_client()
    storage_client.get_storage_client()

    assert captured["kwargs"]["region"] == "auto"


PROXY = "http://proxy.example.com:3128"
S3 = "https://s3.example.com"
MINIO = "http://minio.example.com:9000"


@pytest.fixture
def pools(monkeypatch):
    """Each connection pool asked to send a request, with the URL it was given.
    Nothing reaches the network: every request gets an empty 200."""
    seen = []

    def urlopen(pool, method, url, **kw):
        seen.append((pool, url))
        return urllib3.HTTPResponse(body=b"", status=200)

    monkeypatch.setattr(urllib3.HTTPConnectionPool, "urlopen", urlopen)
    return seen


def _send(pools, client):
    """The pool that sent client's bucket_exists request, and the URL it got."""
    assert client.bucket_exists("futureagi")
    ((pool, url),) = pools
    pools.clear()
    return pool, url


def _route(pools, monkeypatch, **env):
    """(host, proxy) of a storage request: the host it is for, and the proxy it
    goes through, or None when it connects directly."""
    storage_client = reload_storage_client(monkeypatch, **env)
    pool, url = _send(pools, storage_client.get_storage_client())
    # Only a request forwarded through a proxy carries its host in the URL; the
    # others are sent to a pool for their host.
    return urlsplit(url).hostname or pool.host, pool.proxy and pool.proxy.url


@pytest.mark.parametrize(
    ("endpoint", "proxy_env", "expected"),
    [
        # The endpoint's scheme picks the variable, in either case; ALL_PROXY
        # is the fallback.
        (S3, {"HTTPS_PROXY": PROXY}, PROXY),
        (S3, {"https_proxy": PROXY}, PROXY),
        (MINIO, {"HTTP_PROXY": PROXY}, PROXY),
        (S3, {"HTTP_PROXY": PROXY}, None),
        (S3, {"ALL_PROXY": PROXY}, PROXY),
        # A proxy given without a scheme is an HTTP proxy.
        (S3, {"HTTPS_PROXY": "proxy.example.com:3128"}, PROXY),
        # NO_PROXY: exact host, domain suffix with and without the dot, "*".
        (S3, {"HTTPS_PROXY": PROXY, "NO_PROXY": "s3.example.com"}, None),
        (S3, {"HTTPS_PROXY": PROXY, "no_proxy": ".example.com"}, None),
        (S3, {"HTTPS_PROXY": PROXY, "NO_PROXY": "example.com"}, None),
        (S3, {"HTTPS_PROXY": PROXY, "NO_PROXY": "*"}, None),
        (S3, {"HTTPS_PROXY": PROXY, "NO_PROXY": "a.test, s3.example.org"}, PROXY),
        # host:port only matches that port.
        (MINIO, {"HTTP_PROXY": PROXY, "NO_PROXY": "minio.example.com:9000"}, None),
        (MINIO, {"HTTP_PROXY": PROXY, "NO_PROXY": "minio.example.com:9001"}, PROXY),
        # CIDR ranges match IPv4 endpoints.
        ("http://10.1.2.3:9000", {"HTTP_PROXY": PROXY, "NO_PROXY": "10.0.0.0/8"}, None),
        (
            "http://11.1.2.3:9000",
            {"HTTP_PROXY": PROXY, "NO_PROXY": "10.0.0.0/8"},
            PROXY,
        ),
        # Loopback is always direct, whatever NO_PROXY says: no proxy can reach
        # this machine's loopback, where Standalone runs its MinIO.
        ("http://127.0.0.1:9005", {"HTTP_PROXY": PROXY, "NO_PROXY": "localhost"}, None),
        ("http://127.1.2.3:9005", {"HTTP_PROXY": PROXY}, None),
        ("http://localhost:9005", {"HTTP_PROXY": PROXY}, None),
        ("http://[::1]:9005", {"HTTP_PROXY": PROXY}, None),
    ],
)
def test_storage_client_picks_the_proxy_for_its_endpoint(
    pools, monkeypatch, endpoint, proxy_env, expected
):
    _, proxy = _route(
        pools, monkeypatch, STORAGE_BACKEND="s3", S3_ENDPOINT_URL=endpoint, **proxy_env
    )
    assert proxy == expected


@pytest.mark.parametrize(
    ("no_proxy", "expected"),
    [
        ("s3.us-east-1.amazonaws.com", None),
        (".amazonaws.com", None),
        # Names only the configured endpoint, which Minio does not connect to.
        ("s3.amazonaws.com", PROXY),
    ],
)
def test_aws_requests_follow_no_proxy_for_the_host_they_go_to(
    pools, monkeypatch, no_proxy, expected
):
    # With no endpoint configured, Minio sends AWS requests to the bucket's
    # regional virtual host, not to s3.amazonaws.com, so NO_PROXY is matched
    # against that host, as requests would match it.
    route = _route(
        pools,
        monkeypatch,
        STORAGE_BACKEND="s3",
        S3_REGION="us-east-1",
        HTTPS_PROXY=PROXY,
        NO_PROXY=no_proxy,
    )
    assert route == ("futureagi.s3.us-east-1.amazonaws.com", expected)


def test_endpoint_without_a_scheme_uses_the_proxy_for_its_derived_scheme(
    pools, monkeypatch
):
    # The bundled MinIO is plain HTTP, so HTTPS_PROXY alone must not catch it.
    env = {"STORAGE_BACKEND": "minio", "S3_ENDPOINT": "minio.example.com:9000"}
    assert _route(pools, monkeypatch, **env, HTTPS_PROXY=PROXY)[1] is None
    assert _route(pools, monkeypatch, **env, HTTP_PROXY=PROXY)[1] == PROXY


def test_gcs_client_uses_the_https_proxy(pools, monkeypatch):
    env = {"STORAGE_BACKEND": "gcs", "HTTPS_PROXY": PROXY}
    assert _route(pools, monkeypatch, **env) == ("storage.googleapis.com", PROXY)
    no_proxy = {**env, "NO_PROXY": "storage.googleapis.com"}
    assert _route(pools, monkeypatch, **no_proxy) == ("storage.googleapis.com", None)


def test_storage_requests_keep_minios_own_transport_settings(
    pools, monkeypatch, tmp_path
):
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "ca.pem"))

    def settings(pool):
        timeout, retries = pool.timeout, pool.retries
        return (
            (timeout.connect_timeout, timeout.read_timeout, pool.pool.maxsize),
            (pool.cert_reqs, pool.ca_certs),
            (retries.total, retries.backoff_factor, retries.status_forcelist),
        )

    minios_own, _ = _send(pools, Minio("s3.example.com"))
    for no_proxy, proxy in (("", PROXY), ("s3.example.com", None)):
        storage_client = reload_storage_client(
            monkeypatch,
            STORAGE_BACKEND="s3",
            S3_ENDPOINT_URL=S3,
            HTTPS_PROXY=PROXY,
            NO_PROXY=no_proxy,
        )
        pool, _ = _send(pools, storage_client.get_storage_client())
        assert (pool.proxy and pool.proxy.url) == proxy
        assert settings(pool) == settings(minios_own)


class _ObjectStore(BaseHTTPRequestHandler):
    """Has every bucket: answers bucket_exists' HEAD with 200 and records the path."""

    def do_HEAD(self):
        self.server.seen.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


# Never resolves, so a storage request for it only arrives through the proxy.
STORE_HOST = "storage.test"


class _ForwardProxy(_ObjectStore):
    """Relays plain-HTTP requests and tunnels CONNECT to STORE_HOST, which only
    it can reach (at 127.0.0.1), recording each request line with its
    Proxy-Authorization."""

    def do_HEAD(self):
        self.server.seen.append(
            (f"HEAD {self.path}", self.headers.get("Proxy-Authorization"))
        )
        target = urlsplit(self.path)
        upstream = http.client.HTTPConnection("127.0.0.1", target.port, timeout=5)
        upstream.request("HEAD", target.path)
        status = upstream.getresponse().status
        upstream.close()
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_CONNECT(self):
        self.server.seen.append(
            (f"CONNECT {self.path}", self.headers.get("Proxy-Authorization"))
        )
        _, port = self.path.rsplit(":", 1)
        self.close_connection = True
        with socket.create_connection(("127.0.0.1", int(port)), timeout=5) as upstream:
            self.send_response(200)
            self.end_headers()
            peer = {self.connection: upstream, upstream: self.connection}
            while True:
                readable, _, _ = select.select(list(peer), [], [], 5)
                if not readable:
                    return
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    peer[sock].sendall(data)


@contextmanager
def _serve(handler, tls=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if tls:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.seen = []
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _tls_trusted_through_ssl_cert_file(monkeypatch, tmp_path):
    """A server context whose self-signed STORE_HOST certificate is trusted only
    through SSL_CERT_FILE, the variable the chart's caBundle sets."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "storage proxy test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(STORE_HOST)]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context


def _live_storage_client(monkeypatch, endpoint, **proxy_env):
    storage_client = reload_storage_client(
        monkeypatch,
        STORAGE_BACKEND="s3",
        S3_ENDPOINT_URL=endpoint,
        S3_REGION="us-east-1",
        S3_ACCESS_KEY="test-access",
        S3_SECRET_KEY="test-secret",
        **proxy_env,
    )
    return storage_client.get_storage_client()


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_storage_requests_go_through_the_proxy(monkeypatch, tmp_path, scheme):
    tls = (
        _tls_trusted_through_ssl_cert_file(monkeypatch, tmp_path)
        if scheme == "https"
        else None
    )
    with _serve(_ObjectStore, tls) as store, _serve(_ForwardProxy) as proxy:
        host = f"{STORE_HOST}:{store.server_port}"
        proxy_url = f"http://storage:p%40ss@127.0.0.1:{proxy.server_port}"
        client = _live_storage_client(
            monkeypatch, f"{scheme}://{host}", **{f"{scheme.upper()}_PROXY": proxy_url}
        )
        assert client.bucket_exists("futureagi")

    # HTTPS is tunnelled (CONNECT), so the store's certificate is still checked
    # against SSL_CERT_FILE; plain HTTP is forwarded in absolute form.
    request_line = (
        f"CONNECT {host}" if scheme == "https" else f"HEAD http://{host}/futureagi"
    )
    credentials = base64.b64encode(b"storage:p@ss").decode()
    assert proxy.seen == [(request_line, f"Basic {credentials}")]
    assert store.seen == ["/futureagi"]


@pytest.mark.parametrize("store_host", ["127.0.0.1", "localhost"])
def test_loopback_storage_is_reached_directly_despite_the_proxy(
    monkeypatch, store_host
):
    # Standalone's .env may set HTTP_PROXY without listing its own MinIO in
    # NO_PROXY; storage must keep working there.
    with _serve(_ObjectStore) as store, _serve(_ForwardProxy) as proxy:
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        client = _live_storage_client(
            monkeypatch,
            f"http://{store_host}:{store.server_port}",
            HTTP_PROXY=proxy_url,
            HTTPS_PROXY=proxy_url,
        )
        assert client.bucket_exists("futureagi")

    assert proxy.seen == []
    assert store.seen == ["/futureagi"]


class FakeBucketClient:
    """Records bucket calls; holds one bucket's policy like MinIO does."""

    def __init__(self, exists=False, policy=None, policy_error=None, set_error=None):
        self.exists = exists
        self.policy = policy
        self.policy_error = policy_error
        self.set_error = set_error
        self.set_calls = []
        self.get_calls = 0

    def bucket_exists(self, bucket_name):
        return self.exists

    def make_bucket(self, bucket_name):
        self.exists = True

    def get_bucket_policy(self, bucket_name):
        self.get_calls += 1
        if self.policy_error:
            raise self.policy_error
        return self.policy

    def set_bucket_policy(self, bucket_name, policy):
        if self.set_error:
            raise self.set_error
        self.set_calls.append(json.loads(policy))
        self.policy = policy


def _anonymous_statements(policy):
    return [
        s
        for s in policy["Statement"]
        if s["Effect"] == "Allow" and s["Principal"] in ("*", {"AWS": ["*"]})
    ]


def _legacy_public_policy(bucket="futureagi", principal="*"):
    """What ensure_bucket applied before: every action for anyone."""
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": principal,
                    "Action": "s3:*",
                    "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
                }
            ],
        }
    )


def test_new_bucket_is_readable_by_url_but_not_writable_or_listable(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    client = FakeBucketClient(exists=False)

    storage_client.ensure_bucket(client, "futureagi")

    (policy,) = client.set_calls
    (statement,) = _anonymous_statements(policy)
    assert statement["Action"] == ["s3:GetObject"]
    assert statement["Resource"] == ["arn:aws:s3:::futureagi/*"]


def test_existing_minio_bucket_with_the_old_policy_is_cut_back_to_reads(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    client = FakeBucketClient(
        exists=True, policy=_legacy_public_policy(principal={"AWS": ["*"]})
    )

    storage_client.ensure_bucket(client, "futureagi")

    (policy,) = client.set_calls
    (statement,) = _anonymous_statements(policy)
    assert statement["Action"] == ["s3:GetObject"]
    assert statement["Resource"] == ["arn:aws:s3:::futureagi/*"]


def test_tightening_keeps_statements_for_named_principals(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    named = {
        "Effect": "Allow",
        "Principal": {"AWS": ["arn:aws:iam::123456789012:user/backup"]},
        "Action": ["s3:ListBucket"],
        "Resource": ["arn:aws:s3:::futureagi"],
    }
    legacy = json.loads(_legacy_public_policy())
    legacy["Statement"].append(named)
    client = FakeBucketClient(exists=True, policy=json.dumps(legacy))

    storage_client.ensure_bucket(client, "futureagi")

    (policy,) = client.set_calls
    assert named in policy["Statement"]
    assert [s["Action"] for s in _anonymous_statements(policy)] == [["s3:GetObject"]]


def test_read_only_bucket_is_left_alone(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    read_only = {
        "Version": "2012-10-17",
        "Statement": [storage_client._anonymous_read_statement("futureagi")],
    }
    client = FakeBucketClient(exists=True, policy=json.dumps(read_only))

    storage_client.ensure_bucket(client, "futureagi")

    assert client.set_calls == []


def test_existing_bucket_policy_is_checked_once_per_process(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    client = FakeBucketClient(exists=True, policy=_legacy_public_policy())

    storage_client.ensure_bucket(client, "futureagi")
    storage_client.ensure_bucket(client, "futureagi")

    assert client.get_calls == 1
    assert len(client.set_calls) == 1


def test_operator_owned_s3_bucket_policy_is_not_touched(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="s3")
    client = FakeBucketClient(exists=True, policy=_legacy_public_policy())

    storage_client.ensure_bucket(client, "futureagi")

    assert client.get_calls == 0
    assert client.set_calls == []


def test_unreadable_or_missing_policy_does_not_fail_the_upload(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    client = FakeBucketClient(
        exists=True, policy_error=RuntimeError("NoSuchBucketPolicy")
    )

    storage_client.ensure_bucket(client, "futureagi")

    assert client.set_calls == []


def test_failed_tightening_does_not_fail_the_upload(monkeypatch):
    storage_client = reload_storage_client(monkeypatch, STORAGE_BACKEND="minio")
    client = FakeBucketClient(
        exists=True,
        policy=_legacy_public_policy(),
        set_error=RuntimeError("AccessDenied"),
    )

    storage_client.ensure_bucket(client, "futureagi")

    assert client.get_calls == 1
