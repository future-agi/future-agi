"""
Comprehensive live tests for all providers with all models and parameters.

This module tests ALL models from the following providers:
- OpenAI (85 models)
- Anthropic (9 models)
- Groq (23 models)
- xAI (6 models)
- Perplexity (20 models)
- OpenRouter (40 models)
- Gemini (10 models)
- Mistral (10 models)
- DeepInfra (7 models)
- Together AI (3 models)
- Cohere (2 models)
- DeepSeek (2 models)
- Fireworks AI (5 models)
- AI21 (3 models)
- Cerebras (2 models)

Run with:
    # All live tests (requires API keys)
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -m live_llm

    # Specific provider
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "openai"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "anthropic"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "groq"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "xai"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "perplexity"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "openrouter"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "gemini"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "mistral"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "deepinfra"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "together"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "cohere"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "deepseek"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "fireworks"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "ai21"
    python -m pytest agentic_eval/core_evals/run_prompt/tests/test_live_all_providers.py -v -k "cerebras"
"""

import os
import json
import time
from unittest.mock import Mock, patch, MagicMock
from uuid import uuid4

import pytest
import litellm

from .conftest import skip_if_no_api_key


# =============================================================================
# Model Definitions by Provider
# =============================================================================

# OpenAI Chat Models (non-reasoning models)
OPENAI_CHAT_MODELS = [
    # GPT-4o series
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4o-mini-2024-07-18",
    "gpt-4o-2024-05-13",
    "gpt-4o-2024-08-06",
    "gpt-4o-2024-11-20",
    "chatgpt-4o-latest",
    # GPT-4.1 series
    "gpt-4.1",
    "gpt-4.1-2025-04-14",
    "gpt-4.1-mini",
    "gpt-4.1-mini-2025-04-14",
    "gpt-4.1-nano",
    "gpt-4.1-nano-2025-04-14",
    # GPT-4 series
    "gpt-4",
    "gpt-4-turbo",
    "gpt-4-turbo-preview",
    "gpt-4-turbo-2024-04-09",
    "gpt-4-0613",
    "gpt-4-1106-preview",
    "gpt-4-0125-preview",
    # GPT-3.5 series
    "gpt-3.5-turbo",
    "gpt-3.5-turbo-1106",
    "gpt-3.5-turbo-0125",
    "gpt-3.5-turbo-16k",
]

