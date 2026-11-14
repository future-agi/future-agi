"""
Fixtures for run_prompt tests.

This module provides fixtures for testing the RunPrompt class and related functionality.
"""

import os
import pytest
from unittest.mock import Mock, MagicMock, patch
from uuid import uuid4


# =============================================================================
# Pytest Configuration - Register Custom Markers
# =============================================================================


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests (no external dependencies)")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "live_llm: Tests that call live LLM APIs")
    config.addinivalue_line("markers", "external: Tests requiring external network")
    config.addinivalue_line("markers", "slow: Slow-running tests")


# =============================================================================
# Environment Configuration
# =============================================================================


@pytest.fixture(scope="session")
def api_keys():
    """
    Returns available API keys from environment.
    Used to determine which live tests can run.
    """
    return {
        "openai": os.environ.get("OPENAI_API_KEY"),
        "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
        "openrouter": os.environ.get("OPENROUTER_API_KEY"),
        "groq": os.environ.get("GROQ_API_KEY"),
        "xai": os.environ.get("XAI_API_KEY"),
        "perplexity": os.environ.get("PERPLEXITY_API_KEY"),
        "gemini": os.environ.get("GEMINI_API_KEY"),
        "azure": os.environ.get("AZURE_API_KEY"),
        "cohere": os.environ.get("COHERE_API_KEY"),
        "mistral": os.environ.get("MISTRAL_API_KEY"),
        "deepinfra": os.environ.get("DEEPINFRA_API_KEY"),
        "together_ai": os.environ.get("TOGETHERAI_API_KEY"),
        "deepseek": os.environ.get("DEEPSEEK_API_KEY"),
        "fireworks_ai": os.environ.get("FIREWORKS_API_KEY"),
        "ai21": os.environ.get("AI21_API_KEY"),
        "cerebras": os.environ.get("CEREBRAS_API_KEY"),
    }


@pytest.fixture(scope="session")
def available_providers(api_keys):
    """Returns list of providers with configured API keys."""
    return [provider for provider, key in api_keys.items() if key]


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_organization_id():
    """Returns a mock organization ID."""
    return str(uuid4())


@pytest.fixture
def mock_workspace_id():
    """Returns a mock workspace ID."""
    return str(uuid4())


@pytest.fixture
def mock_api_key_entry():
    """Returns a mock ApiKey database entry."""
    mock = Mock()
    mock.key = "test-api-key-12345"
    mock.actual_key = "test-api-key-12345"
    mock.actual_json = None
    return mock


@pytest.fixture
def mock_custom_model():
    """Returns a mock CustomAIModel database entry."""
    mock = Mock()
    mock.user_model_id = "custom-model-1"
    mock.provider = "openai"
    mock.actual_json = {"key": "custom-key-12345"}
    return mock


@pytest.fixture
def mock_litellm_response():
    """Returns a mock LiteLLM API response."""
    mock = Mock()
    mock.choices = [Mock()]
    mock.choices[0].message = Mock()
    mock.choices[0].message.content = "This is a test response from the LLM."
    mock.choices[0].message.tool_calls = None
    mock.usage = Mock()
    mock.usage.prompt_tokens = 10
    mock.usage.completion_tokens = 20
    mock.usage.total_tokens = 30
    mock.model = "gpt-4o"
    return mock


@pytest.fixture
def mock_litellm_stream_response():
    """Returns a mock LiteLLM streaming response."""

    def stream_generator():
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
            {"choices": [{"delta": {"content": "!"}}]},
        ]
        for chunk in chunks:
            mock_chunk = Mock()
            mock_chunk.choices = [Mock()]
            mock_chunk.choices[0].delta = Mock()
            mock_chunk.choices[0].delta.content = chunk["choices"][0]["delta"][
                "content"
            ]
            yield mock_chunk

    return stream_generator()


@pytest.fixture
def mock_websocket_manager():
    """Returns a mock WebSocket manager."""
    mock = MagicMock()
    mock.send_message = MagicMock()
    return mock


# =============================================================================
# Message Fixtures
# =============================================================================


@pytest.fixture
def simple_messages():
    """Simple text messages for testing."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, how are you?"},
    ]


@pytest.fixture
def multimodal_messages():
    """Multimodal messages with text and image for testing."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is in this image?"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.com/image.jpg"},
                },
            ],
        },
    ]


@pytest.fixture
def json_format_messages():
    """Messages requesting JSON output."""
    return [
        {
            "role": "system",
            "content": "You are a helpful assistant that outputs valid JSON.",
        },
        {"role": "user", "content": "Return a JSON object with name and age fields."},
    ]


@pytest.fixture
def tool_messages():
    """Messages with tool/function calling."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the weather in San Francisco?"},
    ]


@pytest.fixture
def sample_tools():
    """Sample tools for function calling tests."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather in a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "The city and state, e.g. San Francisco, CA",
                        }
                    },
                    "required": ["location"],
                },
            },
        }
    ]


@pytest.fixture
def tts_messages():
    """Messages for text-to-speech testing."""
    return [{"role": "user", "content": "Hello, this is a test of text to speech."}]


@pytest.fixture
def long_messages():
    """Long messages for stress testing."""
    long_text = "This is a test sentence. " * 100
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": long_text},
    ]


# =============================================================================
# Model Configuration Fixtures
# =============================================================================


@pytest.fixture
def openai_chat_models():
    """List of OpenAI chat models to test."""
    return [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-4",
        "gpt-3.5-turbo",
    ]


@pytest.fixture
def openai_reasoning_models():
    """List of OpenAI reasoning models to test."""
    return [
        "o1",
        "o1-mini",
        "o1-preview",
        "o3-mini",
    ]


