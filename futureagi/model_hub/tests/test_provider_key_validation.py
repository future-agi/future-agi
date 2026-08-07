"""
Unit tests for model_hub.utils.provider_key_validation.validate_provider_key

Run with: pytest model_hub/tests/test_provider_key_validation.py -v
"""

from unittest.mock import MagicMock, patch

import requests

from model_hub.utils.provider_key_validation import validate_provider_key


def _mock_response(status_code):
    response = MagicMock()
    response.status_code = status_code
    return response


class TestValidateProviderKey:
    def test_unprobed_provider_skips_validation(self):
        with patch("model_hub.utils.provider_key_validation.requests") as mock_requests:
            is_valid, error = validate_provider_key("vertex_ai", "some-key")

        assert is_valid is True
        assert error is None
        mock_requests.get.assert_not_called()
        mock_requests.post.assert_not_called()

    def test_missing_key_skips_validation(self):
        is_valid, error = validate_provider_key("openai", None)

        assert is_valid is True
        assert error is None

    def test_valid_key_accepted(self):
        with patch(
            "model_hub.utils.provider_key_validation.requests.get",
            return_value=_mock_response(200),
        ) as mock_get:
            is_valid, error = validate_provider_key("openai", "sk-good-key")

        assert is_valid is True
        assert error is None
        mock_get.assert_called_once()
        called_url = mock_get.call_args.args[0]
        assert called_url == "https://api.openai.com/v1/models"

    def test_invalid_key_rejected_on_401(self):
        with patch(
            "model_hub.utils.provider_key_validation.requests.get",
            return_value=_mock_response(401),
        ):
            is_valid, error = validate_provider_key("openai", "sk-bad-key")

        assert is_valid is False
        assert "openai" in error
        assert "401" in error

    def test_invalid_key_rejected_on_403(self):
        with patch(
            "model_hub.utils.provider_key_validation.requests.get",
            return_value=_mock_response(403),
        ):
            is_valid, error = validate_provider_key("groq", "gsk-bad-key")

        assert is_valid is False
        assert "groq" in error

    def test_non_auth_error_status_still_accepted(self):
        with patch(
            "model_hub.utils.provider_key_validation.requests.get",
            return_value=_mock_response(500),
        ):
            is_valid, error = validate_provider_key("mistral", "some-key")

        assert is_valid is True
        assert error is None

    def test_network_error_fails_open(self):
        with patch(
            "model_hub.utils.provider_key_validation.requests.get",
            side_effect=requests.ConnectionError("boom"),
        ):
            is_valid, error = validate_provider_key("cohere", "some-key")

        assert is_valid is True
        assert error is None

    def test_anthropic_uses_header_auth(self):
        with patch(
            "model_hub.utils.provider_key_validation.requests.get",
            return_value=_mock_response(200),
        ) as mock_get:
            is_valid, _ = validate_provider_key("anthropic", "sk-ant-key")

        assert is_valid is True
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["x-api-key"] == "sk-ant-key"

    def test_gemini_uses_query_param_auth(self):
        with patch(
            "model_hub.utils.provider_key_validation.requests.get",
            return_value=_mock_response(200),
        ) as mock_get:
            is_valid, _ = validate_provider_key("gemini", "AIza-key")

        assert is_valid is True
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["key"] == "AIza-key"

    def test_perplexity_uses_chat_completions_probe(self):
        with patch(
            "model_hub.utils.provider_key_validation.requests.post",
            return_value=_mock_response(200),
        ) as mock_post:
            is_valid, _ = validate_provider_key("perplexity", "pplx-key")

        assert is_valid is True
        mock_post.assert_called_once()
        called_url = mock_post.call_args.args[0]
        assert called_url == "https://api.perplexity.ai/chat/completions"

    def test_together_ai_invalid_key_rejected(self):
        with patch(
            "model_hub.utils.provider_key_validation.requests.get",
            return_value=_mock_response(401),
        ):
            is_valid, error = validate_provider_key("together_ai", "bad-key")

        assert is_valid is False
        assert "together_ai" in error
