"""``guarded_post`` honours the operator's egress proxy, as requests did.

Before the guard, the custom-model validation and run-time POSTs used
``requests``, which sends through HTTP_PROXY / HTTPS_PROXY / ALL_PROXY unless
NO_PROXY matches. A self-hosted install whose only way out is that proxy
must keep working. The address policy still applies first: every DNS answer
for the target is checked before the proxy is contacted, so a hosted
deployment never asks its proxy for an internal address. (The proxy resolves
the name again; that window exists only behind an operator's own proxy.)

The proxy is a real recorder on 127.0.0.1 that forwards nothing.
"""

import base64

import pytest
from django.test import override_settings

from tfc.utils import ssrf_guard
from tfc.utils.tests.guarded_post_fakes import FakeResolver, FakeTransport
from tfc.utils.tests.loopback_endpoints import (
    MODEL_ANSWER,
    clear_proxy_env,
    recording_proxy,
)

PUBLIC_IP = "93.184.216.34"
PAYLOAD = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
DENY_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    clear_proxy_env(monkeypatch)
    monkeypatch.delenv(DENY_ENV, raising=False)


@pytest.fixture
def resolver(monkeypatch):
    resolver = FakeResolver().install(monkeypatch)
    resolver.add("public-model.example", PUBLIC_IP)
    resolver.add("internal-model.example", "10.0.0.5")
    return resolver


@pytest.fixture
def transport(monkeypatch):
    """Direct connections are recorded here; proxied ones never are."""
    return FakeTransport().install(monkeypatch)


@pytest.fixture
def proxy():
    with recording_proxy() as server:
        yield server


@pytest.fixture
def hosted():
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def self_hosted():
    with override_settings(CLOUD_DEPLOYMENT=""):
        yield


def _post(url, **kwargs):
    return ssrf_guard.guarded_post(url, json=PAYLOAD, **kwargs)


def test_http_endpoint_goes_through_http_proxy(
    hosted, monkeypatch, resolver, transport, proxy
):
    monkeypatch.setenv("HTTP_PROXY", proxy.base)
    response = _post(
        "http://public-model.example:8080/v1/chat/completions",
        headers={"Authorization": "Bearer tenant"},
    )
    assert response.status_code == 200
    assert MODEL_ANSWER in response.text
    assert transport.pools == []
    [seen] = proxy.requests
    assert seen.method == "POST"
    assert seen.target == "http://public-model.example:8080/v1/chat/completions"
    assert seen.headers["host"] == "public-model.example:8080"
    assert seen.headers["authorization"] == "Bearer tenant"


def test_lowercase_and_all_proxy_are_honoured(
    self_hosted, monkeypatch, resolver, transport, proxy
):
    monkeypatch.setenv("all_proxy", proxy.base)
    response = _post("http://public-model.example/v1/chat/completions")
    assert response.status_code == 200
    assert transport.pools == []
    assert proxy.requests[0].target == "http://public-model.example/v1/chat/completions"


def test_https_endpoint_is_tunnelled_by_host_name(
    hosted, monkeypatch, resolver, transport, proxy
):
    monkeypatch.setenv("HTTPS_PROXY", proxy.base)
    with pytest.raises(ssrf_guard.GuardedRequestError) as excinfo:
        _post("https://public-model.example/v1/chat/completions")
    # The recorder refuses the tunnel; what matters is that it was asked for
    # the host name (TLS then verifies that name), and nothing went direct.
    assert excinfo.value.category == "connect"
    assert transport.pools == []
    [seen] = proxy.requests
    assert seen.method == "CONNECT"
    assert seen.target == "public-model.example:443"