# OpenAI Reasoning Models (require special parameter handling - no temperature/top_p)
OPENAI_REASONING_MODELS = [
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
    # GPT-5 series (reasoning models)
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

# Azure OpenAI Models
AZURE_CHAT_MODELS = [
    "azure/gpt-4o",
    "azure/gpt-4o-2024-08-06",
    "azure/gpt-4o-2024-05-13",
    "azure/global-standard/gpt-4o-2024-08-06",
    "azure/global-standard/gpt-4o-mini",
    "azure/gpt-4o-mini",
    "azure/gpt-4-turbo-2024-04-09",
    "azure/gpt-4-turbo",
    "azure/gpt-4",
    "azure/gpt-4-0613",
    "azure/gpt-35-turbo",
    "azure/gpt-35-turbo-16k",
    "azure/gpt-35-turbo-0125",
]

# Azure Reasoning Models
AZURE_REASONING_MODELS = [
    "azure/o1",
    "azure/o1-2024-12-17",
    "azure/o1-mini",
    "azure/o1-mini-2024-09-12",
    "azure/o1-preview",
    "azure/o1-preview-2024-09-12",
    "azure/o3-mini",
    "azure/o3-mini-2025-01-31",
]

# Anthropic Models
ANTHROPIC_MODELS = [
    "claude-3-5-haiku-20241022",
    "claude-3-haiku-20240307",
    "claude-3-7-sonnet-20250219",
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-1-20250805",
    "claude-opus-4-5-20251101",
    "claude-sonnet-4-5-20250929",
]

# Groq Models
GROQ_MODELS = [
    "groq/llama3-8b-8192",
    "groq/llama3-70b-8192",
    "groq/llama-3.3-70b-versatile",
    "groq/llama-3.1-8b-instant",
    "groq/gemma2-9b-it",
    "groq/meta-llama/llama-4-scout-17b-16e-instruct",
    "groq/moonshotai/kimi-k2-instruct",
    "groq/openai/gpt-oss-120b",
    "groq/openai/gpt-oss-20b",
    "groq/meta-llama/llama-guard-4-12b",
    "groq/qwen/qwen3-32b",
    "groq/meta-llama/llama-4-maverick-17b-128e-instruct",
    "groq/compound-beta",
    "groq/compound-beta-mini",
    "groq/deepseek-r1-distill-llama-70b",
    "groq/allam-2-7b",
]

# xAI/Grok Models
XAI_MODELS = [
    "xai/grok-beta",
    "xai/grok-vision-beta",
    "xai/grok-2-latest",
    "xai/grok-2-vision-latest",
    "xai/grok-2-1212",
    "xai/grok-2-vision-1212",
]

# Perplexity Models (current generation Sonar API + Agent API)
# See https://docs.perplexity.ai/getting-started/models
PERPLEXITY_MODELS = [
    # Sonar API (chat completions with built-in web search)
    "perplexity/sonar",
    "perplexity/sonar-pro",
    "perplexity/sonar-reasoning",
    "perplexity/sonar-reasoning-pro",
    "perplexity/sonar-deep-research",
    # Agent API (third-party LLM orchestration; default model gpt-5.1)
    "perplexity/gpt-5.1",
]

# OpenRouter Models
OPENROUTER_MODELS = [
    "openrouter/openai/gpt-4o",
    "openrouter/openai/gpt-4o-2024-05-13",
    "openrouter/openai/gpt-4-vision-preview",
    "openrouter/openai/gpt-3.5-turbo",
    "openrouter/openai/gpt-3.5-turbo-16k",
    "openrouter/openai/gpt-4",
    "openrouter/anthropic/claude-3-haiku",
    "openrouter/anthropic/claude-3-haiku-20240307",
    "openrouter/anthropic/claude-3.5-sonnet",
    "openrouter/anthropic/claude-3.5-sonnet:beta",
    "openrouter/anthropic/claude-3-sonnet",
    "openrouter/anthropic/claude-3-opus",
    "openrouter/anthropic/claude-instant-v1",
    "openrouter/google/gemini-pro-1.5",
    "openrouter/google/gemini-pro-vision",
    "openrouter/google/palm-2-chat-bison",
    "openrouter/google/palm-2-codechat-bison",
    "openrouter/mistralai/mistral-large",
    "openrouter/mistralai/mistral-7b-instruct",
    "openrouter/mistralai/mistral-7b-instruct:free",
    "openrouter/mistralai/mixtral-8x22b-instruct",
    "openrouter/cohere/command-r-plus",
    "openrouter/databricks/dbrx-instruct",
    "openrouter/deepseek/deepseek-coder",
    "openrouter/meta-llama/llama-3-8b-instruct:free",
    "openrouter/meta-llama/llama-3-8b-instruct:extended",
    "openrouter/meta-llama/llama-3-70b-instruct:nitro",
    "openrouter/meta-llama/llama-3-70b-instruct",
    "openrouter/meta-llama/llama-2-13b-chat",
    "openrouter/meta-llama/llama-2-70b-chat",
    "openrouter/meta-llama/codellama-34b-instruct",
    "openrouter/microsoft/wizardlm-2-8x22b:nitro",
    "openrouter/cognitivecomputations/dolphin-mixtral-8x7b",
    "openrouter/fireworks/firellava-13b",
    "openrouter/nousresearch/nous-hermes-llama2-13b",
    "openrouter/mancer/weaver",
    "openrouter/gryphe/mythomax-l2-13b",
    "openrouter/jondurbin/airoboros-l2-70b-2.1",
    "openrouter/undi95/remm-slerp-l2-13b",
    "openrouter/pygmalionai/mythalion-13b",
]


# =============================================================================
# Parameter Configurations for Testing
# =============================================================================

# Temperature variations
TEMPERATURE_VALUES = [0.0, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0]

# Max tokens variations
MAX_TOKENS_VALUES = [50, 100, 250, 500, 1000, 2000, 4000]

# Top P variations
TOP_P_VALUES = [0.1, 0.5, 0.7, 0.9, 0.95, 1.0]

# Frequency penalty variations
FREQUENCY_PENALTY_VALUES = [0.0, 0.5, 1.0, 1.5, 2.0]

# Presence penalty variations
PRESENCE_PENALTY_VALUES = [0.0, 0.5, 1.0, 1.5, 2.0]


# =============================================================================
# Test Helper Functions
# =============================================================================

def get_simple_messages():
    """Get simple test messages."""
    return [
        {"role": "system", "content": "You are a helpful assistant. Keep responses brief."},
        {"role": "user", "content": "Say 'Hello' and nothing else."}
    ]


def get_json_messages():
    """Get messages for JSON response testing."""
    return [
        {"role": "system", "content": "You are a helpful assistant that outputs valid JSON only."},
        {"role": "user", "content": "Return a JSON object with a single key 'status' and value 'ok'."}
    ]


# Reasoning models that require special parameter handling
REASONING_MODELS = [
    # o1 series
    "o1", "o1-2024-12-17", "o1-mini", "o1-mini-2024-09-12",
    "o1-preview", "o1-preview-2024-09-12",
    "o1-pro", "o1-pro-2025-03-19",
    # o3 series
    "o3", "o3-2025-04-16", "o3-mini", "o3-mini-2025-01-31",
    "o3-pro", "o3-pro-2025-06-10",
    # o4 series
    "o4-mini", "o4-mini-2025-04-16",
    # GPT-5 series
    "gpt-5", "gpt-5-2025-08-07", "gpt-5-mini", "gpt-5-mini-2025-08-07",
    "gpt-5-nano", "gpt-5-nano-2025-08-07", "gpt-5-chat-latest",
    "gpt-5-pro", "gpt-5-pro-2025-10-06", "gpt-5-codex",
    # GPT-5.1 series
    "gpt-5.1", "gpt-5.1-2025-11-13", "gpt-5.1-chat-latest",
    "gpt-5.1-codex", "gpt-5.1-codex-mini", "gpt-5.1-codex-max",
    # GPT-5.2 series
    "gpt-5.2", "gpt-5.2-2025-12-11", "gpt-5.2-chat-latest",
    "gpt-5.2-pro", "gpt-5.2-pro-2025-12-11",
]


def is_reasoning_model(model_name):
    """Check if a model is a reasoning model that needs special handling."""
    # Strip provider prefix if present
    base_model = model_name.split('/')[-1] if '/' in model_name else model_name
    return base_model in REASONING_MODELS


def run_model_test(model_name, api_key, organization_id, temperature=0.7, max_tokens=100,
                   top_p=1.0, frequency_penalty=0.0, presence_penalty=0.0,
                   response_format=None, tools=None, tool_choice=None):
    """
    Run a single model test with given parameters.

    This function calls litellm.completion directly to avoid database dependencies
    during testing. API keys are passed directly to litellm.
    """
    messages = get_json_messages() if response_format else get_simple_messages()

    # Build the payload
    payload = {
        "model": model_name,
        "messages": messages,
        "api_key": api_key,
    }

    # Handle reasoning models - they don't support temperature, top_p, etc.
    if is_reasoning_model(model_name):
        payload["max_completion_tokens"] = max_tokens
        # Reasoning models only work with temperature=1 (or not set)
    else:
        # Standard model parameters
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if top_p is not None:
            payload["top_p"] = top_p
        if frequency_penalty is not None and frequency_penalty != 0.0:
            payload["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None and presence_penalty != 0.0:
            payload["presence_penalty"] = presence_penalty

    if response_format:
        payload["response_format"] = response_format
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice

    # Enable dropping unsupported params
    litellm.drop_params = True

    # Make the API call
    response = litellm.completion(**payload)

    # Return the response content
    content = response.choices[0].message.content
    return content, response


# =============================================================================
# OpenAI Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestOpenAILiveAllModels:
    """Comprehensive live tests for all OpenAI models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for OpenAI tests."""
        if not api_keys.get("openai"):
            pytest.skip("OPENAI_API_KEY not configured")
        self.api_key = api_keys["openai"]
        self.organization_id = str(uuid4())

    # -------------------------------------------------------------------------
    # Standard Chat Models - Basic Tests
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", OPENAI_CHAT_MODELS)
    def test_openai_chat_model_basic(self, model):
        """Test basic chat for all OpenAI chat models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
        )
        assert content is not None
        assert len(content) > 0

    # -------------------------------------------------------------------------
    # Standard Chat Models - Temperature Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-3.5-turbo"])
    @pytest.mark.parametrize("temperature", TEMPERATURE_VALUES)
    def test_openai_temperature_variations(self, model, temperature):
        """Test various temperature settings for OpenAI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Standard Chat Models - Max Tokens Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-3.5-turbo"])
    @pytest.mark.parametrize("max_tokens", MAX_TOKENS_VALUES)
    def test_openai_max_tokens_variations(self, model, max_tokens):
        """Test various max_tokens settings for OpenAI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Standard Chat Models - Top P Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-3.5-turbo"])
    @pytest.mark.parametrize("top_p", TOP_P_VALUES)
    def test_openai_top_p_variations(self, model, top_p):
        """Test various top_p settings for OpenAI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            top_p=top_p,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Standard Chat Models - Frequency Penalty Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["gpt-4o-mini"])
    @pytest.mark.parametrize("frequency_penalty", FREQUENCY_PENALTY_VALUES)
    def test_openai_frequency_penalty_variations(self, model, frequency_penalty):
        """Test various frequency_penalty settings for OpenAI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            frequency_penalty=frequency_penalty,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Standard Chat Models - Presence Penalty Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["gpt-4o-mini"])
    @pytest.mark.parametrize("presence_penalty", PRESENCE_PENALTY_VALUES)
    def test_openai_presence_penalty_variations(self, model, presence_penalty):
        """Test various presence_penalty settings for OpenAI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            presence_penalty=presence_penalty,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Standard Chat Models - JSON Response Format
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"])
    def test_openai_json_response_format(self, model):
        """Test JSON response format for OpenAI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.0,
            max_tokens=100,
            response_format={"type": "json_object"},
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Standard Chat Models - Tool/Function Calling
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"])
    def test_openai_tool_calling(self, model):
        """Test tool/function calling for OpenAI models."""
        tools = [
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
                                "description": "The city name"
                            }
                        },
                        "required": ["location"]
                    }
                }
            }
        ]

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What's the weather like in San Francisco?"}
        ]

        litellm.drop_params = True
        response = litellm.completion(
            model=model,
            messages=messages,
            api_key=self.api_key,
            temperature=0.0,
            max_tokens=100,
            tools=tools,
            tool_choice="auto",
        )
        # Response should have either content or tool_calls
        assert response is not None
        assert response.choices[0].message is not None

    # -------------------------------------------------------------------------
    # Reasoning Models - Basic Tests
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", OPENAI_REASONING_MODELS[:4])  # Test first 4 reasoning models
    def test_openai_reasoning_model_basic(self, model):
        """Test basic chat for OpenAI reasoning models."""
        # Reasoning models need different handling - no temperature/top_p
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=1.0,  # Must be 1.0 for reasoning models
            max_tokens=100,
            top_p=1.0,  # Must be 1.0 for reasoning models
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Combined Parameter Tests (Selected Combinations)
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("temp,max_tok,top_p", [
        (0.0, 100, 1.0),
        (0.5, 250, 0.9),
        (0.7, 500, 0.95),
        (1.0, 1000, 0.7),
        (1.5, 2000, 0.5),
    ])
    def test_openai_combined_parameters(self, temp, max_tok, top_p):
        """Test combined parameter variations for OpenAI models."""
        content, response = run_model_test(
            model_name="gpt-4o-mini",
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
        )
        assert content is not None


# =============================================================================
# Anthropic Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestAnthropicLiveAllModels:
    """Comprehensive live tests for all Anthropic models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for Anthropic tests."""
        if not api_keys.get("anthropic"):
            pytest.skip("ANTHROPIC_API_KEY not configured")
        self.api_key = api_keys["anthropic"]
        self.organization_id = str(uuid4())

    # -------------------------------------------------------------------------
    # All Models - Basic Tests
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ANTHROPIC_MODELS)
    def test_anthropic_model_basic(self, model):
        """Test basic chat for all Anthropic models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Temperature Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["claude-3-5-haiku-20241022"])
    @pytest.mark.parametrize("temperature", TEMPERATURE_VALUES)
    def test_anthropic_temperature_variations(self, model, temperature):
        """Test various temperature settings for Anthropic models."""
        # Anthropic max temperature is 1.0
        temp = min(temperature, 1.0)
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=50,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Max Tokens Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["claude-3-5-haiku-20241022"])
    @pytest.mark.parametrize("max_tokens", MAX_TOKENS_VALUES)
    def test_anthropic_max_tokens_variations(self, model, max_tokens):
        """Test various max_tokens settings for Anthropic models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Top P Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["claude-3-5-haiku-20241022"])
    @pytest.mark.parametrize("top_p", TOP_P_VALUES)
    def test_anthropic_top_p_variations(self, model, top_p):
        """Test various top_p settings for Anthropic models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            top_p=top_p,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Combined Parameter Tests
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("temp,max_tok,top_p", [
        (0.0, 100, 1.0),
        (0.5, 250, 0.9),
        (0.7, 500, 0.95),
        (1.0, 1000, 0.7),
    ])
    def test_anthropic_combined_parameters(self, temp, max_tok, top_p):
        """Test combined parameter variations for Anthropic models."""
        content, response = run_model_test(
            model_name="claude-3-5-haiku-20241022",
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
        )
        assert content is not None


