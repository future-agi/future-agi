"""``guarded_post``: the pinned, policy-checked POST custom model endpoints use.

Hosted (CLOUD_DEPLOYMENT names a Future AGI Cloud region): only public
addresses. Self-hosted, by default: loopback and private networks work with
no setting (a model on localhost or the LAN, as in v1.56.1), while
link-local and the cloud metadata addresses and names are refused;
CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS=true tightens it to public only. Plain
http:// stays allowed everywhere. Every refusal happens before a connection,
redirects are never followed, metadata and forwarding headers are dropped,
the body is capped and errors are generic.

DNS and the transport are faked (``guarded_post_fakes``); the loopback tests
at the end use a real server on 127.0.0.1 and nothing else.
"""

import http.server
import json
import threading

import pytest
import urllib3
from django.test import override_settings

from tfc.utils import ssrf_guard
from tfc.utils.tests.guarded_post_fakes import (
    COMPLETION_BODY,
    JSON_HEADERS,
    FakeResolver,
    FakeResponse,
    FakeTransport,
)
from tfc.utils.tests.loopback_endpoints import clear_proxy_env

PUBLIC_IP = "93.184.216.34"
PAYLOAD = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
DENY_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"

# Addresses refused on hosted, by what they are.
HOSTED_REFUSED_ADDRESSES = [
    "127.0.0.1",
    "127.10.0.1",
    "10.0.0.5",
    "172.16.3.4",
    "192.168.1.10",
    "100.64.0.1",  # CGNAT
    "169.254.169.254",  # AWS/GCP/Azure metadata
    "169.254.170.2",  # ECS task metadata
    "0.0.0.0",
    "::1",
    "fe80::1",
    "fd00:ec2::254",
    "fd12:3456::1",
    "::ffff:169.254.169.254",  # IPv4-mapped
    "::ffff:10.0.0.1",
    "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
    "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
    "2002:a9fe:a9fe::1",  # 6to4 of 169.254.169.254
]

# Refused on every deployment, self-hosted included, with no setting.
ALWAYS_REFUSED_ADDRESSES = [
    "169.254.169.254",
    "169.254.170.2",
    "169.254.1.1",
    "fe80::1",
    "fd00:ec2::254",
    "fd20:ce::254",
    "100.100.100.200",
    "::ffff:169.254.169.254",
    "64:ff9b::a9fe:a9fe",
]

METADATA_URLS = [
    "http://metadata.google.internal/computeMetadata/v1/instance/",
    "http://METADATA.GOOGLE.INTERNAL./computeMetadata/v1/",
    "http://metadata/computeMetadata/v1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::ffff:a9fe:a9fe]/latest/meta-data/",
    "http://[fd00:ec2::254]/latest/meta-data/",
]


@pytest.fixture(autouse=True)
def _no_env_proxy(monkeypatch):
    # guarded_post honours HTTP(S)_PROXY; these tests use the fake transport.
    clear_proxy_env(monkeypatch)


@pytest.fixture
def resolver(monkeypatch):
    return FakeResolver().install(monkeypatch)


@pytest.fixture
def transport(monkeypatch):
    return FakeTransport().install(monkeypatch)


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.delenv(DENY_ENV, raising=False)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def self_hosted(monkeypatch):
    monkeypatch.delenv(DENY_ENV, raising=False)
    with override_settings(CLOUD_DEPLOYMENT=""):
        yield


@pytest.fixture
def self_hosted_tightened(monkeypatch):
    monkeypatch.setenv(DENY_ENV, "true")
    with override_settings(CLOUD_DEPLOYMENT=""):
        yield


def _post(url, **kwargs):
    return ssrf_guard.guarded_post(url, json=PAYLOAD, **kwargs)


def _assert_refused(url, category="blocked"):
    with pytest.raises(ssrf_guard.EndpointBlocked) as excinfo:
        _post(url)
    assert excinfo.value.category == category
    assert str(excinfo.value) == ssrf_guard.ENDPOINT_ERROR_MESSAGES[category]
    return excinfo.value


