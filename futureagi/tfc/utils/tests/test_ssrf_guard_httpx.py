"""The guarded httpx transport litellm's clients use for tenant base URLs.

An openai/azure custom model's ``api_base`` is called by litellm (the OpenAI
SDK's httpx client), not by ``guarded_post``. Its stock client follows
redirects and resolves the name itself, so a pre-flight check of the base
URL left a hosted deployment open to a public endpoint that redirects to an
internal address, and to DNS rebinding. ``guarded_httpx_client`` closes both:

- the connection is made by a network backend that resolves the host,
  checks every answer with the deployment's address policy and connects to a
  checked address (TLS still verifies the host name);
- a 3xx response is refused, never followed;
- metadata, forwarding and proxy headers are dropped and Host is the URL's;
- the operator's env proxy is honoured, after the same address check.

Real loopback servers only: ``public-a.test`` -> 127.0.0.1, treated as a
public address; ``[::1]`` stands in for the metadata server.
"""

import asyncio

import httpcore
import httpx
import pytest
from django.test import override_settings

from tfc.utils import ssrf_guard
from tfc.utils.tests.guarded_post_fakes import FakeResolver
from tfc.utils.tests.loopback_endpoints import (
    MODEL_ANSWER,
    PUBLIC_NAME,
    SECRET_MARKER,
    LoopbackServer,
    clear_proxy_env,
    metadata_stand_in,
    model_server,
    recording_proxy,
    redirector,
    treat_as_public,
)

DENY_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"
BODY = {"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    clear_proxy_env(monkeypatch)
    monkeypatch.delenv(DENY_ENV, raising=False)


@pytest.fixture
def resolver(monkeypatch):
    resolver = FakeResolver().install(monkeypatch)
    resolver.add(PUBLIC_NAME, "127.0.0.1")
    resolver.add("localhost", "127.0.0.1")
    resolver.add("internal-model.example", "10.0.0.5")
    return resolver


@pytest.fixture
def hosted(monkeypatch):
    treat_as_public(monkeypatch)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def self_hosted():
    with override_settings(CLOUD_DEPLOYMENT=""):
        yield


@pytest.fixture
def connects(monkeypatch):
    """Every TCP connection httpcore's sync backend opens, by address."""
    seen = []
    real = httpcore.SyncBackend.connect_tcp

    def connect_tcp(self, host, port, *args, **kwargs):
        seen.append(host)
        return real(self, host, port, *args, **kwargs)

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect_tcp)
    return seen


@pytest.fixture
def metadata():
    with metadata_stand_in() as server:
        yield server


def _post(url, **kwargs):
    with ssrf_guard.guarded_httpx_client() as client:
        return client.post(url, json=BODY, **kwargs)


def test_public_endpoint_works(hosted, resolver, connects):
    with model_server() as server:
        response = _post(f"http://{PUBLIC_NAME}:{server.port}/v1/chat/completions")
    assert response.status_code == 200
    assert MODEL_ANSWER in response.text
    assert connects == ["127.0.0.1"]
    assert server.requests[0].headers["host"] == f"{PUBLIC_NAME}:{server.port}"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_to_internal_is_refused_and_never_followed(
    hosted, resolver, metadata, status
):
    target = f"http://[::1]:{metadata.port}/latest/meta-data/iam/security-credentials/r"
    with redirector(target, status) as public:
        with pytest.raises(ssrf_guard.GuardedRequestError) as excinfo:
            _post(f"http://{PUBLIC_NAME}:{public.port}/v1/chat/completions")
    assert excinfo.value.category == "redirect"
    assert metadata.requests == []
    assert SECRET_MARKER not in str(excinfo.value)


def test_redirect_to_public_is_refused_too(hosted, resolver):
    with (
        model_server() as other,
        redirector(
            f"http://{PUBLIC_NAME}:{other.port}/v1/chat/completions", 307
        ) as public,
    ):
        with pytest.raises(ssrf_guard.GuardedRequestError):
            _post(f"http://{PUBLIC_NAME}:{public.port}/v1/chat/completions")
    assert other.requests == []