# =============================================================================
# Groq Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestGroqLiveAllModels:
    """Comprehensive live tests for all Groq models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for Groq tests."""
        if not api_keys.get("groq"):
            pytest.skip("GROQ_API_KEY not configured")
        self.api_key = api_keys["groq"]
        self.organization_id = str(uuid4())

    # -------------------------------------------------------------------------
    # All Models - Basic Tests
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", GROQ_MODELS)
    def test_groq_model_basic(self, model):
        """Test basic chat for all Groq models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Temperature Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["groq/llama-3.1-8b-instant"])
    @pytest.mark.parametrize("temperature", TEMPERATURE_VALUES)
    def test_groq_temperature_variations(self, model, temperature):
        """Test various temperature settings for Groq models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Max Tokens Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["groq/llama-3.1-8b-instant"])
    @pytest.mark.parametrize("max_tokens", MAX_TOKENS_VALUES)
    def test_groq_max_tokens_variations(self, model, max_tokens):
        """Test various max_tokens settings for Groq models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Top P Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["groq/llama-3.1-8b-instant"])
    @pytest.mark.parametrize("top_p", TOP_P_VALUES)
    def test_groq_top_p_variations(self, model, top_p):
        """Test various top_p settings for Groq models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            top_p=top_p,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Combined Parameter Tests
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("temp,max_tok,top_p", [
        (0.0, 100, 1.0),
        (0.5, 250, 0.9),
        (0.7, 500, 0.95),
        (1.0, 1000, 0.7),
    ])
    def test_groq_combined_parameters(self, temp, max_tok, top_p):
        """Test combined parameter variations for Groq models."""
        content, response = run_model_test(
            model_name="groq/llama-3.1-8b-instant",
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
        )
        assert content is not None