# --------------------------------------------------------------- policy ---


def test_policy_hosted_is_public_only(hosted):
    assert ssrf_guard.model_endpoint_policy() is ssrf_guard.EndpointPolicy.PUBLIC_ONLY


def test_policy_self_hosted_default_allows_private(self_hosted):
    assert (
        ssrf_guard.model_endpoint_policy() is ssrf_guard.EndpointPolicy.PRIVATE_ALLOWED
    )


def test_policy_self_hosted_setting_tightens(self_hosted_tightened):
    assert ssrf_guard.model_endpoint_policy() is ssrf_guard.EndpointPolicy.PUBLIC_ONLY


def test_policy_hosted_ignores_a_false_setting(monkeypatch):
    monkeypatch.setenv(DENY_ENV, "false")
    with override_settings(CLOUD_DEPLOYMENT="EU"):
        assert (
            ssrf_guard.model_endpoint_policy() is ssrf_guard.EndpointPolicy.PUBLIC_ONLY
        )


# --------------------------------------------------------------- hosted ---


@pytest.mark.parametrize("address", HOSTED_REFUSED_ADDRESSES)
def test_hosted_refuses_internal_answers_before_connecting(
    hosted, resolver, transport, address
):
    resolver.add("model.example.com", address)
    _assert_refused("https://model.example.com/v1/chat/completions")
    assert transport.pools == []
    assert transport.requests == []


@pytest.mark.parametrize("url", METADATA_URLS)
def test_hosted_refuses_metadata_names_and_literals_without_dns(
    hosted, resolver, transport, url
):
    _assert_refused(url)
    assert resolver.lookups == []
    assert transport.pools == []


def test_hosted_refuses_a_name_with_one_internal_answer(hosted, resolver, transport):
    resolver.add("mixed.example.com", PUBLIC_IP, "10.0.0.9")
    _assert_refused("https://mixed.example.com/v1/chat/completions")
    assert transport.pools == []


def test_hosted_refusal_names_no_host_or_address(hosted, resolver, transport):
    resolver.add("internal-svc.example.com", "10.20.30.40")
    error = _assert_refused("http://internal-svc.example.com:8123/?query=x")
    assert "10.20.30.40" not in str(error)
    assert "internal-svc" not in str(error)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_hosted_redirect_to_internal_is_not_followed(
    hosted, resolver, transport, status
):
    resolver.add("redirector.example.com", PUBLIC_IP)
    transport.respond(
        FakeResponse(
            status,
            {"Location": "http://169.254.169.254/computeMetadata/v1/"},
        )
    )
    with pytest.raises(ssrf_guard.GuardedRequestError) as excinfo:
        _post("https://redirector.example.com/v1/chat/completions")
    assert excinfo.value.category == "redirect"
    assert str(excinfo.value) == ssrf_guard.ENDPOINT_ERROR_MESSAGES["redirect"]
    # One connection, to the public pinned address; none to the Location.
    assert [p["host"] for p in transport.pools] == [PUBLIC_IP]
    assert len(transport.requests) == 1
    assert transport.requests[0]["redirect"] is False


def test_hosted_plain_http_public_endpoint_works(hosted, resolver, transport):
    resolver.add("api.example.com", PUBLIC_IP)
    response = _post("http://api.example.com/v1/chat/completions")
    assert response.status_code == 200
    assert response.text == COMPLETION_BODY.decode()
    assert transport.pools[0]["scheme"] == "http"
    assert transport.pools[0]["host"] == PUBLIC_IP
    assert transport.pools[0]["port"] == 80
    sent = transport.requests[0]
    assert sent["method"] == "POST"
    assert sent["url"] == "/v1/chat/completions"
    assert json.loads(sent["body"]) == PAYLOAD
    assert transport.sent_headers["host"] == "api.example.com"
    assert transport.sent_headers["content-type"] == "application/json"


