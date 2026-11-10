from unittest.mock import Mock, patch

import pytest
import requests

from agentcc.views.provider_credential import AgentccProviderCredentialViewSet


@pytest.mark.parametrize("provider_name", [None, "azure", "custom-azure"])
@pytest.mark.parametrize("suffix", ["", "/"])
def test_azure_discovery_uses_native_path_and_auth(provider_name, suffix):
    session = Mock()
    session.get.return_value.json.return_value = {
        "data": [{"id": "synthetic-z"}, {"id": "synthetic-a"}]
    }
    with (
        patch("agentcc.views.provider_credential.ensure_public_http_url"),
        patch(
            "agentcc.views.provider_credential.build_ssrf_safe_session",
            return_value=session,
        ),
    ):
        models = AgentccProviderCredentialViewSet()._fetch_models_from_provider(
            provider_name,
            f"https://resource.example.test{suffix}",
            "synthetic-key",
            "azure",
        )

    assert models == ["synthetic-a", "synthetic-z"]
    session.get.assert_called_once_with(
        "https://resource.example.test/openai/models",
        headers={"api-key": "synthetic-key"},
        params={"api-version": "2024-10-21"},
        timeout=15,
    )
    session.get.return_value.raise_for_status.assert_called_once_with()


def test_azure_discovery_requires_a_resource_url():
    with (
        patch("agentcc.views.provider_credential.http_requests.get") as get,
        pytest.raises(ValueError, match="requires a resource base URL"),
    ):
        AgentccProviderCredentialViewSet()._fetch_models_from_provider(
            "azure", "", "synthetic-key", "azure"
        )
    get.assert_not_called()


def test_azure_discovery_does_not_fall_back_after_an_auth_failure():
    session = Mock()
    session.get.return_value.raise_for_status.side_effect = requests.HTTPError(
        "synthetic authentication rejection"
    )
    with (
        patch("agentcc.views.provider_credential.ensure_public_http_url"),
        patch(
            "agentcc.views.provider_credential.build_ssrf_safe_session",
            return_value=session,
        ),
        pytest.raises(requests.HTTPError, match="synthetic authentication rejection"),
    ):
        AgentccProviderCredentialViewSet()._fetch_models_from_provider(
            "azure", "https://resource.example.test", "synthetic-key", "azure"
        )
    assert session.get.call_count == 1
