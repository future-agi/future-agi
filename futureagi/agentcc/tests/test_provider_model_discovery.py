from unittest.mock import Mock, patch

from agentcc.views.provider_credential import AgentccProviderCredentialViewSet


def _mock_response(payload=None):
    response = Mock()
    response.json.return_value = payload or {"models": []}
    return response


class TestAgentccProviderCredentialModelDiscovery:
    def test_custom_google_format_uses_configured_base_url(self):
        response = _mock_response({"models": [{"name": "models/gemini-custom"}]})
        safe_session = Mock()
        safe_session.get.return_value = response

        with (
            patch(
                "agentcc.views.provider_credential.ensure_public_http_url"
            ) as mock_validate_url,
            patch(
                "agentcc.views.provider_credential.build_ssrf_safe_session",
                return_value=safe_session,
            ) as mock_safe_session,
        ):
            models = AgentccProviderCredentialViewSet()._fetch_models_from_provider(
                None,
                "https://models.example.com",
                "custom-api-key",
                "google",
            )

        assert models == ["gemini-custom"]
        mock_validate_url.assert_called_once_with(
            "https://models.example.com", "Invalid base URL"
        )
        mock_safe_session.assert_called_once_with(
            "Connection to private address blocked"
        )
        safe_session.get.assert_called_once_with(
            "https://models.example.com/v1beta/models",
            params={"key": "custom-api-key"},
            timeout=15,
        )

    def test_custom_google_format_does_not_duplicate_v1beta_path(self):
        safe_session = Mock()
        safe_session.get.return_value = _mock_response()

        with (
            patch("agentcc.views.provider_credential.ensure_public_http_url"),
            patch(
                "agentcc.views.provider_credential.build_ssrf_safe_session",
                return_value=safe_session,
            ),
        ):
            AgentccProviderCredentialViewSet()._fetch_models_from_provider(
                None,
                "https://models.example.com/v1beta",
                "custom-api-key",
                "google",
            )

        safe_session.get.assert_called_once_with(
            "https://models.example.com/v1beta/models",
            params={"key": "custom-api-key"},
            timeout=15,
        )

    def test_google_preset_without_base_url_uses_official_endpoint(self):
        response = _mock_response()

        with patch(
            "agentcc.views.provider_credential.http_requests.get",
            return_value=response,
        ) as mock_get:
            AgentccProviderCredentialViewSet()._fetch_models_from_provider(
                "google",
                "",
                "google-api-key",
                "google",
            )

        mock_get.assert_called_once_with(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": "google-api-key"},
            timeout=15,
        )

    def test_google_preset_with_official_base_url_uses_official_endpoint(self):
        safe_session = Mock()
        safe_session.get.return_value = _mock_response()

        with (
            patch("agentcc.views.provider_credential.ensure_public_http_url"),
            patch(
                "agentcc.views.provider_credential.build_ssrf_safe_session",
                return_value=safe_session,
            ),
        ):
            AgentccProviderCredentialViewSet()._fetch_models_from_provider(
                "google",
                "https://generativelanguage.googleapis.com",
                "google-api-key",
                "google",
            )

        safe_session.get.assert_called_once_with(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": "google-api-key"},
            timeout=15,
        )
