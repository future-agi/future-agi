"""Address policy gaps in model endpoints: site-local IPv6 and link-local.

- Deprecated IPv6 site-local addresses (fec0::/10) are neither ``is_private``
  nor ``is_reserved`` to Python, so the classifier called them public and a
  hosted deployment connected to them. They are private now: hosted refuses
  them, a self-hosted install keeps reaching them like any LAN address.
- Self-hosted installs refuse link-local by default. An operator whose model
  host is only reachable on a link-local address (rootless Podman's pasta
  maps ``host.containers.internal`` to 169.254.1.2) can admit specific
  link-local ranges with CUSTOM_MODEL_ALLOWED_LINK_LOCAL_CIDRS. It is ignored
  on hosted and when CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS=true, and it never
  admits the cloud metadata ranges (169.254.169.0/24, 169.254.170.0/24) or
  names.
"""

import pytest
from django.test import override_settings

from tfc.utils import ssrf_guard
from tfc.utils.tests.guarded_post_fakes import FakeResolver, FakeTransport
from tfc.utils.tests.loopback_endpoints import clear_proxy_env

PAYLOAD = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
DENY_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"
LINK_LOCAL_ENV = "CUSTOM_MODEL_ALLOWED_LINK_LOCAL_CIDRS"


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    clear_proxy_env(monkeypatch)
    monkeypatch.delenv(DENY_ENV, raising=False)
    monkeypatch.delenv(LINK_LOCAL_ENV, raising=False)


@pytest.fixture
def resolver(monkeypatch):
    return FakeResolver().install(monkeypatch)


@pytest.fixture
def transport(monkeypatch):
    return FakeTransport().install(monkeypatch)


@pytest.fixture
def hosted():
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def self_hosted():
    with override_settings(CLOUD_DEPLOYMENT=""):
        yield


def _post(url):
    return ssrf_guard.guarded_post(url, json=PAYLOAD)


def _assert_refused(url, transport):
    with pytest.raises(ssrf_guard.EndpointBlocked) as excinfo:
        _post(url)
    assert excinfo.value.category == "blocked"
    assert transport.pools == []


# ------------------------------------------------------ IPv6 site-local ---


@pytest.mark.parametrize("address", ["fec0::123", "fec0::1", "feff::1", "fed0:1::5"])
def test_site_local_is_private_not_public(address):
    assert ssrf_guard.classify_ip(address) is ssrf_guard.IPClass.PRIVATE


def test_hosted_refuses_a_site_local_literal(hosted, resolver, transport):
    _assert_refused("http://[fec0::123]:8000/v1/chat/completions", transport)


def test_hosted_refuses_a_site_local_dns_answer(hosted, resolver, transport):
    resolver.add("site-local.example", "fec0::123")
    _assert_refused("https://site-local.example/v1/chat/completions", transport)


def test_hosted_refuses_a_name_with_one_site_local_answer(hosted, resolver, transport):
    resolver.add("mixed.example", "93.184.216.34", "fec0::123")
    _assert_refused("https://mixed.example/v1/chat/completions", transport)


def test_hosted_preflight_refuses_site_local(hosted, resolver):
    resolver.add("site-local.example", "fec0::123")
    with pytest.raises(ssrf_guard.EndpointBlocked):
        ssrf_guard.check_endpoint_url("https://site-local.example/v1")


def test_safe_fetch_classifier_refuses_site_local():
    with pytest.raises(ssrf_guard.SsrfBlocked):
        ssrf_guard._reject_unsafe_ip("fec0::123", "site-local.example")


def test_self_hosted_still_reaches_a_site_local_model(self_hosted, resolver, transport):
    resolver.add("lan-model.internal", "fec0::123")
    response = _post("http://lan-model.internal:8000/v1/chat/completions")
    assert response.status_code == 200
    assert [p["host"] for p in transport.pools] == ["fec0::123"]


