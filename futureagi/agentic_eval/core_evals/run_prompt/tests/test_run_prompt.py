"""
Comprehensive test cases for RunPrompt and LiteLLM integration.

This module contains tests for:
- Unit tests (mocked - no API calls)
- Integration tests (with live API calls)
- Configuration tests for all model types
- Provider-specific tests
- Reasoning model parameter handling

Run with:
    # All tests (requires API keys)
    python -m pytest agentic_eval/core_evals/run_prompt/tests/ -v

    # Unit tests only (no API keys needed)
    python -m pytest agentic_eval/core_evals/run_prompt/tests/ -v -m unit

    # Live LLM tests (requires API keys)
    python -m pytest agentic_eval/core_evals/run_prompt/tests/ -v -m live_llm

    # Specific provider tests
    python -m pytest agentic_eval/core_evals/run_prompt/tests/ -v -k "openai"
"""

import os
import json
import time
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from uuid import uuid4

import pytest

# Import test fixtures
from .conftest import skip_if_no_api_key


# =============================================================================
# Unit Tests - RunPrompt Class Initialization
# =============================================================================

@pytest.mark.unit
class TestRunPromptInitialization:
    """Test RunPrompt class initialization and configuration."""

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_init_basic(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id):
        """Test basic RunPrompt initialization."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=1000,
            top_p=1.0,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        assert run_prompt.model == "gpt-4o"
        assert run_prompt.messages == simple_messages
        assert run_prompt.temperature == 0.7
        assert run_prompt.max_tokens == 1000

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_init_with_run_prompt_config(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id, run_prompt_config_creative):
        """Test RunPrompt initialization with config overrides."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=None,  # Should use config value
            frequency_penalty=None,
            presence_penalty=None,
            max_tokens=None,
            top_p=None,
            response_format=None,
            tool_choice=None,
            tools=None,
            run_prompt_config=run_prompt_config_creative,
        )

        assert run_prompt.temperature == 1.0
        assert run_prompt.max_tokens == 2000
        assert run_prompt.top_p == 0.95

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_init_explicit_overrides_config(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id, run_prompt_config_creative):
        """Test that explicit parameters override config values."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.5,  # Explicit value should override config
            frequency_penalty=0.1,
            presence_penalty=0.1,
            max_tokens=500,
            top_p=0.9,
            response_format=None,
            tool_choice=None,
            tools=None,
            run_prompt_config=run_prompt_config_creative,
        )

        assert run_prompt.temperature == 0.5
        assert run_prompt.max_tokens == 500
        assert run_prompt.top_p == 0.9


# =============================================================================
# Unit Tests - Payload Creation
# =============================================================================

@pytest.mark.unit
class TestPayloadCreation:
    """Test payload creation for different model types."""

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_standard_model_payload(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id):
        """Test payload creation for standard chat models."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.5,
            presence_penalty=0.5,
            max_tokens=1000,
            top_p=0.9,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        payload = run_prompt._create_payload(provider="openai", api_key="test-key")

        assert payload["model"] == "gpt-4o"
        assert payload["temperature"] == 0.7
        assert payload["max_tokens"] == 1000
        assert payload["top_p"] == 0.9
        assert "max_completion_tokens" not in payload

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_reasoning_model_payload_o1(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id):
        """Test payload creation for o1 reasoning models."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="o1",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.5,
            presence_penalty=0.5,
            max_tokens=1000,
            top_p=0.9,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        payload = run_prompt._create_payload(provider="openai", api_key="test-key")

        # Reasoning models should use max_completion_tokens
        assert "max_completion_tokens" in payload
        assert payload["max_completion_tokens"] == 1000
        assert "max_tokens" not in payload

        # Reasoning models should not have temperature/top_p (if not 1.0)
        assert "temperature" not in payload
        assert "top_p" not in payload

        # Should not have frequency/presence penalty
        assert "frequency_penalty" not in payload
        assert "presence_penalty" not in payload

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_reasoning_model_payload_o3_mini(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id):
        """Test payload creation for o3-mini reasoning model."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="o3-mini",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.5,
            presence_penalty=0.5,
            max_tokens=2000,
            top_p=0.9,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        payload = run_prompt._create_payload(provider="openai", api_key="test-key")

        assert "max_completion_tokens" in payload
        assert payload["max_completion_tokens"] == 2000
        assert "max_tokens" not in payload
        assert "temperature" not in payload
        assert "frequency_penalty" not in payload

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_azure_reasoning_model_payload(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id):
        """Test payload creation for Azure-prefixed reasoning models."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="azure/o3-mini",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.5,
            presence_penalty=0.5,
            max_tokens=1500,
            top_p=0.9,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        payload = run_prompt._create_payload(provider="azure", api_key={"api_key": "test-key", "api_base": "https://test.openai.azure.com"})

        # Azure reasoning models should also use max_completion_tokens
        assert "max_completion_tokens" in payload
        assert payload["max_completion_tokens"] == 1500
        assert "max_tokens" not in payload
        assert "temperature" not in payload

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_json_response_format_payload(self, mock_custom_model, mock_api_key, json_format_messages, mock_organization_id):
        """Test payload creation with JSON response format."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="gpt-4o",
            messages=json_format_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=1000,
            top_p=1.0,
            response_format={"type": "json_object"},
            tool_choice=None,
            tools=None,
        )

        payload = run_prompt._create_payload(provider="openai", api_key="test-key")

        assert payload["response_format"]["type"] == "json_object"

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_tools_payload(self, mock_custom_model, mock_api_key, tool_messages, mock_organization_id, sample_tools):
        """Test payload creation with function/tool calling."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="gpt-4o",
            messages=tool_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=1000,
            top_p=1.0,
            response_format=None,
            tool_choice="auto",
            tools=sample_tools,
        )

        payload = run_prompt._create_payload(provider="openai", api_key="test-key")

        assert payload["tools"] == sample_tools
        assert payload["tool_choice"] == "auto"


# =============================================================================
# Unit Tests - Reasoning Model Detection
# =============================================================================

@pytest.mark.unit
class TestReasoningModelDetection:
    """Test detection and handling of reasoning models."""

    @pytest.mark.parametrize("model_name", [
        "o1", "o1-2024-12-17", "o1-mini", "o1-mini-2024-09-12",
        "o1-preview", "o1-preview-2024-09-12", "o1-pro",
        "o3", "o3-mini", "o3-mini-2025-01-31", "o3-pro",
        "o4-mini",
        "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-pro",
        "gpt-5.1", "gpt-5.2", "gpt-5.2-pro",
    ])
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_reasoning_model_detected(self, mock_custom_model, mock_api_key, model_name, simple_messages, mock_organization_id):
        """Test that all reasoning models are properly detected."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model=model_name,
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.5,
            presence_penalty=0.5,
            max_tokens=1000,
            top_p=0.9,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        payload = run_prompt._create_payload(provider="openai", api_key="test-key")

        assert "max_completion_tokens" in payload, f"Model {model_name} should use max_completion_tokens"
        assert "max_tokens" not in payload, f"Model {model_name} should not have max_tokens"

    @pytest.mark.parametrize("model_name", [
        "azure/o1", "azure/o1-mini", "azure/o3-mini",
        "bedrock/o1", "openai/o3-mini",
    ])
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_prefixed_reasoning_model_detected(self, mock_custom_model, mock_api_key, model_name, simple_messages, mock_organization_id):
        """Test that prefixed reasoning models are properly detected."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model=model_name,
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.5,
            presence_penalty=0.5,
            max_tokens=1000,
            top_p=0.9,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        # Use appropriate provider based on prefix
        provider = model_name.split('/')[0]
        api_key = {"api_key": "test-key", "api_base": "https://test.example.com"} if provider == "azure" else "test-key"

        payload = run_prompt._create_payload(provider=provider, api_key=api_key)

        assert "max_completion_tokens" in payload, f"Model {model_name} should use max_completion_tokens"

    @pytest.mark.parametrize("model_name", [
        "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo",
        "claude-3-5-sonnet-20241022", "claude-3-opus-20240229",
    ])
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_standard_model_not_reasoning(self, mock_custom_model, mock_api_key, model_name, simple_messages, mock_organization_id):
        """Test that standard models are NOT detected as reasoning models."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model=model_name,
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.5,
            presence_penalty=0.5,
            max_tokens=1000,
            top_p=0.9,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        provider = "anthropic" if "claude" in model_name else "openai"
        payload = run_prompt._create_payload(provider=provider, api_key="test-key")

        assert "max_tokens" in payload, f"Model {model_name} should use max_tokens"
        assert "max_completion_tokens" not in payload, f"Model {model_name} should NOT use max_completion_tokens"


