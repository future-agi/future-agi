"""Which provider base URLs model discovery may call, with and without the
AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS opt-in."""

import json
from pathlib import Path

import pytest

from agentcc.services.url_safety import (
    ALLOW_PRIVATE_PROVIDER_URLS_ENV,
    PROVIDER_PRIVATE_URL_ERROR,
    PROVIDER_URL_ERROR,
    WEBHOOK_PRIVATE_URL_ERROR,
    ensure_provider_base_url_allowed,
    ensure_public_http_url,
    private_provider_urls_allowed,
)

# Shared with the gateway's test of the same policy (TestValidateBaseURL).
_POLICY = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "api_contracts/gateway/provider-url-policy.json"
    ).read_text()
)


@pytest.fixture(autouse=True)
def dns(provider_dns):
    return provider_dns(_POLICY["dns"])


def _check(url, allow_private):
    """None if the URL passes, else the error message."""
    try:
        ensure_public_http_url(
            url,
            PROVIDER_URL_ERROR,
            allow_private=allow_private,
            private_message=PROVIDER_PRIVATE_URL_ERROR,
        )
    except ValueError as e:
        return str(e)
    return None


# What discovery answers for each class, without and with the opt-in.
_EXPECTED = {
    "public": (None, None),
    "private": (PROVIDER_PRIVATE_URL_ERROR, None),
}


@pytest.mark.parametrize("case", _POLICY["urls"], ids=lambda case: case["url"])
def test_url_policy_matches_the_gateway(case):
    # backend_class marks a known difference from the gateway.
    expected = _EXPECTED.get(
        case.get("backend_class", case["class"]),
        (PROVIDER_URL_ERROR, PROVIDER_URL_ERROR),
    )
    assert _check(case["url"], allow_private=False) == expected[0]
    assert _check(case["url"], allow_private=True) == expected[1]


def test_private_url_error_names_the_opt_in():
    assert f"{ALLOW_PRIVATE_PROVIDER_URLS_ENV}=true" in PROVIDER_PRIVATE_URL_ERROR


def test_save_error_does_not_ask_for_a_resolvable_host(monkeypatch):
    # Saving accepts a host that does not resolve here, so its error must not
    # say the host has to resolve.
    monkeypatch.delenv(ALLOW_PRIVATE_PROVIDER_URLS_ENV, raising=False)
    ensure_provider_base_url_allowed("http://no-such-host.invalid")
    with pytest.raises(ValueError) as refused:
        ensure_provider_base_url_allowed("http://127.0.0.1:8080")
    assert "never allowed" in str(refused.value)
    assert "resolve" not in str(refused.value)


def test_metadata_host_is_refused_before_any_lookup(dns):
    assert _check("http://metadata.google.internal", allow_private=True)
    dns.assert_not_called()


def test_webhook_urls_stay_strict():
    # Webhooks never take the opt-in: private and CGNAT hosts stay refused.
    for url in ("http://mock-llm:8080", "http://tailnet-box"):
        with pytest.raises(ValueError, match=WEBHOOK_PRIVATE_URL_ERROR):
            ensure_public_http_url(url, WEBHOOK_PRIVATE_URL_ERROR)


def test_opt_in_is_off_when_unset(monkeypatch):
    monkeypatch.delenv(ALLOW_PRIVATE_PROVIDER_URLS_ENV, raising=False)
    assert private_provider_urls_allowed() is False


# The gateway reads the same variable; both parse these values alike.
@pytest.mark.parametrize(
    "case", _POLICY["opt_in_env"], ids=lambda case: repr(case["value"])
)
def test_opt_in_env_parsing(monkeypatch, case):
    monkeypatch.setenv(ALLOW_PRIVATE_PROVIDER_URLS_ENV, case["value"])
    assert private_provider_urls_allowed() is case["allowed"]