def test_tightened_self_host_refuses_site_local(
    self_hosted, monkeypatch, resolver, transport
):
    monkeypatch.setenv(DENY_ENV, "true")
    _assert_refused("http://[fec0::123]:8000/v1/chat/completions", transport)


# ------------------------------------------------- link-local operator opt-in ---


def test_self_hosted_refuses_a_link_local_model_without_the_setting(
    self_hosted, resolver, transport
):
    resolver.add("host.containers.internal", "169.254.1.2")
    _assert_refused(
        "http://host.containers.internal:11434/v1/chat/completions", transport
    )


def test_self_hosted_setting_admits_the_listed_link_local_range(
    self_hosted, monkeypatch, resolver, transport
):
    monkeypatch.setenv(LINK_LOCAL_ENV, "169.254.1.0/24")
    resolver.add("host.containers.internal", "169.254.1.2")
    response = _post("http://host.containers.internal:11434/v1/chat/completions")
    assert response.status_code == 200
    assert [p["host"] for p in transport.pools] == ["169.254.1.2"]


def test_setting_admits_only_the_listed_range(
    self_hosted, monkeypatch, resolver, transport
):
    monkeypatch.setenv(LINK_LOCAL_ENV, "169.254.1.0/24")
    _assert_refused("http://169.254.2.1:11434/v1/chat/completions", transport)


def test_setting_admits_a_listed_ipv6_link_local_range(
    self_hosted, monkeypatch, resolver, transport
):
    monkeypatch.setenv(LINK_LOCAL_ENV, "fe80::/64")
    policy = ssrf_guard.EndpointPolicy.PRIVATE_ALLOWED
    assert ssrf_guard.endpoint_address_allowed("fe80::1", policy)
    assert not ssrf_guard.endpoint_address_allowed("fe80:0:0:1::1", policy)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.169.253/",
        "http://169.254.170.2/v2/credentials/",
        "http://169.254.170.23/v1/credentials",
        "http://[::ffff:169.254.169.254]/latest/meta-data/",
        "http://[64:ff9b::a9fe:a9fe]/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://[fd00:ec2::254]/latest/meta-data/",
    ],
)
def test_setting_never_admits_metadata(
    self_hosted, monkeypatch, resolver, transport, url
):
    monkeypatch.setenv(LINK_LOCAL_ENV, "169.254.0.0/16, fe80::/10")
    _assert_refused(url, transport)


def test_setting_is_ignored_on_hosted(hosted, monkeypatch, resolver, transport):
    monkeypatch.setenv(LINK_LOCAL_ENV, "169.254.0.0/16")
    _assert_refused("http://169.254.1.2:11434/v1/chat/completions", transport)


def test_setting_is_ignored_when_private_endpoints_are_denied(
    self_hosted, monkeypatch, resolver, transport
):
    monkeypatch.setenv(DENY_ENV, "true")
    monkeypatch.setenv(LINK_LOCAL_ENV, "169.254.1.0/24")
    _assert_refused("http://169.254.1.2:11434/v1/chat/completions", transport)


def test_setting_ignores_entries_that_are_not_link_local(
    hosted, monkeypatch, resolver, transport
):
    # A non-link-local range cannot widen anything, here or on hosted.
    monkeypatch.setenv(LINK_LOCAL_ENV, "10.0.0.0/8, 0.0.0.0/0, ::/0, not-a-cidr")
    _assert_refused("http://10.0.0.5/v1/chat/completions", transport)
    _assert_refused("http://169.254.1.2/v1/chat/completions", transport)


def test_setting_with_garbage_entries_keeps_the_valid_ones(
    self_hosted, monkeypatch, resolver, transport
):
    monkeypatch.setenv(LINK_LOCAL_ENV, "nonsense,,169.254.1.0/24;")
    response = _post("http://169.254.1.2:11434/v1/chat/completions")
    assert response.status_code == 200