@pytest.fixture
def anthropic_models():
    """List of Anthropic models to test."""
    return [
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
        "claude-3-sonnet-20240229",
        "claude-3-haiku-20240307",
    ]


@pytest.fixture
def groq_models():
    """List of Groq models to test."""
    return [
        "groq/llama-3.3-70b-versatile",
        "groq/llama-3.1-8b-instant",
        "groq/mixtral-8x7b-32768",
    ]


@pytest.fixture
def azure_reasoning_models():
    """List of Azure reasoning models to test."""
    return [
        "azure/o1",
        "azure/o1-mini",
        "azure/o1-preview",
        "azure/o3-mini",
    ]


@pytest.fixture
def all_reasoning_models():
    """Complete list of reasoning models requiring special handling."""
    return [
        # o1 series
        "o1",
        "o1-2024-12-17",
        "o1-mini",
        "o1-mini-2024-09-12",
        "o1-preview",
        "o1-preview-2024-09-12",
        "o1-pro",
        "o1-pro-2025-03-19",
        # o3 series
        "o3",
        "o3-2025-04-16",
        "o3-mini",
        "o3-mini-2025-01-31",
        "o3-pro",
        "o3-pro-2025-06-10",
        # o4 series
        "o4-mini",
        "o4-mini-2025-04-16",
        # GPT-5 series
        "gpt-5",
        "gpt-5-2025-08-07",
        "gpt-5-mini",
        "gpt-5-mini-2025-08-07",
        "gpt-5-nano",
        "gpt-5-nano-2025-08-07",
        "gpt-5-chat-latest",
        "gpt-5-pro",
        "gpt-5-pro-2025-10-06",
        "gpt-5-codex",
        "gpt-5-codex-2025-09-01",
        # GPT-5.1 series
        "gpt-5.1",
        "gpt-5.1-2025-11-13",
        "gpt-5.1-chat-latest",
        "gpt-5.1-codex",
        "gpt-5.1-codex-mini",
        "gpt-5.1-codex-max",
        # GPT-5.2 series
        "gpt-5.2",
        "gpt-5.2-2025-12-11",
        "gpt-5.2-chat-latest",
        "gpt-5.2-pro",
        "gpt-5.2-pro-2025-12-11",
    ]


@pytest.fixture
def run_prompt_config_default():
    """Default run prompt configuration."""
    return {
        "temperature": 0.7,
        "max_tokens": 1000,
        "top_p": 1.0,
    }


@pytest.fixture
def run_prompt_config_creative():
    """Creative configuration with higher temperature."""
    return {
        "temperature": 1.0,
        "max_tokens": 2000,
        "top_p": 0.95,
        "frequency_penalty": 0.5,
        "presence_penalty": 0.5,
    }


@pytest.fixture
def run_prompt_config_deterministic():
    """Deterministic configuration with low temperature."""
    return {
        "temperature": 0.0,
        "max_tokens": 500,
        "top_p": 1.0,
    }


@pytest.fixture
def run_prompt_config_json():
    """Configuration for JSON output."""
    return {
        "temperature": 0.0,
        "max_tokens": 1000,
        "response_format": {"type": "json_object"},
    }


# =============================================================================
# Database Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_api_key_objects():
    """Mock ApiKey.objects for database queries."""
    with patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey") as mock:
        mock_entry = Mock()
        mock_entry.key = "test-api-key"
        mock_entry.actual_key = "test-api-key"
        mock_entry.actual_json = None
        mock.objects.get.return_value = mock_entry
        mock.objects.filter.return_value.first.return_value = mock_entry
        yield mock


@pytest.fixture
def mock_custom_model_objects():
    """Mock CustomAIModel.objects for database queries."""
    with patch(
        "agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel"
    ) as mock:
        mock.objects.filter.return_value.values.return_value = []
        mock.objects.get.side_effect = mock.DoesNotExist
        mock.DoesNotExist = Exception
        yield mock


# =============================================================================
# Provider-Specific Fixtures
# =============================================================================


@pytest.fixture
def openai_config():
    """Configuration for OpenAI provider tests."""
    return {
        "provider": "openai",
        "api_key_env": "OPENAI_API_KEY",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
    }


@pytest.fixture
def anthropic_config():
    """Configuration for Anthropic provider tests."""
    return {
        "provider": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "models": ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"],
    }


@pytest.fixture
def groq_config():
    """Configuration for Groq provider tests."""
    return {
        "provider": "groq",
        "api_key_env": "GROQ_API_KEY",
        "models": ["groq/llama-3.3-70b-versatile", "groq/llama-3.1-8b-instant"],
    }


@pytest.fixture
def perplexity_config():
    """Configuration for Perplexity provider tests."""
    return {
        "provider": "perplexity",
        "api_key_env": "PERPLEXITY_API_KEY",
        "models": ["perplexity/sonar-pro", "perplexity/sonar"],
    }


@pytest.fixture
def xai_config():
    """Configuration for xAI provider tests."""
    return {
        "provider": "xai",
        "api_key_env": "XAI_API_KEY",
        "models": ["xai/grok-2-latest", "xai/grok-beta"],
    }


@pytest.fixture
def openrouter_config():
    """Configuration for OpenRouter provider tests."""
    return {
        "provider": "openrouter",
        "api_key_env": "OPENROUTER_API_KEY",
        "models": [
            "openrouter/meta-llama/llama-3-8b-instruct:free",
            "openrouter/openai/gpt-4o",
            "openrouter/anthropic/claude-3.5-sonnet",
        ],
    }


# =============================================================================
# Helper Functions
# =============================================================================


def skip_if_no_api_key(provider: str, api_keys: dict):
    """Skip test if API key for provider is not available."""
    if not api_keys.get(provider):
        pytest.skip(f"Skipping: {provider.upper()}_API_KEY not configured")
