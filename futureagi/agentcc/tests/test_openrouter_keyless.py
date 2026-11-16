"""OpenRouter is keyed even when a System One credential points at a proxy."""

import pytest

from agentcc.serializers.provider_credential import (
    AgentccProviderCredentialCreateSerializer,
)


@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
@pytest.mark.parametrize(
    "name,base_url",
    [
        ("openrouter", "https://decider.example.com"),
        ("custom-decider", "https://openrouter.ai/api/v1"),
        ("custom-decider", "https://API.OpenRouter.AI./api/v1"),
    ],
)
def test_openrouter_system_one_never_accepts_a_keyless_credential(
    name, base_url, api_format
):
    serializer = AgentccProviderCredentialCreateSerializer(
        data={
            "provider_name": name,
            "credentials": {},
            "api_format": api_format,
            "base_url": base_url,
            "models_list": ["typesafe/jev-1.13"],
        }
    )
    assert not serializer.is_valid()
    assert "credentials" in serializer.errors