def test_hosted_https_connects_to_the_pinned_address_with_sni(
    hosted, resolver, transport
):
    resolver.add("api.example.com", PUBLIC_IP)
    _post("https://api.example.com:8443/v1/chat/completions?x=1")
    pool = transport.pools[0]
    assert pool["scheme"] == "https"
    assert pool["host"] == PUBLIC_IP
    assert pool["port"] == 8443
    assert pool["server_hostname"] == "api.example.com"
    assert pool["assert_hostname"] == "api.example.com"
    assert pool["cert_reqs"] == "CERT_REQUIRED"
    assert transport.requests[0]["url"] == "/v1/chat/completions?x=1"
    assert transport.sent_headers["host"] == "api.example.com:8443"


def test_metadata_and_forwarding_headers_are_dropped(hosted, resolver, transport):
    resolver.add("api.example.com", PUBLIC_IP)
    _post(
        "https://api.example.com/v1/chat/completions",
        headers={
            "Authorization": "Bearer tenant-token",
            "X-Api-Key": "tenant-key",
            "Host": "metadata.google.internal",
            "Metadata-Flavor": "Google",
            "X-Google-Metadata-Request": "True",
            "X-aws-ec2-metadata-token": "t",
            "X-aws-ec2-metadata-token-ttl-seconds": "21600",
            "X-Forwarded-For": "127.0.0.1",
            "X-Forwarded-Host": "internal",
            "Forwarded": "for=127.0.0.1",
            "Proxy-Authorization": "Basic x",
            "X-Scope-OrgID": "tenant-b",
            "X-ClickHouse-User": "default",
        },
    )
    sent = transport.sent_headers
    assert sent["authorization"] == "Bearer tenant-token"
    assert sent["x-api-key"] == "tenant-key"
    assert sent["host"] == "api.example.com"
    for dropped in (
        "metadata-flavor",
        "x-google-metadata-request",
        "x-aws-ec2-metadata-token",
        "x-aws-ec2-metadata-token-ttl-seconds",
        "x-forwarded-for",
        "x-forwarded-host",
        "forwarded",
        "proxy-authorization",
        "x-scope-orgid",
        "x-clickhouse-user",
    ):
        assert dropped not in sent


def test_body_over_the_cap_is_refused_without_echo(hosted, resolver, transport):
    resolver.add("api.example.com", PUBLIC_IP)
    transport.respond(FakeResponse(200, JSON_HEADERS, b"S" * 4096 + b"secret-body"))
    with pytest.raises(ssrf_guard.GuardedRequestError) as excinfo:
        _post("https://api.example.com/v1/chat/completions", max_bytes=1024)
    assert excinfo.value.category == "too_large"
    assert "secret-body" not in str(excinfo.value)


def test_default_cap_is_two_megabytes():
    assert ssrf_guard.MODEL_ENDPOINT_MAX_RESPONSE_BYTES == 2 * 1024 * 1024


@pytest.mark.parametrize(
    "error, category",
    [
        (
            urllib3.exceptions.NewConnectionError(None, "refused 10.1.1.1:443"),
            "connect",
        ),
        (
            urllib3.exceptions.ConnectTimeoutError(None, "timed out 10.1.1.1"),
            "timeout",
        ),
        (
            urllib3.exceptions.ReadTimeoutError(None, "/x", "read timed out"),
            "timeout",
        ),
        (urllib3.exceptions.SSLError("certificate verify failed"), "tls"),
        (urllib3.exceptions.ProtocolError("Connection aborted."), "bad_response"),
    ],
)
def test_transport_errors_are_generic(hosted, resolver, transport, error, category):
    resolver.add("api.example.com", PUBLIC_IP)
    transport.fail_with(error)
    with pytest.raises(ssrf_guard.GuardedRequestError) as excinfo:
        _post("https://api.example.com/v1/chat/completions")
    assert excinfo.value.category == category
    assert str(excinfo.value) == ssrf_guard.ENDPOINT_ERROR_MESSAGES[category]
    assert "10.1.1.1" not in str(excinfo.value)