# =============================================================================
# Unit Tests - LiteLLM Model Manager
# =============================================================================

@pytest.mark.unit
class TestLiteLLMModelManager:
    """Test LiteLLMModelManager class."""

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_init_without_organization(self, mock_custom_model):
        """Test ModelManager initialization without organization."""
        from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

        mock_custom_model.objects.filter.return_value.values.return_value = []

        manager = LiteLLMModelManager(model_name="gpt-4o")

        assert manager.model_name == "gpt-4o"
        assert len(manager.models) > 0

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_init_with_organization(self, mock_custom_model, mock_organization_id):
        """Test ModelManager initialization with organization."""
        from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

        mock_custom_model.objects.filter.return_value.values.return_value = [
            {"user_model_id": "custom-gpt-4", "provider": "openai"}
        ]

        manager = LiteLLMModelManager(model_name="custom-gpt-4", organization_id=mock_organization_id)

        assert any(m["model_name"] == "custom-gpt-4" for m in manager.models)

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_get_provider(self, mock_custom_model):
        """Test getting provider for a model."""
        from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

        mock_custom_model.objects.filter.return_value.values.return_value = []

        manager = LiteLLMModelManager(model_name="gpt-4o")
        provider = manager.get_provider("gpt-4o")

        assert provider == "openai"

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_get_provider_anthropic(self, mock_custom_model):
        """Test getting provider for Anthropic model."""
        from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

        mock_custom_model.objects.filter.return_value.values.return_value = []

        manager = LiteLLMModelManager(model_name="claude-3-5-sonnet-20241022")
        provider = manager.get_provider("claude-3-5-sonnet-20241022")

        assert provider == "anthropic"

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_get_model_by_provider(self, mock_custom_model):
        """Test getting all models for a provider."""
        from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

        mock_custom_model.objects.filter.return_value.values.return_value = []

        manager = LiteLLMModelManager(model_name="gpt-4o")
        openai_models = manager.get_model_by_provider("openai")

        assert len(openai_models) > 0
        assert "gpt-4o" in openai_models

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_failed_models_removed(self, mock_custom_model):
        """Test that failed/deprecated models are removed."""
        from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager

        mock_custom_model.objects.filter.return_value.values.return_value = []

        manager = LiteLLMModelManager(model_name="gpt-4o")

        # Check that deprecated models are not in the list
        model_names = [m["model_name"] for m in manager.models]
        assert "claude-instant-1" not in model_names
        assert "claude-2" not in model_names


