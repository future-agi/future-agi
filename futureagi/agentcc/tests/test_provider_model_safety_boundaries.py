"""Non-overlapping secret fixtures and accepted provider routing grammars."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from agentcc.tests.provider_discovery_test_support import (
    discovery_enabled as discovery_enabled,
)
from agentcc.views import provider_credential as module


@pytest.mark.parametrize("echo", ["auxiliary-fixture-only", "legacy-fixture-only"])
def test_saved_key_listing_uses_auxiliary_and_legacy_credential_exclusions(echo):
    saved = SimpleNamespace(
        encrypted_credentials=b"synthetic-cipher",
        api_format="openai",
        base_url="",
        extra_config={"wrapper": [{"apiKey": "legacy-fixture-only"}]},
        models_list=[],
    )
    provider = MagicMock(status_code=200)
    provider.json.return_value = {"data": [{"id": echo}, {"id": "gpt-4o"}]}
    request = SimpleNamespace(
        organization=SimpleNamespace(id="synthetic-org"),
        data={"provider_name": "openai"},
    )
    with (
        patch.object(module, "AgentccProviderCredential") as model,
        patch.object(
            module.CredentialManager,
            "decrypt",
            return_value={
                "api_key": "original-fixture-only",
                "nested": {"aws_session_token": "auxiliary-fixture-only"},
            },
        ),
        patch.object(module.http_requests, "get", return_value=provider),
    ):
        model.no_workspace_objects.filter.return_value.first.return_value = saved
        response = module.AgentccProviderCredentialViewSet().fetch_models(request)
    assert response.data["result"] == {"models": ["gpt-4o"], "dropped_model_count": 1}


@pytest.mark.parametrize(
    "provider,api_format,value",
    [
        ("perplexity", "systemone", "pplx-decider-v1.1-27b"),
        ("perplexity", "systemone", "pplx-decider-v1-27b"),
        ("vertex", "gemini", "vertex_ai/gemini-2.5-flash"),
        ("custom", "openai", "task-model"),
        ("anthropic", "anthropic", "claude-sonnet-4-5-20250929"),
        ("cohere", "openai", "command-r-plus-08-2024"),
    ],
)
def test_known_model_ids_are_not_mistaken_for_provider_keys(
    provider, api_format, value
):
    from agentcc.services.provider_model_safety import filter_provider_model_ids

    assert filter_provider_model_ids(
        [value], (), provider_name=provider, api_format=api_format
    ) == {"models": [value], "dropped_model_count": 0}
    # A known public model name is not a bypass if it equals the actual secret.
    assert filter_provider_model_ids(
        [value], (value,), provider_name=provider, api_format=api_format
    ) == {"models": [], "dropped_model_count": 1}


def test_google_normalization_rechecks_the_result():
    from agentcc.services.provider_model_safety import filter_provider_model_ids

    assert filter_provider_model_ids(
        ["models/gemini-fixture-secret", "models/gemini-2.5-flash"],
        ("gemini-fixture-secret",),
        api_format="google",
        normalize_google=True,
    ) == {"models": ["gemini-2.5-flash"], "dropped_model_count": 1}