def test_second_checked_address_is_tried_when_the_first_cannot_connect(
    hosted, resolver, transport
):
    # A dual-stack name seen from a container without IPv6.
    resolver.add("dual.example.com", "2606:2800:220:1:248:1893:25c8:1946", PUBLIC_IP)
    transport.respond(
        urllib3.exceptions.NewConnectionError(None, "Network is unreachable"),
        FakeResponse(200, JSON_HEADERS, COMPLETION_BODY),
    )
    response = _post("https://dual.example.com/v1/chat/completions")
    assert response.status_code == 200
    assert [p["host"] for p in transport.pools] == [
        "2606:2800:220:1:248:1893:25c8:1946",
        PUBLIC_IP,
    ]


def test_a_dual_stack_name_with_one_internal_answer_is_refused(
    hosted, resolver, transport
):
    resolver.add("dual.example.com", "fd00:ec2::254", PUBLIC_IP)
    _assert_refused("https://dual.example.com/v1/chat/completions")
    assert transport.pools == []


def test_error_status_returns_without_reading_the_body(hosted, resolver, transport):
    resolver.add("api.example.com", PUBLIC_IP)
    transport.respond(FakeResponse(500, JSON_HEADERS, b"internal stack trace"))
    response = _post("https://api.example.com/v1/chat/completions")
    assert response.status_code == 500
    assert response.content == b""


@pytest.mark.parametrize(
    "url",
    [
        "ftp://api.example.com/x",
        "file:///etc/passwd",
        "http:///no-host",
        "http://api.example.com/a b",
        "http://api.example.com/\r\nX-Injected: 1",
        "",
        None,
    ],
)
def test_invalid_urls_are_refused(hosted, resolver, transport, url):
    with pytest.raises(ssrf_guard.EndpointBlocked) as excinfo:
        ssrf_guard.guarded_post(url, json=PAYLOAD)
    assert excinfo.value.category == "invalid_url"
    assert transport.pools == []


def test_unresolvable_host_is_refused_generically(hosted, resolver, transport):
    _assert_refused("https://nowhere.example.com/v1", category="unresolvable")
    assert transport.pools == []


def test_check_endpoint_url_preflight(hosted, resolver):
    resolver.add("lan.example.com", "10.0.0.1")
    resolver.add("api.example.com", PUBLIC_IP)
    with pytest.raises(ssrf_guard.EndpointBlocked):
        ssrf_guard.check_endpoint_url("https://lan.example.com/v1")
    with pytest.raises(ssrf_guard.EndpointBlocked):
        ssrf_guard.check_endpoint_url("http://169.254.169.254/")
    ssrf_guard.check_endpoint_url("http://api.example.com/v1")
    # Saving tolerates a name this container cannot resolve; calling does not.
    ssrf_guard.check_endpoint_url(
        "https://nowhere.example.com/v1", allow_unresolvable=True
    )
    with pytest.raises(ssrf_guard.EndpointBlocked):
        ssrf_guard.check_endpoint_url("https://nowhere.example.com/v1")


# ------------------------------------------------------------ self-host ---


@pytest.mark.parametrize(
    "host, address",
    [
        ("localhost", "127.0.0.1"),
        ("localhost", "::1"),
        ("gpu-box.lan", "192.168.1.20"),
        ("vllm", "172.18.0.4"),  # a Docker service name
        ("ollama.internal", "10.0.0.7"),
        ("tailnet-model", "100.101.102.103"),
    ],
)
def test_self_hosted_default_keeps_local_and_private_models_working(
    self_hosted, resolver, transport, host, address
):
    resolver.add(host, address)
    response = _post(f"http://{host}:8000/v1/chat/completions")
    assert response.status_code == 200
    assert transport.pools[0]["host"] == address
    assert transport.sent_headers["host"] == f"{host}:8000"


def test_self_hosted_default_allows_a_private_literal(self_hosted, resolver, transport):
    response = _post("http://10.0.0.7:11434/v1/chat/completions")
    assert response.status_code == 200
    assert transport.pools[0]["host"] == "10.0.0.7"


