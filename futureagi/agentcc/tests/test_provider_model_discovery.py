from unittest.mock import Mock, patch

import pytest

from agentcc.services.url_safety import PROVIDER_PRIVATE_URL_ERROR, PROVIDER_URL_ERROR
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
                "agentcc.views.provider_credential.private_provider_urls_allowed",
                return_value=False,
            ),
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
            "https://models.example.com",
            PROVIDER_URL_ERROR,
            allow_private=False,
            private_message=PROVIDER_PRIVATE_URL_ERROR,
        )
        mock_safe_session.assert_called_once_with(
            "Connection to private address blocked", allow_private=False
        )
        safe_session.get.assert_called_once_with(
            "https://models.example.com/v1beta/models",
            params={"key": "custom-api-key"},
            timeout=15,
        )

    @pytest.mark.parametrize(
        "base_url,expected",
        [
            ("https://models.example.com/", "https://models.example.com/v1beta/models"),
            # The gateway's Gemini provider calls {base}/v1beta/models/... with
            # no de-duplication, so discovery must not list models for a base
            # whose every runtime call would hit /v1beta/v1beta.
            (
                "https://models.example.com/v1beta",
                "https://models.example.com/v1beta/v1beta/models",
            ),
        ],
    )
    def test_custom_google_format_probes_the_path_the_gateway_calls(
        self, base_url, expected
    ):
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
                base_url,
                "custom-api-key",
                "google",
            )

        safe_session.get.assert_called_once_with(
            expected,
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

    def test_google_preset_on_openai_format_lists_models_at_google(self):
        # The Google preset saved in the openai format points at Google's
        # OpenAI-compatible base; the edit dialog's fetch-by-name lands here
        # and must still reach Google's real models route.
        safe_session = Mock()
        safe_session.get.return_value = _mock_response(
            {"models": [{"name": "models/gemini-2.5-flash"}]}
        )

        with (
            patch("agentcc.views.provider_credential.ensure_public_http_url"),
            patch(
                "agentcc.views.provider_credential.build_ssrf_safe_session",
                return_value=safe_session,
            ),
        ):
            models = AgentccProviderCredentialViewSet()._fetch_models_from_provider(
                "google",
                "https://generativelanguage.googleapis.com/v1beta/openai",
                "google-api-key",
                "openai",
            )

        assert models == ["gemini-2.5-flash"]
        safe_session.get.assert_called_once_with(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": "google-api-key"},
            timeout=15,
        )