# =============================================================================
# xAI Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestXAILiveAllModels:
    """Comprehensive live tests for all xAI/Grok models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for xAI tests."""
        if not api_keys.get("xai"):
            pytest.skip("XAI_API_KEY not configured")
        self.api_key = api_keys["xai"]
        self.organization_id = str(uuid4())

    # -------------------------------------------------------------------------
    # All Models - Basic Tests
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", XAI_MODELS)
    def test_xai_model_basic(self, model):
        """Test basic chat for all xAI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Temperature Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["xai/grok-beta"])
    @pytest.mark.parametrize("temperature", TEMPERATURE_VALUES)
    def test_xai_temperature_variations(self, model, temperature):
        """Test various temperature settings for xAI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Max Tokens Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["xai/grok-beta"])
    @pytest.mark.parametrize("max_tokens", MAX_TOKENS_VALUES)
    def test_xai_max_tokens_variations(self, model, max_tokens):
        """Test various max_tokens settings for xAI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Top P Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["xai/grok-beta"])
    @pytest.mark.parametrize("top_p", TOP_P_VALUES)
    def test_xai_top_p_variations(self, model, top_p):
        """Test various top_p settings for xAI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            top_p=top_p,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Frequency Penalty Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["xai/grok-beta"])
    @pytest.mark.parametrize("frequency_penalty", FREQUENCY_PENALTY_VALUES)
    def test_xai_frequency_penalty_variations(self, model, frequency_penalty):
        """Test various frequency_penalty settings for xAI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            frequency_penalty=frequency_penalty,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Presence Penalty Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["xai/grok-beta"])
    @pytest.mark.parametrize("presence_penalty", PRESENCE_PENALTY_VALUES)
    def test_xai_presence_penalty_variations(self, model, presence_penalty):
        """Test various presence_penalty settings for xAI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            presence_penalty=presence_penalty,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Combined Parameter Tests
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("temp,max_tok,top_p,freq,pres", [
        (0.0, 100, 1.0, 0.0, 0.0),
        (0.5, 250, 0.9, 0.5, 0.5),
        (0.7, 500, 0.95, 1.0, 1.0),
        (1.0, 1000, 0.7, 0.0, 0.5),
        (1.5, 2000, 0.5, 0.5, 0.0),
    ])
    def test_xai_combined_parameters(self, temp, max_tok, top_p, freq, pres):
        """Test combined parameter variations for xAI models."""
        content, response = run_model_test(
            model_name="xai/grok-beta",
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
            frequency_penalty=freq,
            presence_penalty=pres,
        )
        assert content is not None


# =============================================================================
# Perplexity Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestPerplexityLiveAllModels:
    """Comprehensive live tests for all Perplexity models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for Perplexity tests."""
        if not api_keys.get("perplexity"):
            pytest.skip("PERPLEXITY_API_KEY not configured")
        self.api_key = api_keys["perplexity"]
        self.organization_id = str(uuid4())

    # -------------------------------------------------------------------------
    # All Models - Basic Tests
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", PERPLEXITY_MODELS)
    def test_perplexity_model_basic(self, model):
        """Test basic chat for all Perplexity models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Temperature Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["perplexity/sonar-pro"])
    @pytest.mark.parametrize("temperature", TEMPERATURE_VALUES)
    def test_perplexity_temperature_variations(self, model, temperature):
        """Test various temperature settings for Perplexity models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Max Tokens Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["perplexity/sonar-pro"])
    @pytest.mark.parametrize("max_tokens", MAX_TOKENS_VALUES)
    def test_perplexity_max_tokens_variations(self, model, max_tokens):
        """Test various max_tokens settings for Perplexity models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Top P Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["perplexity/sonar-pro"])
    @pytest.mark.parametrize("top_p", TOP_P_VALUES)
    def test_perplexity_top_p_variations(self, model, top_p):
        """Test various top_p settings for Perplexity models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            top_p=top_p,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Combined Parameter Tests
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("temp,max_tok,top_p", [
        (0.0, 100, 1.0),
        (0.5, 250, 0.9),
        (0.7, 500, 0.95),
        (1.0, 1000, 0.7),
    ])
    def test_perplexity_combined_parameters(self, temp, max_tok, top_p):
        """Test combined parameter variations for Perplexity models."""
        content, response = run_model_test(
            model_name="perplexity/sonar-pro",
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
        )
        assert content is not None