# =============================================================================
# Unit Tests - Retry Logic
# =============================================================================

@pytest.mark.unit
class TestRetryLogic:
    """Test retry logic for API calls."""

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_retry_on_timeout_success(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id):
        """Test successful call after retry."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=1000,
            top_p=1.0,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        call_count = [0]

        def mock_func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise TimeoutError("Connection timeout")
            return "success"

        result = run_prompt._retry_on_timeout(mock_func, max_retries=5, initial_delay=0.01)
        assert result == "success"
        assert call_count[0] == 3

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_retry_exhausted(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id):
        """Test that retries are exhausted properly."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=1000,
            top_p=1.0,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        def always_timeout():
            raise TimeoutError("Always timeout")

        with pytest.raises(TimeoutError):
            run_prompt._retry_on_timeout(always_timeout, max_retries=3, initial_delay=0.01)


# =============================================================================
# Unit Tests - Message Handling
# =============================================================================

@pytest.mark.unit
class TestMessageHandling:
    """Test message handling and processing."""

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_get_input_text_from_simple_messages(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id):
        """Test extracting text from simple messages."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="tts-1",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="audio",
            temperature=None,
            frequency_penalty=None,
            presence_penalty=None,
            max_tokens=None,
            top_p=None,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        input_text = run_prompt._get_input_text_from_messages()
        assert "helpful assistant" in input_text
        assert "Hello" in input_text

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_get_input_text_from_multimodal_messages(self, mock_custom_model, mock_api_key, multimodal_messages, mock_organization_id):
        """Test extracting text from multimodal messages."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="tts-1",
            messages=multimodal_messages,
            organization_id=mock_organization_id,
            output_format="audio",
            temperature=None,
            frequency_penalty=None,
            presence_penalty=None,
            max_tokens=None,
            top_p=None,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        input_text = run_prompt._get_input_text_from_messages()
        assert "What is in this image" in input_text

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_get_input_text_empty_raises_error(self, mock_custom_model, mock_api_key, mock_organization_id):
        """Test that empty messages raise an error."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="tts-1",
            messages=[{"role": "user", "content": ""}],
            organization_id=mock_organization_id,
            output_format="audio",
            temperature=None,
            frequency_penalty=None,
            presence_penalty=None,
            max_tokens=None,
            top_p=None,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        with pytest.raises(ValueError, match="No text found"):
            run_prompt._get_input_text_from_messages()


# =============================================================================
# Integration Tests - Live LLM Calls (OpenAI)
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
@pytest.mark.django_db
class TestOpenAILiveIntegration:
    """Live integration tests for OpenAI models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for OpenAI tests."""
        if not api_keys.get("openai"):
            pytest.skip("OPENAI_API_KEY not configured")

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_gpt4o_basic_chat(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id, api_keys):
        """Test basic chat with GPT-4o."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []
        mock_custom_model.DoesNotExist = Exception
        mock_custom_model.objects.get.side_effect = mock_custom_model.DoesNotExist

        mock_api_key_entry = Mock()
        mock_api_key_entry.key = api_keys["openai"]
        mock_api_key_entry.actual_key = api_keys["openai"]
        mock_api_key_entry.actual_json = None
        mock_api_key.objects.get.return_value = mock_api_key_entry

        run_prompt = RunPrompt(
            model="gpt-4o-mini",  # Using mini for cost efficiency
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=100,
            top_p=1.0,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        result = run_prompt.litellm_response()

        assert result is not None
        # litellm_response returns a (response_text, metadata) tuple; assert the
        # model produced non-empty response text.
        response_text = result[0] if isinstance(result, tuple) else result
        assert response_text

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_gpt4o_json_response(self, mock_custom_model, mock_api_key, mock_organization_id, api_keys):
        """Test JSON response format with GPT-4o."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []
        mock_custom_model.DoesNotExist = Exception
        mock_custom_model.objects.get.side_effect = mock_custom_model.DoesNotExist

        mock_api_key_entry = Mock()
        mock_api_key_entry.key = api_keys["openai"]
        mock_api_key_entry.actual_key = api_keys["openai"]
        mock_api_key_entry.actual_json = None
        mock_api_key.objects.get.return_value = mock_api_key_entry

        messages = [
            {"role": "system", "content": "You output valid JSON only."},
            {"role": "user", "content": "Return a JSON with name 'test' and value 123."}
        ]

        run_prompt = RunPrompt(
            model="gpt-4o-mini",
            messages=messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=100,
            top_p=1.0,
            response_format={"type": "json_object"},
            tool_choice=None,
            tools=None,
        )

        result = run_prompt.litellm_response()
        assert result is not None


# =============================================================================
# Integration Tests - Live LLM Calls (Anthropic)
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
@pytest.mark.django_db
class TestAnthropicLiveIntegration:
    """Live integration tests for Anthropic models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for Anthropic tests."""
        if not api_keys.get("anthropic"):
            pytest.skip("ANTHROPIC_API_KEY not configured")

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_claude_sonnet_basic_chat(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id, api_keys):
        """Test basic chat with Claude Sonnet."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []
        mock_custom_model.DoesNotExist = Exception
        mock_custom_model.objects.get.side_effect = mock_custom_model.DoesNotExist

        mock_api_key_entry = Mock()
        mock_api_key_entry.key = api_keys["anthropic"]
        mock_api_key_entry.actual_key = api_keys["anthropic"]
        mock_api_key_entry.actual_json = None
        mock_api_key.objects.get.return_value = mock_api_key_entry

        run_prompt = RunPrompt(
            model="claude-haiku-4-5-20251001",  # Using current Haiku for cost efficiency
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=100,
            # claude 4.x rejects temperature and top_p together; send temperature only
            top_p=None,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        result = run_prompt.litellm_response()
        assert result is not None


# =============================================================================
# Integration Tests - Live LLM Calls (Groq)
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
@pytest.mark.django_db
class TestGroqLiveIntegration:
    """Live integration tests for Groq models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for Groq tests."""
        if not api_keys.get("groq"):
            pytest.skip("GROQ_API_KEY not configured")

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_groq_llama_basic_chat(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id, api_keys):
        """Test basic chat with Groq Llama."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []
        mock_custom_model.DoesNotExist = Exception
        mock_custom_model.objects.get.side_effect = mock_custom_model.DoesNotExist

        mock_api_key_entry = Mock()
        mock_api_key_entry.key = api_keys["groq"]
        mock_api_key_entry.actual_key = api_keys["groq"]
        mock_api_key_entry.actual_json = None
        mock_api_key.objects.get.return_value = mock_api_key_entry

        run_prompt = RunPrompt(
            model="groq/llama-3.1-8b-instant",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=100,
            top_p=1.0,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        result = run_prompt.litellm_response()
        assert result is not None


# =============================================================================
# Integration Tests - Live LLM Calls (xAI)
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
@pytest.mark.django_db
class TestXAILiveIntegration:
    """Live integration tests for xAI models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for xAI tests."""
        if not api_keys.get("xai"):
            pytest.skip("XAI_API_KEY not configured")

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_grok_basic_chat(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id, api_keys):
        """Test basic chat with Grok."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []
        mock_custom_model.DoesNotExist = Exception
        mock_custom_model.objects.get.side_effect = mock_custom_model.DoesNotExist

        mock_api_key_entry = Mock()
        mock_api_key_entry.key = api_keys["xai"]
        mock_api_key_entry.actual_key = api_keys["xai"]
        mock_api_key_entry.actual_json = None
        mock_api_key.objects.get.return_value = mock_api_key_entry

        run_prompt = RunPrompt(
            model="xai/grok-3",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            # grok-3 rejects presence/frequency penalty params
            frequency_penalty=None,
            presence_penalty=None,
            max_tokens=100,
            top_p=1.0,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        result = run_prompt.litellm_response()
        assert result is not None


# =============================================================================
# Integration Tests - Live LLM Calls (Perplexity)
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestPerplexityLiveIntegration:
    """Live integration tests for Perplexity models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for Perplexity tests."""
        if not api_keys.get("perplexity"):
            pytest.skip("PERPLEXITY_API_KEY not configured")

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_perplexity_sonar_basic_chat(self, mock_custom_model, mock_api_key, simple_messages, mock_organization_id, api_keys):
        """Test basic chat with Perplexity Sonar."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []
        mock_custom_model.DoesNotExist = Exception
        mock_custom_model.objects.get.side_effect = mock_custom_model.DoesNotExist

        mock_api_key_entry = Mock()
        mock_api_key_entry.key = api_keys["perplexity"]
        mock_api_key_entry.actual_key = api_keys["perplexity"]
        mock_api_key_entry.actual_json = None
        mock_api_key.objects.get.return_value = mock_api_key_entry

        run_prompt = RunPrompt(
            model="perplexity/sonar",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=100,
            top_p=1.0,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        result = run_prompt.litellm_response()
        assert result is not None


# =============================================================================
# Stress Tests
# =============================================================================

@pytest.mark.slow
@pytest.mark.integration
class TestStressTests:
    """Stress tests for RunPrompt."""

    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_large_message_handling(self, mock_custom_model, mock_api_key, long_messages, mock_organization_id):
        """Test handling of large messages."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="gpt-4o",
            messages=long_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=1000,
            top_p=1.0,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        payload = run_prompt._create_payload(provider="openai", api_key="test-key")
        assert len(payload["messages"]) == 2
        assert len(payload["messages"][1]["content"]) > 1000


# =============================================================================
# Configuration Tests
# =============================================================================

@pytest.mark.unit
class TestConfigurationVariations:
    """Test various configuration combinations."""

    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0, 2.0])
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_temperature_variations(self, mock_custom_model, mock_api_key, temperature, simple_messages, mock_organization_id):
        """Test various temperature settings."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=temperature,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=100,
            top_p=1.0,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        payload = run_prompt._create_payload(provider="openai", api_key="test-key")
        assert payload["temperature"] == temperature

    @pytest.mark.parametrize("max_tokens", [1, 100, 1000, 4000, 8000])
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_max_tokens_variations(self, mock_custom_model, mock_api_key, max_tokens, simple_messages, mock_organization_id):
        """Test various max_tokens settings."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=max_tokens,
            top_p=1.0,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        payload = run_prompt._create_payload(provider="openai", api_key="test-key")
        assert payload["max_tokens"] == max_tokens

    @pytest.mark.parametrize("top_p", [0.1, 0.5, 0.9, 1.0])
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey")
    @patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel")
    def test_top_p_variations(self, mock_custom_model, mock_api_key, top_p, simple_messages, mock_organization_id):
        """Test various top_p settings."""
        from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

        mock_custom_model.objects.filter.return_value.values.return_value = []

        run_prompt = RunPrompt(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            output_format="text",
            temperature=0.7,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=100,
            top_p=top_p,
            response_format=None,
            tool_choice=None,
            tools=None,
        )

        payload = run_prompt._create_payload(provider="openai", api_key="test-key")
        assert payload["top_p"] == top_p


# =============================================================================
# Available Models Tests
# =============================================================================

@pytest.mark.unit
class TestAvailableModels:
    """Test available models configuration."""

    def test_available_models_loaded(self):
        """Test that available models are loaded."""
        from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

        assert AVAILABLE_MODELS is not None
        assert len(AVAILABLE_MODELS) > 0

    def test_oss_catalog_excludes_ee_only_models(self):
        """Test that OSS metadata does not expose private EE-only model aliases."""
        from agentic_eval.core_evals.run_prompt.available_models import OSS_AVAILABLE_MODELS

        model_names = {m["model_name"] for m in OSS_AVAILABLE_MODELS}

        assert "turing_large" not in model_names
        assert "protect_toxicity" not in model_names
        assert not any(
            name.startswith("bedrock/arn:aws:bedrock:")
            for name in model_names
        )

    def test_oss_public_catalog_keeps_pricing_metadata(self):
        """Test that public model pricing stays with the OSS catalog."""
        from agentic_eval.core_evals.run_prompt.available_models import OSS_AVAILABLE_MODELS

        gpt4o = next(
            model for model in OSS_AVAILABLE_MODELS
            if model["model_name"] == "gpt-4o"
        )

        assert gpt4o["pricing"] == {
            "input_per_1M_tokens": 5,
            "output_per_1M_tokens": 15,
        }

    def test_runtime_catalog_appends_ee_only_models_when_available(self):
        """Test that EE installs still expose private evaluator models at runtime."""
        from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

        model_names = {m["model_name"] for m in AVAILABLE_MODELS}

        assert "turing_large" in model_names
        assert "protect_toxicity" in model_names

    def test_openai_models_present(self):
        """Test that OpenAI models are present."""
        from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

        model_names = [m["model_name"] for m in AVAILABLE_MODELS]
        assert "gpt-4o" in model_names
        assert "gpt-4o-mini" in model_names

    def test_anthropic_models_present(self):
        """Test that Anthropic models are present."""
        from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

        model_names = [m["model_name"] for m in AVAILABLE_MODELS]
        assert "claude-3-5-sonnet-20241022" in model_names

    def test_reasoning_models_have_fields_not_allowed(self):
        """Test that reasoning models have fields_not_allowed configured."""
        from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

        reasoning_model_names = ["o1", "o3-mini", "o3", "o4-mini"]
        for model in AVAILABLE_MODELS:
            if model["model_name"] in reasoning_model_names:
                assert "fields_not_allowed" in model, f"Model {model['model_name']} should have fields_not_allowed"

    def test_azure_reasoning_models_present(self):
        """Test that Azure reasoning models are present."""
        from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

        model_names = [m["model_name"] for m in AVAILABLE_MODELS]
        azure_reasoning_models = ["azure/o1", "azure/o3-mini"]

        for model in azure_reasoning_models:
            assert model in model_names, f"Azure model {model} should be present"

    def test_model_has_required_fields(self):
        """Test that models have required fields."""
        from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

        required_fields = ["model_name", "providers", "mode"]

        for model in AVAILABLE_MODELS[:10]:  # Check first 10 models
            for field in required_fields:
                assert field in model, f"Model {model.get('model_name', 'unknown')} missing {field}"
