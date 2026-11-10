from unittest.mock import Mock, patch

import pytest

from agentcc.views.provider_credential import AgentccProviderCredentialViewSet


@pytest.mark.parametrize("provider_name", [None, "azure", "custom-azure"])
@pytest.mark.parametrize("base_url", ["", "https://resource.example.test/"])
def test_native_azure_requires_explicit_deployment_names(provider_name, base_url):
    session = Mock()
    session.get.return_value.json.return_value = {
        "data": [{"id": "catalogue-model-not-deployment"}]
    }
    with (
        patch("agentcc.views.provider_credential.ensure_public_http_url"),
        patch("agentcc.views.provider_credential.http_requests.get") as get,
        patch(
            "agentcc.views.provider_credential.build_ssrf_safe_session",
            return_value=session,
        ),
    ):
        models = AgentccProviderCredentialViewSet()._fetch_models_from_provider(
            provider_name, base_url, "synthetic-key", "azure"
        )

    assert models == []
    session.get.assert_not_called()
    get.assert_not_called()


def test_saved_azure_openai_compatible_connection_keeps_its_protocol():
    session = Mock()
    session.get.return_value.json.return_value = {"data": [{"id": "deployment"}]}
    with (
        patch("agentcc.views.provider_credential.ensure_public_http_url"),
        patch(
            "agentcc.views.provider_credential.build_ssrf_safe_session",
            return_value=session,
        ),
    ):
        models = AgentccProviderCredentialViewSet()._fetch_models_from_provider(
            "azure", "https://resource.example.test/openai/v1", "synthetic-key", "openai"
        )

    assert models == ["deployment"]
    session.get.assert_called_once_with(
        "https://resource.example.test/openai/v1/models",
        headers={"Authorization": "Bearer synthetic-key"},
        timeout=15,
    )