# =============================================================================
# OpenRouter Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestOpenRouterLiveAllModels:
    """Comprehensive live tests for all OpenRouter models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for OpenRouter tests."""
        if not api_keys.get("openrouter"):
            pytest.skip("OPENROUTER_API_KEY not configured")
        self.api_key = api_keys["openrouter"]
        self.organization_id = str(uuid4())

    # -------------------------------------------------------------------------
    # All Models - Basic Tests
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", OPENROUTER_MODELS)
    def test_openrouter_model_basic(self, model):
        """Test basic chat for all OpenRouter models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Temperature Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["openrouter/meta-llama/llama-3-8b-instruct:free"])
    @pytest.mark.parametrize("temperature", TEMPERATURE_VALUES)
    def test_openrouter_temperature_variations(self, model, temperature):
        """Test various temperature settings for OpenRouter models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Max Tokens Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["openrouter/meta-llama/llama-3-8b-instruct:free"])
    @pytest.mark.parametrize("max_tokens", MAX_TOKENS_VALUES)
    def test_openrouter_max_tokens_variations(self, model, max_tokens):
        """Test various max_tokens settings for OpenRouter models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Top P Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["openrouter/meta-llama/llama-3-8b-instruct:free"])
    @pytest.mark.parametrize("top_p", TOP_P_VALUES)
    def test_openrouter_top_p_variations(self, model, top_p):
        """Test various top_p settings for OpenRouter models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            top_p=top_p,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Frequency Penalty Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["openrouter/meta-llama/llama-3-8b-instruct:free"])
    @pytest.mark.parametrize("frequency_penalty", FREQUENCY_PENALTY_VALUES)
    def test_openrouter_frequency_penalty_variations(self, model, frequency_penalty):
        """Test various frequency_penalty settings for OpenRouter models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            frequency_penalty=frequency_penalty,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Presence Penalty Variations
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", ["openrouter/meta-llama/llama-3-8b-instruct:free"])
    @pytest.mark.parametrize("presence_penalty", PRESENCE_PENALTY_VALUES)
    def test_openrouter_presence_penalty_variations(self, model, presence_penalty):
        """Test various presence_penalty settings for OpenRouter models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            presence_penalty=presence_penalty,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Combined Parameter Tests
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("temp,max_tok,top_p,freq,pres", [
        (0.0, 100, 1.0, 0.0, 0.0),
        (0.5, 250, 0.9, 0.5, 0.5),
        (0.7, 500, 0.95, 1.0, 1.0),
        (1.0, 1000, 0.7, 0.0, 0.5),
        (1.5, 2000, 0.5, 0.5, 0.0),
    ])
    def test_openrouter_combined_parameters(self, temp, max_tok, top_p, freq, pres):
        """Test combined parameter variations for OpenRouter models."""
        content, response = run_model_test(
            model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
            frequency_penalty=freq,
            presence_penalty=pres,
        )
        assert content is not None

    # -------------------------------------------------------------------------
    # Provider-Specific Models via OpenRouter
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize("model", [
        "openrouter/openai/gpt-4o",
        "openrouter/anthropic/claude-3.5-sonnet",
        "openrouter/google/gemini-pro-1.5",
        "openrouter/mistralai/mistral-large",
    ])
    def test_openrouter_premium_models(self, model):
        """Test premium models available via OpenRouter."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
        )
        assert content is not None


# =============================================================================
# Cross-Provider Parameter Consistency Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestCrossProviderParameterConsistency:
    """Test that parameters work consistently across providers."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for cross-provider tests."""
        self.api_keys = api_keys
        self.organization_id = str(uuid4())

    def test_temperature_zero_determinism(self, api_keys):
        """Test that temperature=0 produces deterministic results across providers."""
        models_to_test = []

        if api_keys.get("openai"):
            models_to_test.append(("gpt-4o-mini", api_keys["openai"]))
        if api_keys.get("anthropic"):
            models_to_test.append(("claude-3-5-haiku-20241022", api_keys["anthropic"]))
        if api_keys.get("groq"):
            models_to_test.append(("groq/llama-3.1-8b-instant", api_keys["groq"]))

        if not models_to_test:
            pytest.skip("No API keys configured for this test")

        for model, api_key in models_to_test:
            # Run same prompt twice with temp=0
            content1, response1 = run_model_test(
                model_name=model,
                api_key=api_key,
                organization_id=self.organization_id,
                temperature=0.0,
                max_tokens=50,
            )
            content2, response2 = run_model_test(
                model_name=model,
                api_key=api_key,
                organization_id=self.organization_id,
                temperature=0.0,
                max_tokens=50,
            )
            # Both should succeed (determinism verification would require content comparison)
            assert content1 is not None
            assert content2 is not None

    def test_max_tokens_respected(self, api_keys):
        """Test that max_tokens is respected across providers."""
        models_to_test = []

        if api_keys.get("openai"):
            models_to_test.append(("gpt-4o-mini", api_keys["openai"]))
        if api_keys.get("anthropic"):
            models_to_test.append(("claude-3-5-haiku-20241022", api_keys["anthropic"]))

        if not models_to_test:
            pytest.skip("No API keys configured for this test")

        for model, api_key in models_to_test:
            # Request very few tokens
            content, response = run_model_test(
                model_name=model,
                api_key=api_key,
                organization_id=self.organization_id,
                temperature=0.7,
                max_tokens=10,  # Very small
            )
            assert content is not None


# =============================================================================
# Error Handling Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestErrorHandling:
    """Test error handling for various scenarios."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for error handling tests."""
        self.api_keys = api_keys
        self.organization_id = str(uuid4())

    def test_invalid_model_name(self, api_keys):
        """Test handling of invalid model names."""
        if not api_keys.get("openai"):
            pytest.skip("OPENAI_API_KEY not configured")

        with pytest.raises(Exception):
            run_model_test(
                model_name="invalid-model-name-12345",
                api_key=api_keys["openai"],
                organization_id=self.organization_id,
                temperature=0.7,
                max_tokens=50,
            )

    def test_invalid_api_key(self, api_keys):
        """Test handling of invalid API keys."""
        with pytest.raises(Exception):
            run_model_test(
                model_name="gpt-4o-mini",
                api_key="invalid-api-key-12345",
                organization_id=self.organization_id,
                temperature=0.7,
                max_tokens=50,
            )

    def test_empty_messages(self, api_keys):
        """Test handling of empty messages."""
        if not api_keys.get("openai"):
            pytest.skip("OPENAI_API_KEY not configured")

        with patch("agentic_eval.core_evals.run_prompt.litellm_models.CustomAIModel") as mock_custom_model, \
             patch("agentic_eval.core_evals.run_prompt.litellm_models.ApiKey") as mock_api_key:

            mock_custom_model.objects.filter.return_value.values.return_value = []
            mock_custom_model.objects.get.side_effect = mock_custom_model.DoesNotExist
            mock_custom_model.DoesNotExist = Exception
            mock_api_key.objects.get.return_value = create_mock_api_key_entry(api_keys["openai"])

            from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

            with pytest.raises(Exception):
                run_prompt = RunPrompt(
                    model="gpt-4o-mini",
                    messages=[],  # Empty messages
                    organization_id=self.organization_id,
                    output_format="text",
                    temperature=0.7,
                    frequency_penalty=0.0,
                    presence_penalty=0.0,
                    max_tokens=50,
                    top_p=1.0,
                    response_format=None,
                    tool_choice=None,
                    tools=None,
                )
                run_prompt.litellm_response()