def test_follow_redirects_per_request_cannot_reopen_it(hosted, resolver, metadata):
    # The OpenAI SDK can pass follow_redirects per request; the transport
    # refuses the 3xx itself, so no second hop is ever made.
    target = f"http://[::1]:{metadata.port}/"
    with redirector(target, 307) as public:
        with ssrf_guard.guarded_httpx_client() as client:
            with pytest.raises(ssrf_guard.GuardedRequestError):
                client.post(
                    f"http://{PUBLIC_NAME}:{public.port}/v1",
                    json=BODY,
                    follow_redirects=True,
                )
    assert metadata.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "http://internal-model.example/v1/chat/completions",
        "http://10.0.0.5:8000/v1/chat/completions",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:169.254.169.254]/latest/meta-data/",
        "http://[64:ff9b::a9fe:a9fe]/latest/meta-data/",
        "http://[fec0::123]/v1/chat/completions",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://[::1]:9/v1",
    ],
)
def test_internal_target_is_refused_without_a_connection(
    hosted, resolver, connects, url
):
    with pytest.raises(ssrf_guard.EndpointBlocked) as excinfo:
        _post(url)
    assert excinfo.value.category == "blocked"
    assert connects == []


def test_dns_rebinding_after_the_preflight_is_refused(
    hosted, monkeypatch, resolver, connects, metadata
):
    # The pre-flight sees a public answer; the connection's own lookup sees
    # an internal one. The transport checks the answer it connects with.
    answers = [["127.0.0.1"], ["::1"]]

    def rebinding(host, port=None, *args, **kwargs):
        if host == "rebind.example":
            ips = answers.pop(0) if answers else ["::1"]
            return [
                (
                    (
                        ssrf_guard.socket.AF_INET6
                        if ":" in ip
                        else ssrf_guard.socket.AF_INET
                    ),
                    ssrf_guard.socket.SOCK_STREAM,
                    6,
                    "",
                    (ip, port or 0),
                )
                for ip in ips
            ]
        return resolver(host, port, *args, **kwargs)

    monkeypatch.setattr("tfc.utils.ssrf_guard.socket.getaddrinfo", rebinding)
    url = f"http://rebind.example:{metadata.port}/latest/meta-data/"
    ssrf_guard.check_endpoint_url(url)
    with pytest.raises(ssrf_guard.EndpointBlocked):
        _post(url)
    assert connects == []
    assert metadata.requests == []


def test_metadata_forwarding_and_proxy_headers_are_dropped(hosted, resolver):
    with model_server() as server:
        _post(
            f"http://{PUBLIC_NAME}:{server.port}/v1/chat/completions",
            headers={
                "Authorization": "Bearer tenant",
                "Metadata-Flavor": "Google",
                "X-Google-Metadata-Request": "True",
                "X-aws-ec2-metadata-token": "t",
                "X-aws-ec2-metadata-token-ttl-seconds": "21600",
                "Metadata": "true",
                "X-Forwarded-For": "169.254.169.254",
                "Forwarded": "for=10.0.0.1",
                "Proxy-Authorization": "Basic eA==",
                "Host": "metadata.google.internal",
                "X-Custom": "kept",
            },
        )
    seen = server.requests[0].headers
    for name in (
        "metadata-flavor",
        "x-google-metadata-request",
        "x-aws-ec2-metadata-token",
        "x-aws-ec2-metadata-token-ttl-seconds",
        "metadata",
        "x-forwarded-for",
        "forwarded",
        "proxy-authorization",
    ):
        assert name not in seen
    assert seen["host"] == f"{PUBLIC_NAME}:{server.port}"
    assert seen["authorization"] == "Bearer tenant"
    assert seen["x-custom"] == "kept"
    assert seen["content-length"]