@pytest.mark.parametrize("address", ALWAYS_REFUSED_ADDRESSES)
def test_self_hosted_default_refuses_link_local_and_metadata(
    self_hosted, resolver, transport, address
):
    resolver.add("model.example.com", address)
    _assert_refused("http://model.example.com/v1/chat/completions")
    assert transport.pools == []


@pytest.mark.parametrize("url", METADATA_URLS)
def test_self_hosted_default_refuses_metadata_names(
    self_hosted, resolver, transport, url
):
    _assert_refused(url)
    assert transport.pools == []


@pytest.mark.parametrize(
    "host, address",
    [("localhost", "127.0.0.1"), ("gpu-box.lan", "192.168.1.20")],
)
def test_self_hosted_setting_refuses_private(
    self_hosted_tightened, resolver, transport, host, address
):
    resolver.add(host, address)
    _assert_refused(f"http://{host}:8000/v1/chat/completions")
    assert transport.pools == []


def test_self_hosted_setting_still_allows_public(
    self_hosted_tightened, resolver, transport
):
    resolver.add("api.example.com", PUBLIC_IP)
    assert _post("http://api.example.com/v1/chat/completions").status_code == 200


# ------------------------------------------------- real loopback server ---


class _ModelServer:
    """An HTTP server on 127.0.0.1 that records what it was sent."""

    def __init__(self, status=200, headers=None, body=COMPLETION_BODY):
        self.seen = []
        server = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                server.seen.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": self.rfile.read(length),
                    }
                )
                self.send_response(status)
                for name, value in (headers or JSON_HEADERS).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_loopback_model_works_end_to_end_on_self_hosted(self_hosted, resolver):
    resolver.add("local-model.internal", "127.0.0.1")
    with _ModelServer() as server:
        response = ssrf_guard.guarded_post(
            f"http://local-model.internal:{server.port}/v1/chat/completions",
            json=PAYLOAD,
            headers={"Authorization": "Bearer k", "Metadata-Flavor": "Google"},
            timeout=5,
        )
    assert response.status_code == 200
    assert response.text == COMPLETION_BODY.decode()
    (seen,) = server.seen
    assert seen["path"] == "/v1/chat/completions"
    assert json.loads(seen["body"]) == PAYLOAD
    assert seen["headers"]["host"] == f"local-model.internal:{server.port}"
    assert seen["headers"]["authorization"] == "Bearer k"
    assert "metadata-flavor" not in seen["headers"]


def test_loopback_redirect_is_not_followed(self_hosted, resolver):
    resolver.add("local-model.internal", "127.0.0.1")
    with _ModelServer(
        status=307, headers={"Location": "http://169.254.169.254/"}, body=b""
    ) as server:
        with pytest.raises(ssrf_guard.GuardedRequestError) as excinfo:
            ssrf_guard.guarded_post(
                f"http://local-model.internal:{server.port}/v1/chat/completions",
                json=PAYLOAD,
                timeout=5,
            )
    assert excinfo.value.category == "redirect"
    assert len(server.seen) == 1


def test_loopback_body_cap(self_hosted, resolver):
    resolver.add("local-model.internal", "127.0.0.1")
    with _ModelServer(body=b"x" * (3 * 1024 * 1024)) as server:
        with pytest.raises(ssrf_guard.GuardedRequestError) as excinfo:
            ssrf_guard.guarded_post(
                f"http://local-model.internal:{server.port}/v1/chat/completions",
                json=PAYLOAD,
                timeout=5,
            )
    assert excinfo.value.category == "too_large"


def test_loopback_refused_on_hosted_before_connecting(hosted, resolver):
    resolver.add("local-model.internal", "127.0.0.1")
    with _ModelServer() as server:
        with pytest.raises(ssrf_guard.EndpointBlocked):
            ssrf_guard.guarded_post(
                f"http://local-model.internal:{server.port}/v1/chat/completions",
                json=PAYLOAD,
                timeout=5,
            )
    assert server.seen == []
