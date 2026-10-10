"""No model-discovery URL repeats a version segment the Base URL already has.

The Base URL is used exactly as the operator entered it — for some providers the
version segment selects which API answers, so it is their choice to make. What
is guaranteed here is narrower: whatever they type, no branch emits "/v1/v1" or
a doubled slash.

Verified against the live Perplexity API on 2026-10-06, which is why the Base
URL is left alone: GET /v1/models -> 200 but GET /models -> 404, while
POST /chat/completions -> 200 but POST /v1/chat/completions -> 404. Its
catalogue and its chat endpoint sit on different APIs, so
"https://api.perplexity.ai/v1" and "https://api.perplexity.ai" are not two
spellings of one thing.
"""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.views.provider_credential import AgentccProviderCredentialViewSet


def _models_url(base_url, api_format="openai", provider_name="custom"):
    """The URL discovery actually requests for this provider configuration."""
    session = MagicMock()
    session.get.return_value.json.return_value = {"data": []}
    with (
        patch("agentcc.views.provider_credential.ensure_public_http_url"),
        patch(
            "agentcc.views.provider_credential.build_ssrf_safe_session",
            return_value=session,
        ),
    ):
        AgentccProviderCredentialViewSet()._fetch_models_from_provider(
            provider_name, base_url, "sk-test", api_format
        )
    return session.get.call_args[0][0]


@pytest.mark.parametrize(
    "provider_name,api_format,base_url",
    [
        # The anthropic branch triggers on `api_format == "anthropic"` too, so a
        # Custom / Self-hosted provider reaches it — this is not preset-only.
        ("custom", "anthropic", "https://api.anthropic.com/v1"),
        ("custom", "anthropic", "https://api.anthropic.com/v1/"),
        ("custom", "anthropic", "https://my-proxy.internal/anthropic/v1"),
        ("anthropic", "anthropic", "https://api.anthropic.com/v1"),
        ("cohere", "openai", "https://api.cohere.com/v1"),
        ("cohere", "openai", "https://api.cohere.com/v1/"),
    ],
)
def test_discovery_url_never_doubles_the_version_segment(
    provider_name, api_format, base_url
):
    url = _models_url(base_url, api_format=api_format, provider_name=provider_name)
    assert "/v1/v1" not in url, url
    assert "//models" not in url, url


def test_anthropic_discovery_is_one_url_whatever_the_base_spelling():
    """Anthropic's catalogue is always /v1/models, so every spelling lands there
    — including the trailing slash, which used to give "//v1/models"."""
    urls = {
        _models_url(base, api_format="anthropic", provider_name="anthropic")
        for base in (
            "https://api.anthropic.com",
            "https://api.anthropic.com/",
            "https://api.anthropic.com/v1",
            "https://api.anthropic.com/v1/",
        )
    }
    assert urls == {"https://api.anthropic.com/v1/models"}


def test_cohere_compatibility_endpoint_is_unchanged():
    """The old code special-cased "/compatibility/v1"; stating "/v1" as the
    prefix must land on exactly the same URL."""
    assert (
        _models_url(
            "https://api.cohere.ai/compatibility/v1",
            api_format="openai",
            provider_name="cohere",
        )
        == "https://api.cohere.ai/compatibility/v1/models"
    )


def test_cohere_plain_base_still_gets_the_version_segment():
    assert (
        _models_url(
            "https://api.cohere.com", api_format="openai", provider_name="cohere"
        )
        == "https://api.cohere.com/v1/models"
    )