def test_plain_http_public_endpoint_works_on_hosted(hosted, resolver):
    with model_server() as server:
        response = _post(f"http://{PUBLIC_NAME}:{server.port}/v1")
    assert response.status_code == 200


def test_self_hosted_localhost_model_works_without_a_setting(self_hosted, resolver):
    with model_server() as server:
        response = _post(f"http://localhost:{server.port}/v1/chat/completions")
    assert response.status_code == 200
    assert MODEL_ANSWER in response.text


def test_self_hosted_metadata_is_refused(self_hosted, resolver, connects):
    with pytest.raises(ssrf_guard.EndpointBlocked):
        _post("http://169.254.169.254/latest/meta-data/")
    assert connects == []


def test_tightened_self_host_refuses_localhost(self_hosted, monkeypatch, resolver):
    monkeypatch.setenv(DENY_ENV, "true")
    with model_server() as server:
        with pytest.raises(ssrf_guard.EndpointBlocked):
            _post(f"http://127.0.0.1:{server.port}/v1")
        assert server.requests == []


def test_async_client_pins_and_refuses_redirects(hosted, resolver, metadata):
    async def run(url):
        async with ssrf_guard.guarded_async_httpx_client() as client:
            return await client.post(url, json=BODY)

    with model_server() as server:
        response = asyncio.run(run(f"http://{PUBLIC_NAME}:{server.port}/v1"))
    assert response.status_code == 200
    with redirector(f"http://[::1]:{metadata.port}/", 302) as public:
        with pytest.raises(ssrf_guard.GuardedRequestError):
            asyncio.run(run(f"http://{PUBLIC_NAME}:{public.port}/v1"))
    with pytest.raises(ssrf_guard.EndpointBlocked):
        asyncio.run(run("http://internal-model.example/v1"))
    assert metadata.requests == []


def test_env_proxy_carries_a_public_endpoint(hosted, monkeypatch, resolver, connects):
    with recording_proxy() as proxy:
        monkeypatch.setenv("HTTP_PROXY", proxy.base)
        response = _post(f"http://{PUBLIC_NAME}:8080/v1/chat/completions")
    assert response.status_code == 200
    assert proxy.requests[0].target == f"http://{PUBLIC_NAME}:8080/v1/chat/completions"
    assert connects == ["127.0.0.1"]  # the proxy itself, nothing else


def test_env_proxy_is_not_asked_for_an_internal_target(hosted, monkeypatch, resolver):
    with recording_proxy() as proxy:
        monkeypatch.setenv("HTTP_PROXY", proxy.base)
        with pytest.raises(ssrf_guard.EndpointBlocked):
            _post("http://internal-model.example/v1/chat/completions")
        with pytest.raises(ssrf_guard.EndpointBlocked):
            _post("http://169.254.169.254/latest/meta-data/")
    assert proxy.requests == []


def test_proxied_redirect_is_refused(hosted, monkeypatch, resolver):
    with LoopbackServer(
        lambda request: (302, {"Location": "http://169.254.169.254/"}, b"")
    ) as proxy:
        monkeypatch.setenv("HTTP_PROXY", proxy.base)
        with pytest.raises(ssrf_guard.GuardedRequestError):
            _post(f"http://{PUBLIC_NAME}:8080/v1/chat/completions")
    assert len(proxy.requests) == 1


def test_no_proxy_match_goes_direct(hosted, monkeypatch, resolver, connects):
    with recording_proxy() as proxy, model_server() as server:
        monkeypatch.setenv("HTTP_PROXY", proxy.base)
        monkeypatch.setenv("NO_PROXY", PUBLIC_NAME)
        response = _post(f"http://{PUBLIC_NAME}:{server.port}/v1")
    assert response.status_code == 200
    assert proxy.requests == []
    assert len(server.requests) == 1


def test_guarded_client_is_an_httpx_client():
    with ssrf_guard.guarded_httpx_client() as client:
        assert isinstance(client, httpx.Client)
        assert client.follow_redirects is False