def test_proxy_credentials_become_proxy_authorization(
    self_hosted, monkeypatch, resolver, transport, proxy
):
    monkeypatch.setenv("HTTP_PROXY", f"http://ops:p%40ss@127.0.0.1:{proxy.port}")
    _post(
        "http://public-model.example/v1/chat/completions",
        headers={"Proxy-Authorization": "Basic dGVuYW50OnNlY3JldA=="},
    )
    expected = "Basic " + base64.b64encode(b"ops:p@ss").decode("ascii")
    assert proxy.requests[0].headers["proxy-authorization"] == expected


def test_no_proxy_match_connects_directly(
    hosted, monkeypatch, resolver, transport, proxy
):
    monkeypatch.setenv("HTTP_PROXY", proxy.base)
    monkeypatch.setenv("NO_PROXY", "localhost,.example")
    response = _post("http://public-model.example/v1/chat/completions")
    assert response.status_code == 200
    assert proxy.requests == []
    assert [p["host"] for p in transport.pools] == [PUBLIC_IP]


def test_hosted_internal_target_is_refused_before_the_proxy(
    hosted, monkeypatch, resolver, transport, proxy
):
    monkeypatch.setenv("HTTP_PROXY", proxy.base)
    monkeypatch.setenv("HTTPS_PROXY", proxy.base)
    for url in (
        "http://internal-model.example/v1/chat/completions",
        "https://internal-model.example/v1/chat/completions",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ):
        with pytest.raises(ssrf_guard.EndpointBlocked) as excinfo:
            _post(url)
        assert excinfo.value.category == "blocked"
    assert proxy.requests == []
    assert transport.pools == []


def test_self_hosted_metadata_is_refused_before_the_proxy(
    self_hosted, monkeypatch, resolver, transport, proxy
):
    monkeypatch.setenv("HTTP_PROXY", proxy.base)
    with pytest.raises(ssrf_guard.EndpointBlocked):
        _post("http://169.254.169.254/latest/meta-data/")
    assert proxy.requests == []


def test_hosted_unresolvable_name_is_refused_even_with_a_proxy(
    hosted, monkeypatch, resolver, transport, proxy
):
    monkeypatch.setenv("HTTP_PROXY", proxy.base)
    with pytest.raises(ssrf_guard.EndpointBlocked) as excinfo:
        _post("http://only-the-proxy-resolves.example/v1/chat/completions")
    assert excinfo.value.category == "unresolvable"
    assert proxy.requests == []


def test_self_hosted_name_only_the_proxy_resolves_still_works(
    self_hosted, monkeypatch, resolver, transport, proxy
):
    # Split-horizon egress: the pod cannot resolve public names, its proxy
    # can. requests worked here; the guard must too on a self-hosted install.
    monkeypatch.setenv("HTTP_PROXY", proxy.base)
    response = _post("http://only-the-proxy-resolves.example/v1/chat/completions")
    assert response.status_code == 200
    assert proxy.requests[0].target.startswith("http://only-the-proxy-resolves")


def test_proxied_redirect_is_not_followed(hosted, monkeypatch, resolver, transport):
    from tfc.utils.tests.loopback_endpoints import LoopbackServer

    with LoopbackServer(
        lambda request: (302, {"Location": "http://169.254.169.254/"}, b"")
    ) as redirecting_proxy:
        monkeypatch.setenv("HTTP_PROXY", redirecting_proxy.base)
        with pytest.raises(ssrf_guard.GuardedRequestError) as excinfo:
            _post("http://public-model.example/v1/chat/completions")
    assert excinfo.value.category == "redirect"
    assert len(redirecting_proxy.requests) == 1


def test_proxy_preflight_is_honoured_by_check_endpoint_url(
    self_hosted, monkeypatch, resolver
):
    # The litellm pre-flight follows the same rule: a name only the proxy can
    # resolve passes on a self-hosted install when a proxy will carry it.
    with pytest.raises(ssrf_guard.EndpointBlocked):
        ssrf_guard.check_endpoint_url("http://only-the-proxy-resolves.example/v1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:3128")
    ssrf_guard.check_endpoint_url("http://only-the-proxy-resolves.example/v1")