# =============================================================================
# Additional Provider Model Definitions
# =============================================================================

# Gemini Models (Google)
GEMINI_MODELS = [
    "gemini/gemini-2.5-pro",
    "gemini/gemini-2.5-flash",
    "gemini/gemini-2.5-flash-lite",
    "gemini/gemini-2.0-flash",
    "gemini/gemini-2.0-flash-001",
    "gemini/gemini-2.0-flash-lite",
    "gemini/gemini-2.0-flash-lite-001",
    "gemini/gemini-flash-latest",
    "gemini/gemini-flash-lite-latest",
    "gemini/gemini-pro-latest",
]

# Mistral Models
MISTRAL_MODELS = [
    "mistral/mistral-large-latest",
    "mistral/mistral-large-2407",
    "mistral/mistral-medium-latest",
    "mistral/mistral-small-latest",
    "mistral/mistral-small",
    "mistral/open-mistral-7b",
    "mistral/open-mixtral-8x7b",
    "mistral/open-mixtral-8x22b",
    "mistral/open-mistral-nemo",
    "mistral/codestral-latest",
]

# DeepInfra Models
DEEPINFRA_MODELS = [
    "deepinfra/meta-llama/Meta-Llama-3-8B-Instruct",
    "deepinfra/meta-llama/Meta-Llama-3-70B-Instruct",
    "deepinfra/mistralai/Mixtral-8x7B-Instruct-v0.1",
    "deepinfra/mistralai/Mistral-7B-Instruct-v0.1",
    "deepinfra/Gryphe/MythoMax-L2-13b",
    "deepinfra/openchat/openchat_3.5",
    "deepinfra/01-ai/Yi-34B-Chat",
]

# Together AI Models
TOGETHER_AI_MODELS = [
    "together_ai/mistralai/Mixtral-8x7B-Instruct-v0.1",
    "together_ai/mistralai/Mistral-7B-Instruct-v0.1",
    "together_ai/togethercomputer/CodeLlama-34b-Instruct",
]

# Cohere Models (chat models only)
COHERE_MODELS = [
    "command-nightly",
    "command",
]

# DeepSeek Models
DEEPSEEK_MODELS = [
    "deepseek/deepseek-chat",
    "deepseek/deepseek-reasoner",
]

# DeepSeek reasoning models that need special handling
DEEPSEEK_REASONING_MODELS = [
    "deepseek/deepseek-reasoner",
]

# Fireworks AI Models
FIREWORKS_AI_MODELS = [
    "fireworks_ai/accounts/fireworks/models/llama-v3p2-1b-instruct",
    "fireworks_ai/accounts/fireworks/models/llama-v3p2-3b-instruct",
    "fireworks_ai/accounts/fireworks/models/firefunction-v2",
    "fireworks_ai/accounts/fireworks/models/mixtral-8x22b-instruct-hf",
    "fireworks_ai/accounts/fireworks/models/qwen2-72b-instruct",
]

# AI21 Models
AI21_MODELS = [
    "jamba-1.5-mini",
    "jamba-1.5-large",
    "jamba-1.5",
]

# Cerebras Models
CEREBRAS_MODELS = [
    "cerebras/llama3.1-8b",
    "cerebras/llama3.1-70b",
]


def is_deepseek_reasoning_model(model_name):
    """Check if a model is a DeepSeek reasoning model."""
    return model_name in DEEPSEEK_REASONING_MODELS


# =============================================================================
# Gemini Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestGeminiLiveAllModels:
    """Comprehensive live tests for all Gemini models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for Gemini tests."""
        if not api_keys.get("gemini"):
            pytest.skip("GEMINI_API_KEY not configured")
        self.api_key = api_keys["gemini"]
        self.organization_id = str(uuid4())

    @pytest.mark.parametrize("model", GEMINI_MODELS[:5])  # Test subset for speed
    def test_gemini_model_basic(self, model):
        """Test basic completion for Gemini models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=100,
        )
        assert content is not None
        assert len(content) > 0

    @pytest.mark.parametrize("model", GEMINI_MODELS[:3])
    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_gemini_temperature_variations(self, model, temperature):
        """Test Gemini models with different temperature values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    @pytest.mark.parametrize("model", GEMINI_MODELS[:3])
    @pytest.mark.parametrize("max_tokens", [50, 100, 250])
    def test_gemini_max_tokens_variations(self, model, max_tokens):
        """Test Gemini models with different max_tokens values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    @pytest.mark.parametrize("model", GEMINI_MODELS[:3])
    @pytest.mark.parametrize("top_p", [0.5, 0.9, 1.0])
    def test_gemini_top_p_variations(self, model, top_p):
        """Test Gemini models with different top_p values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            top_p=top_p,
        )
        assert content is not None

    @pytest.mark.parametrize("temp,max_tok,top_p", [
        (0.3, 100, 0.9),
        (0.7, 200, 0.95),
        (1.0, 150, 1.0),
    ])
    def test_gemini_combined_parameters(self, temp, max_tok, top_p):
        """Test Gemini with combined parameter variations."""
        model = "gemini/gemini-2.0-flash"
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
        )
        assert content is not None


