"""Successful, local fake-provider responses are untrusted."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from agentcc.tests.provider_discovery_test_support import (
    discovery_enabled as discovery_enabled,
)
from agentcc.views import provider_credential as module

# Visibly synthetic; never sent to a network or a real provider.
KEY = "synthetic-discovery-key-not-a-credential"
AUX = "synthetic-auxiliary-secret"
LEGACY = "synthetic-legacy-secret"
SHAPED = "sk-test-not-a-real-provider-key"
ENTROPY = "aB3dE6gH9jK2mN5pQ8sT1vW4yZ7cF0iL"


def unsafe_ids():
    return [
        KEY,
        KEY[:12],
        "model-" + KEY + "-suffix",
        base64.b64encode(KEY.encode()).decode(),
        base64.urlsafe_b64encode(KEY.encode()).decode().rstrip("="),
        SHAPED,
        "cataloguesk-test-not-a-real-provider-key",
        "aB3dE6gH9jK2mN5p/Q8sT1vW4yZ7cF0iL",
        KEY.encode().hex(),
        "Bearer synthetic-auth-value",
        "Basic c3ludGhldGljOnBhc3N3b3Jk",
        ENTROPY,
        "model-" + "x" * 257,
        "model\nname",
        "model?api_key=synthetic",
        AUX,
        LEGACY,
    ]


def credential_echo_ids():
    # Persistence and saved-key listing exclude held credentials only, not
    # unrelated strings rejected by the bounded caller-key discovery grammar.
    return unsafe_ids()[:5] + [KEY.encode().hex(), AUX, LEGACY]


def _discover(provider, api_format, *, saved=True, system_one=False, key=KEY):
    bad = credential_echo_ids() if saved else unsafe_ids()
    good = ["model-one", "model-two"]
    values = bad + good
    google = provider == "google"
    raw = ["models/" + value for value in values] if google else values
    payload = (
        {"models": [{"name": value} for value in raw]}
        if google or provider == "cohere"
        else {"data": [{"id": value} for value in raw]}
    )
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    transport = MagicMock()
    transport.get.return_value = response
    logger = MagicMock()
    row = SimpleNamespace(
        encrypted_credentials=b"synthetic-cipher",
        base_url="",
        api_format=api_format,
        extra_config={"legacy": {"apiKey": LEGACY}},
        models_list=values if system_one else ["model-one"],
        save=MagicMock(),
    )
    data = (
        {"provider_name": provider}
        if saved
        else {"api_format": api_format, "api_key": key}
    )
    request = SimpleNamespace(
        data=data, organization=SimpleNamespace(id="synthetic-org")
    )
    with (
        patch.object(module, "AgentccProviderCredential") as model,
        patch.object(
            module.CredentialManager,
            "decrypt",
            return_value={"api_key": key, "auxiliary": {"value": AUX}},
        ),
        patch.object(module, "http_requests", transport),
        patch.object(module, "logger", logger),
    ):
        model.no_workspace_objects.filter.return_value.first.return_value = (
            row if saved else None
        )
        result = module.AgentccProviderCredentialViewSet().fetch_models(request)
    return result, row, transport, logger, bad


@pytest.mark.parametrize(
    "provider,api_format",
    [
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("google", "google"),
        ("google", "openai"),
        ("cohere", "openai"),
    ],
)
def test_successful_saved_discovery_drops_untrusted_ids(provider, api_format):
    response, row, transport, logger, bad = _discover(provider, api_format)
    assert response.status_code == 200
    assert response.data["result"]["models"] == ["model-one", "model-two"]
    assert response.data["result"]["dropped_model_count"] == len(bad)
    body = json.dumps(response.data)
    logs = repr(logger.mock_calls)
    for value in bad:
        assert value not in body
        assert value not in logs
    assert row.models_list == ["model-one"]
    row.save.assert_not_called()
    transport.get.assert_called_once()
    assert transport.get.call_args.kwargs["allow_redirects"] is False


def test_successful_raw_discovery_drops_the_submitted_key():
    response, _, _, _, _ = _discover("openai", "openai", saved=False)
    values = response.data["result"]["models"]
    assert KEY not in values and KEY[:12] not in values
    assert base64.b64encode(KEY.encode()).decode() not in values
    assert "model-one" in values


@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
def test_saved_system_one_filters_without_a_probe_or_rewriting_legacy_rows(api_format):
    response, row, transport, logger, bad = _discover(
        "custom-decider", api_format, system_one=True
    )
    assert response.data["result"]["models"] == ["model-one", "model-two"]
    assert response.data["result"]["dropped_model_count"] == len(bad)
    transport.get.assert_not_called()
    row.save.assert_not_called()
    assert row.models_list == bad + ["model-one", "model-two"]
    assert KEY not in json.dumps(response.data) + repr(logger.mock_calls)


def test_google_raw_secret_is_checked_before_prefix_normalization():
    # After stripping models/, this exact saved key is no longer present.
    key = "models/gemini-test-private-id"
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "models": [{"name": key}, {"name": "models/gemini-2.5-flash"}]
    }
    request = SimpleNamespace(data={"api_key": key, "api_format": "google"})
    with patch.object(module.http_requests, "get", return_value=response):
        result = module.AgentccProviderCredentialViewSet().fetch_models(request)
    assert result.data["result"]["models"] == ["gemini-2.5-flash"]
    assert result.data["result"]["dropped_model_count"] == 1


@pytest.mark.parametrize(
    "value",
    [
        "gsk_synthetic",
        "pplx-synthetic",
        "AIzaSynthetic",
        "hf_synthetic",
        "xai-synthetic",
        "nvapi-synthetic",
        "fw_synthetic",
        "AKIASYNTHETIC",
        "r8_synthetic",
        "sk-ant-synthetic",
        "Bearer-synthetic",
        "Basic_synthetic",
        ENTROPY,
        "a" * 257,
        "name with space",
        "models/<unsafe>",
    ],
)
def test_credential_shapes_and_grammar_are_not_routable(value):
    from agentcc.services.provider_model_safety import filter_provider_model_ids

    result = filter_provider_model_ids([value, "gpt-4o"], ())
    assert result == {"models": ["gpt-4o"], "dropped_model_count": 1}


@pytest.mark.parametrize(
    "value",
    [
        "gpt-4o",
        "gpt-4o-2024-08-06",
        "ft:gpt-4o:synthetic-org:custom:id123",
        "accounts/fireworks/models/llama-v3p1-70b-instruct",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "qwen3:8b",
        "vertex_ai/gemini-2.5-flash",
        "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "strands-decider",
    ],
)
def test_compatible_normal_ids_remain_routable(value):
    from agentcc.services.provider_model_safety import filter_provider_model_ids

    assert filter_provider_model_ids([value], ()) == {
        "models": [value],
        "dropped_model_count": 0,
    }