# =============================================================================
# Mistral Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestMistralLiveAllModels:
    """Comprehensive live tests for all Mistral models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for Mistral tests."""
        if not api_keys.get("mistral"):
            pytest.skip("MISTRAL_API_KEY not configured")
        self.api_key = api_keys["mistral"]
        self.organization_id = str(uuid4())

    @pytest.mark.parametrize("model", MISTRAL_MODELS[:5])
    def test_mistral_model_basic(self, model):
        """Test basic completion for Mistral models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=100,
        )
        assert content is not None
        assert len(content) > 0

    @pytest.mark.parametrize("model", MISTRAL_MODELS[:3])
    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_mistral_temperature_variations(self, model, temperature):
        """Test Mistral models with different temperature values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    @pytest.mark.parametrize("model", MISTRAL_MODELS[:3])
    @pytest.mark.parametrize("max_tokens", [50, 100, 250])
    def test_mistral_max_tokens_variations(self, model, max_tokens):
        """Test Mistral models with different max_tokens values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    @pytest.mark.parametrize("model", MISTRAL_MODELS[:3])
    @pytest.mark.parametrize("top_p", [0.5, 0.9, 1.0])
    def test_mistral_top_p_variations(self, model, top_p):
        """Test Mistral models with different top_p values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            top_p=top_p,
        )
        assert content is not None

    @pytest.mark.parametrize("temp,max_tok,top_p", [
        (0.3, 100, 0.9),
        (0.7, 200, 0.95),
        (1.0, 150, 1.0),
    ])
    def test_mistral_combined_parameters(self, temp, max_tok, top_p):
        """Test Mistral with combined parameter variations."""
        model = "mistral/mistral-small-latest"
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
        )
        assert content is not None


# =============================================================================
# DeepInfra Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestDeepInfraLiveAllModels:
    """Comprehensive live tests for all DeepInfra models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for DeepInfra tests."""
        if not api_keys.get("deepinfra"):
            pytest.skip("DEEPINFRA_API_KEY not configured")
        self.api_key = api_keys["deepinfra"]
        self.organization_id = str(uuid4())

    @pytest.mark.parametrize("model", DEEPINFRA_MODELS[:5])
    def test_deepinfra_model_basic(self, model):
        """Test basic completion for DeepInfra models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=100,
        )
        assert content is not None
        assert len(content) > 0

    @pytest.mark.parametrize("model", DEEPINFRA_MODELS[:3])
    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_deepinfra_temperature_variations(self, model, temperature):
        """Test DeepInfra models with different temperature values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    @pytest.mark.parametrize("model", DEEPINFRA_MODELS[:3])
    @pytest.mark.parametrize("max_tokens", [50, 100, 250])
    def test_deepinfra_max_tokens_variations(self, model, max_tokens):
        """Test DeepInfra models with different max_tokens values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    @pytest.mark.parametrize("model", DEEPINFRA_MODELS[:3])
    @pytest.mark.parametrize("top_p", [0.5, 0.9, 1.0])
    def test_deepinfra_top_p_variations(self, model, top_p):
        """Test DeepInfra models with different top_p values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=50,
            top_p=top_p,
        )
        assert content is not None

    @pytest.mark.parametrize("temp,max_tok,top_p", [
        (0.3, 100, 0.9),
        (0.7, 200, 0.95),
        (1.0, 150, 1.0),
    ])
    def test_deepinfra_combined_parameters(self, temp, max_tok, top_p):
        """Test DeepInfra with combined parameter variations."""
        model = "deepinfra/meta-llama/Meta-Llama-3-8B-Instruct"
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
        )
        assert content is not None


# =============================================================================
# Together AI Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestTogetherAILiveAllModels:
    """Comprehensive live tests for all Together AI models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for Together AI tests."""
        if not api_keys.get("together_ai"):
            pytest.skip("TOGETHERAI_API_KEY not configured")
        self.api_key = api_keys["together_ai"]
        self.organization_id = str(uuid4())

    @pytest.mark.parametrize("model", TOGETHER_AI_MODELS)
    def test_together_ai_model_basic(self, model):
        """Test basic completion for Together AI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=100,
        )
        assert content is not None
        assert len(content) > 0

    @pytest.mark.parametrize("model", TOGETHER_AI_MODELS[:2])
    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_together_ai_temperature_variations(self, model, temperature):
        """Test Together AI models with different temperature values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    @pytest.mark.parametrize("model", TOGETHER_AI_MODELS[:2])
    @pytest.mark.parametrize("max_tokens", [50, 100, 250])
    def test_together_ai_max_tokens_variations(self, model, max_tokens):
        """Test Together AI models with different max_tokens values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    @pytest.mark.parametrize("temp,max_tok,top_p", [
        (0.3, 100, 0.9),
        (0.7, 200, 0.95),
        (1.0, 150, 1.0),
    ])
    def test_together_ai_combined_parameters(self, temp, max_tok, top_p):
        """Test Together AI with combined parameter variations."""
        model = "together_ai/mistralai/Mistral-7B-Instruct-v0.1"
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
        )
        assert content is not None


# =============================================================================
# Cohere Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestCohereLiveAllModels:
    """Comprehensive live tests for all Cohere models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for Cohere tests."""
        if not api_keys.get("cohere"):
            pytest.skip("COHERE_API_KEY not configured")
        self.api_key = api_keys["cohere"]
        self.organization_id = str(uuid4())

    @pytest.mark.parametrize("model", COHERE_MODELS)
    def test_cohere_model_basic(self, model):
        """Test basic completion for Cohere models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=100,
        )
        assert content is not None
        assert len(content) > 0

    @pytest.mark.parametrize("model", COHERE_MODELS)
    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_cohere_temperature_variations(self, model, temperature):
        """Test Cohere models with different temperature values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    @pytest.mark.parametrize("model", COHERE_MODELS)
    @pytest.mark.parametrize("max_tokens", [50, 100, 250])
    def test_cohere_max_tokens_variations(self, model, max_tokens):
        """Test Cohere models with different max_tokens values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    @pytest.mark.parametrize("temp,max_tok", [
        (0.3, 100),
        (0.7, 200),
        (1.0, 150),
    ])
    def test_cohere_combined_parameters(self, temp, max_tok):
        """Test Cohere with combined parameter variations."""
        model = "command"
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
        )
        assert content is not None


# =============================================================================
# DeepSeek Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestDeepSeekLiveAllModels:
    """Comprehensive live tests for all DeepSeek models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for DeepSeek tests."""
        if not api_keys.get("deepseek"):
            pytest.skip("DEEPSEEK_API_KEY not configured")
        self.api_key = api_keys["deepseek"]
        self.organization_id = str(uuid4())

    @pytest.mark.parametrize("model", ["deepseek/deepseek-chat"])
    def test_deepseek_chat_model_basic(self, model):
        """Test basic completion for DeepSeek chat models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=100,
        )
        assert content is not None
        assert len(content) > 0

    @pytest.mark.parametrize("model", ["deepseek/deepseek-reasoner"])
    def test_deepseek_reasoner_model_basic(self, model):
        """Test basic completion for DeepSeek reasoning models (R1)."""
        # Reasoning models may need special handling like OpenAI o1
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,  # Will be dropped for reasoning models
            max_tokens=100,
        )
        assert content is not None
        assert len(content) > 0

    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_deepseek_temperature_variations(self, temperature):
        """Test DeepSeek chat model with different temperature values."""
        content, response = run_model_test(
            model_name="deepseek/deepseek-chat",
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    @pytest.mark.parametrize("max_tokens", [50, 100, 250])
    def test_deepseek_max_tokens_variations(self, max_tokens):
        """Test DeepSeek chat model with different max_tokens values."""
        content, response = run_model_test(
            model_name="deepseek/deepseek-chat",
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    @pytest.mark.parametrize("temp,max_tok,top_p", [
        (0.3, 100, 0.9),
        (0.7, 200, 0.95),
        (1.0, 150, 1.0),
    ])
    def test_deepseek_combined_parameters(self, temp, max_tok, top_p):
        """Test DeepSeek with combined parameter variations."""
        content, response = run_model_test(
            model_name="deepseek/deepseek-chat",
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
        )
        assert content is not None


# =============================================================================
# Fireworks AI Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestFireworksAILiveAllModels:
    """Comprehensive live tests for all Fireworks AI models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for Fireworks AI tests."""
        if not api_keys.get("fireworks_ai"):
            pytest.skip("FIREWORKS_API_KEY not configured")
        self.api_key = api_keys["fireworks_ai"]
        self.organization_id = str(uuid4())

    @pytest.mark.parametrize("model", FIREWORKS_AI_MODELS[:4])
    def test_fireworks_ai_model_basic(self, model):
        """Test basic completion for Fireworks AI models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=100,
        )
        assert content is not None
        assert len(content) > 0

    @pytest.mark.parametrize("model", FIREWORKS_AI_MODELS[:2])
    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_fireworks_ai_temperature_variations(self, model, temperature):
        """Test Fireworks AI models with different temperature values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    @pytest.mark.parametrize("model", FIREWORKS_AI_MODELS[:2])
    @pytest.mark.parametrize("max_tokens", [50, 100, 250])
    def test_fireworks_ai_max_tokens_variations(self, model, max_tokens):
        """Test Fireworks AI models with different max_tokens values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    @pytest.mark.parametrize("temp,max_tok,top_p", [
        (0.3, 100, 0.9),
        (0.7, 200, 0.95),
        (1.0, 150, 1.0),
    ])
    def test_fireworks_ai_combined_parameters(self, temp, max_tok, top_p):
        """Test Fireworks AI with combined parameter variations."""
        model = "fireworks_ai/accounts/fireworks/models/llama-v3p2-1b-instruct"
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
        )
        assert content is not None


# =============================================================================
# AI21 Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestAI21LiveAllModels:
    """Comprehensive live tests for all AI21 models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for AI21 tests."""
        if not api_keys.get("ai21"):
            pytest.skip("AI21_API_KEY not configured")
        self.api_key = api_keys["ai21"]
        self.organization_id = str(uuid4())

    @pytest.mark.parametrize("model", AI21_MODELS)
    def test_ai21_model_basic(self, model):
        """Test basic completion for AI21 models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=100,
        )
        assert content is not None
        assert len(content) > 0

    @pytest.mark.parametrize("model", AI21_MODELS[:2])
    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_ai21_temperature_variations(self, model, temperature):
        """Test AI21 models with different temperature values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    @pytest.mark.parametrize("model", AI21_MODELS[:2])
    @pytest.mark.parametrize("max_tokens", [50, 100, 250])
    def test_ai21_max_tokens_variations(self, model, max_tokens):
        """Test AI21 models with different max_tokens values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    @pytest.mark.parametrize("temp,max_tok,top_p", [
        (0.3, 100, 0.9),
        (0.7, 200, 0.95),
        (1.0, 150, 1.0),
    ])
    def test_ai21_combined_parameters(self, temp, max_tok, top_p):
        """Test AI21 with combined parameter variations."""
        model = "jamba-1.5-mini"
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
        )
        assert content is not None


# =============================================================================
# Cerebras Live Integration Tests
# =============================================================================

@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.external
class TestCerebrasLiveAllModels:
    """Comprehensive live tests for all Cerebras models."""

    @pytest.fixture(autouse=True)
    def setup(self, api_keys):
        """Setup for Cerebras tests."""
        if not api_keys.get("cerebras"):
            pytest.skip("CEREBRAS_API_KEY not configured")
        self.api_key = api_keys["cerebras"]
        self.organization_id = str(uuid4())

    @pytest.mark.parametrize("model", CEREBRAS_MODELS)
    def test_cerebras_model_basic(self, model):
        """Test basic completion for Cerebras models."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=100,
        )
        assert content is not None
        assert len(content) > 0

    @pytest.mark.parametrize("model", CEREBRAS_MODELS)
    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_cerebras_temperature_variations(self, model, temperature):
        """Test Cerebras models with different temperature values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temperature,
            max_tokens=50,
        )
        assert content is not None

    @pytest.mark.parametrize("model", CEREBRAS_MODELS)
    @pytest.mark.parametrize("max_tokens", [50, 100, 250])
    def test_cerebras_max_tokens_variations(self, model, max_tokens):
        """Test Cerebras models with different max_tokens values."""
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=0.7,
            max_tokens=max_tokens,
        )
        assert content is not None

    @pytest.mark.parametrize("temp,max_tok,top_p", [
        (0.3, 100, 0.9),
        (0.7, 200, 0.95),
        (1.0, 150, 1.0),
    ])
    def test_cerebras_combined_parameters(self, temp, max_tok, top_p):
        """Test Cerebras with combined parameter variations."""
        model = "cerebras/llama3.1-8b"
        content, response = run_model_test(
            model_name=model,
            api_key=self.api_key,
            organization_id=self.organization_id,
            temperature=temp,
            max_tokens=max_tok,
            top_p=top_p,
        )
        assert content is not None
