"""
Tests for RunPrompt Handler Refactor.

This module contains comprehensive tests for the new modular handler architecture:
- ModelHandlerContext creation and validation
- ModelHandlerFactory routing logic
- Individual handlers: LLMHandler, ImageHandler, TTSHandler, STTHandler, CustomModelHandler
- Integration tests with mocked litellm calls

Run with:
    # All handler tests
    set -a && source .env.test && set +a && .venv/bin/pytest agentic_eval/core_evals/run_prompt/runprompt_handlers/tests/test_handlers.py -v

    # Unit tests only
    set -a && source .env.test && set +a && .venv/bin/pytest agentic_eval/core_evals/run_prompt/runprompt_handlers/tests/test_handlers.py -v -m unit

    # Integration tests only
    set -a && source .env.test && set +a && .venv/bin/pytest agentic_eval/core_evals/run_prompt/runprompt_handlers/tests/test_handlers.py -v -m integration
"""

import os
import time
import json
import base64
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from uuid import uuid4

import pytest


# =============================================================================
# Fixtures
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
def simple_messages():
    """Simple text messages for testing."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, how are you?"},
    ]


@pytest.fixture
def image_prompt_messages():
    """Messages for image generation."""
    return [{"role": "user", "content": "A cute baby sea otter floating on water"}]


@pytest.fixture
def tts_messages():
    """Messages for text-to-speech."""
    return [{"role": "user", "content": "Hello, this is a test of text to speech."}]


@pytest.fixture
def audio_messages():
    """Messages with audio input for STT."""
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": base64.b64encode(b"fake audio data").decode()
                    },
                }
            ],
        }
    ]


@pytest.fixture
def mock_litellm_response():
    """Mock LiteLLM completion response."""
    mock = Mock()
    mock.choices = [Mock()]
    mock.choices[0].message = Mock()
    mock.choices[0].message.content = "This is a test response."
    mock.choices[0].message.tool_calls = None
    mock.usage = Mock()
    mock.usage.prompt_tokens = 10
    mock.usage.completion_tokens = 20
    mock.usage.total_tokens = 30
    mock.model = "gpt-4o"
    return mock


@pytest.fixture
def mock_image_response():
    """Mock LiteLLM image generation response."""
    mock = Mock()
    mock.data = [Mock()]
    mock.data[0].url = "https://example.com/generated-image.png"
    mock.data[0].b64_json = None
    mock.data[0].revised_prompt = "A cute baby sea otter"
    return mock


@pytest.fixture
def mock_speech_response():
    """Mock LiteLLM speech response."""
    mock = Mock()
    mock.content = b"fake audio bytes for tts"
    return mock


@pytest.fixture
def mock_transcription_response():
    """Mock LiteLLM transcription response."""
    mock = Mock()
    mock.text = "This is the transcribed text from audio."
    return mock


# =============================================================================
# Unit Tests - ModelHandlerContext
# =============================================================================


@pytest.mark.unit
class TestModelHandlerContext:
    """Test ModelHandlerContext creation and properties."""

    def test_context_creation_basic(self, mock_organization_id, simple_messages):
        """Test basic context creation."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
        )

        assert context.model == "gpt-4o"
        assert context.messages == simple_messages
        assert context.organization_id == mock_organization_id
        assert context.output_format == "text"  # default

    def test_context_creation_with_all_params(
        self, mock_organization_id, simple_messages
    ):
        """Test context creation with all parameters."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            workspace_id="workspace-123",
            output_format="json",
            temperature=0.7,
            max_tokens=1000,
            top_p=0.9,
            frequency_penalty=0.5,
            presence_penalty=0.5,
            response_format={"type": "json_object"},
            tools=[{"type": "function", "function": {"name": "test"}}],
            run_prompt_config={"voice": "alloy"},
            provider="openai",
            api_key="test-key",
        )

        assert context.temperature == 0.7
        assert context.max_tokens == 1000
        assert context.output_format == "json"
        assert context.provider == "openai"

    def test_context_voice_property(self, mock_organization_id, tts_messages):
        """Test voice property extraction from run_prompt_config."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )

        context = ModelHandlerContext(
            model="openai/tts-1",
            messages=tts_messages,
            organization_id=mock_organization_id,
            output_format="audio",
            run_prompt_config={"voice": "alloy", "voice_id": "voice-123"},
        )

        assert context.voice == "alloy"
        assert context.voice_id == "voice-123"

    def test_context_from_run_prompt_class(self, mock_organization_id, simple_messages):
        """Test creating context from a RunPrompt-like instance."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )

        # Create a mock RunPrompt instance
        mock_run_prompt = Mock()
        mock_run_prompt.model = "gpt-4o"
        mock_run_prompt.messages = simple_messages
        mock_run_prompt.organization_id = mock_organization_id
        mock_run_prompt.workspace_id = "workspace-123"
        mock_run_prompt.output_format = "text"
        mock_run_prompt.temperature = 0.7
        mock_run_prompt.frequency_penalty = 0.0
        mock_run_prompt.presence_penalty = 0.0
        mock_run_prompt.max_tokens = 1000
        mock_run_prompt.top_p = 1.0
        mock_run_prompt.response_format = None
        mock_run_prompt.tool_choice = None
        mock_run_prompt.tools = None
        mock_run_prompt.run_prompt_config = {}
        mock_run_prompt.ws_manager = None

        context = ModelHandlerContext.from_run_prompt(
            mock_run_prompt,
            template_id="template-123",
            version="v1",
            provider="openai",
            api_key="test-key",
        )

        assert context.model == "gpt-4o"
        assert context.template_id == "template-123"
        assert context.provider == "openai"


@pytest.mark.unit
class TestPayloadBuilder:
    """Test LiteLLM payload construction."""

    def test_string_api_key_sets_provider_for_anthropic(
        self, mock_organization_id, simple_messages
    ):
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.payload_builder import (
            PayloadBuilder,
        )

        context = ModelHandlerContext(
            model="claude-3-5-haiku-20241022",
            messages=simple_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            provider="anthropic",
        )

        payload = PayloadBuilder.build_llm_payload(
            context=context,
            provider="anthropic",
            api_key="test-key",
        )

        assert payload["model"] == "claude-3-5-haiku-20241022"
        assert payload["api_key"] == "test-key"
        assert payload["custom_llm_provider"] == "anthropic"

    def test_string_api_key_does_not_set_provider_for_openai(
        self, mock_organization_id, simple_messages
    ):
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.payload_builder import (
            PayloadBuilder,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            provider="openai",
        )

        payload = PayloadBuilder.build_llm_payload(
            context=context,
            provider="openai",
            api_key="test-key",
        )

        assert payload["api_key"] == "test-key"
        assert "custom_llm_provider" not in payload


# =============================================================================
# Unit Tests - ModelHandlerFactory Routing
# =============================================================================


@pytest.mark.unit
class TestModelHandlerFactory:
    """Test ModelHandlerFactory routing logic."""

    def test_factory_routes_to_llm_handler_for_chat(
        self, mock_organization_id, simple_messages
    ):
        """Test factory routes chat models to LLMHandler."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerFactory,
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
        )

        with patch("model_hub.utils.utils.get_model_mode") as mock_mode:
            mock_mode.return_value = "chat"
            handler = ModelHandlerFactory.create_handler(context)

        assert isinstance(handler, LLMHandler)

    def test_factory_routes_to_image_handler(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test factory routes image generation models to ImageHandler."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerFactory,
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="hd/1024-x-1792/dall-e-3",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )

        with patch("model_hub.utils.utils.get_model_mode") as mock_mode:
            mock_mode.return_value = "image_generation"
            handler = ModelHandlerFactory.create_handler(context)

        assert isinstance(handler, ImageHandler)

    def test_factory_routes_to_tts_handler(self, mock_organization_id, tts_messages):
        """Test factory routes TTS models to TTSHandler."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerFactory,
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            TTSHandler,
        )

        context = ModelHandlerContext(
            model="openai/tts-1",
            messages=tts_messages,
            organization_id=mock_organization_id,
            output_format="audio",
        )

        with patch("model_hub.utils.utils.get_model_mode") as mock_mode:
            mock_mode.return_value = "tts"
            handler = ModelHandlerFactory.create_handler(context)

        assert isinstance(handler, TTSHandler)

    def test_factory_routes_to_stt_handler(self, mock_organization_id, audio_messages):
        """Test factory routes STT models to STTHandler."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerFactory,
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            STTHandler,
        )

        context = ModelHandlerContext(
            model="whisper-1",
            messages=audio_messages,
            organization_id=mock_organization_id,
        )

        with patch("model_hub.utils.utils.get_model_mode") as mock_mode:
            mock_mode.return_value = "stt"
            handler = ModelHandlerFactory.create_handler(context)

        assert isinstance(handler, STTHandler)

    def test_factory_routes_gpt_image_models(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test factory routes gpt-image models to ImageHandler."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerFactory,
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="gpt-image-1",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )

        with patch("model_hub.utils.utils.get_model_mode") as mock_mode:
            mock_mode.return_value = "image_generation"
            handler = ModelHandlerFactory.create_handler(context)

        assert isinstance(handler, ImageHandler)

    def test_factory_routes_vertex_ai_imagen(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test factory routes Vertex AI Imagen to ImageHandler."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerFactory,
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="vertex_ai/imagen-3.0-generate-001",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )

        with patch("model_hub.utils.utils.get_model_mode") as mock_mode:
            mock_mode.return_value = "image_generation"
            handler = ModelHandlerFactory.create_handler(context)

        assert isinstance(handler, ImageHandler)


# =============================================================================
# Unit Tests - ImageHandler
# =============================================================================


@pytest.mark.unit
class TestImageHandler:
    """Test ImageHandler functionality."""

    def test_parse_model_name_dalle3_with_prefixes(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test parsing DALL-E 3 model name with HD and size prefixes."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="hd/1024-x-1792/dall-e-3",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )
        handler = ImageHandler(context)

        actual_model, params = handler._parse_image_model_name(
            "hd/1024-x-1792/dall-e-3"
        )

        assert actual_model == "dall-e-3"
        assert params["quality"] == "hd"
        assert params["size"] == "1024x1792"

    def test_parse_model_name_dalle2_with_size(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test parsing DALL-E 2 model name with size prefix."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="256-x-256/dall-e-2",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )
        handler = ImageHandler(context)

        actual_model, params = handler._parse_image_model_name("256-x-256/dall-e-2")

        assert actual_model == "dall-e-2"
        assert params["size"] == "256x256"
        assert "quality" not in params

    def test_parse_model_name_azure_dalle3(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test parsing Azure DALL-E 3 model name."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="azure/standard/1024-x-1024/dall-e-3",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )
        handler = ImageHandler(context)

        actual_model, params = handler._parse_image_model_name(
            "azure/standard/1024-x-1024/dall-e-3"
        )

        assert actual_model == "azure/dall-e-3"
        assert params["quality"] == "standard"
        assert params["size"] == "1024x1024"

    def test_parse_model_name_stable_diffusion_with_steps(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test parsing Stable Diffusion model name with steps."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="512-x-512/50-steps/stability.stable-diffusion-xl-v0",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )
        handler = ImageHandler(context)

        actual_model, params = handler._parse_image_model_name(
            "512-x-512/50-steps/stability.stable-diffusion-xl-v0"
        )

        assert actual_model == "stability.stable-diffusion-xl-v0"
        assert params["size"] == "512x512"
        assert params["steps"] == 50

    def test_parse_model_name_gpt_image_plain(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test parsing plain GPT-Image model name (no prefixes)."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="gpt-image-1",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )
        handler = ImageHandler(context)

        actual_model, params = handler._parse_image_model_name("gpt-image-1")

        assert actual_model == "gpt-image-1"
        assert params == {}

    def test_parse_model_name_vertex_ai_imagen(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test parsing Vertex AI Imagen model name."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="vertex_ai/imagen-3.0-generate-001",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )
        handler = ImageHandler(context)

        actual_model, params = handler._parse_image_model_name(
            "vertex_ai/imagen-3.0-generate-001"
        )

        assert actual_model == "vertex_ai/imagen-3.0-generate-001"
        assert params == {}

    def test_parse_model_name_together_ai_flux(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test parsing Together AI FLUX model name."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="together_ai/black-forest-labs/FLUX.1-schnell",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )
        handler = ImageHandler(context)

        actual_model, params = handler._parse_image_model_name(
            "together_ai/black-forest-labs/FLUX.1-schnell"
        )

        assert actual_model == "together_ai/black-forest-labs/FLUX.1-schnell"
        assert params == {}

    def test_extract_text_from_messages(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test text extraction from messages."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="gpt-image-1",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )
        handler = ImageHandler(context)

        text = handler._extract_text_from_messages()

        assert "sea otter" in text.lower()


# =============================================================================
# Unit Tests - LLMHandler
# =============================================================================


@pytest.mark.unit
class TestLLMHandler:
    """Test LLMHandler functionality."""

    def test_llm_handler_creation(self, mock_organization_id, simple_messages):
        """Test LLMHandler can be instantiated."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
        )

        handler = LLMHandler(context)
        assert handler.context.model == "gpt-4o"

    def test_llm_handler_validates_context(self, mock_organization_id):
        """Test LLMHandler validates required context fields."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        # Missing messages should raise
        context = ModelHandlerContext(
            model="gpt-4o",
            messages=[],  # Empty messages
            organization_id=mock_organization_id,
        )

        with pytest.raises(ValueError, match="Messages are required"):
            LLMHandler(context)


# =============================================================================
# Unit Tests - HandlerResponse
# =============================================================================


@pytest.mark.unit
class TestHandlerResponse:
    """Test HandlerResponse dataclass."""

    def test_handler_response_creation(self):
        """Test HandlerResponse can be created."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            HandlerResponse,
        )

        response = HandlerResponse(
            response="test response",
            start_time=time.time() - 1,
            end_time=time.time(),
            model="gpt-4o",
            metadata={"usage": {"total_tokens": 100}},
        )

        assert response.response == "test response"
        assert response.model == "gpt-4o"
        assert response.runtime > 0

    def test_handler_response_to_value_info(self):
        """Test conversion to legacy value_info format."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            HandlerResponse,
        )

        start = time.time() - 1
        end = time.time()
        response = HandlerResponse(
            response="test response",
            start_time=start,
            end_time=end,
            model="gpt-4o",
            metadata={"usage": {"total_tokens": 100}},
        )

        result, value_info = response.to_value_info()

        assert result == "test response"
        assert value_info["model"] == "gpt-4o"
        assert value_info["data"]["response"] == "test response"
        assert value_info["runtime"] > 0


# =============================================================================
# Integration Tests - ImageHandler with Mocked LiteLLM
# =============================================================================


@pytest.mark.integration
class TestImageHandlerIntegration:
    """Integration tests for ImageHandler with mocked litellm."""

    def test_image_handler_execute_sync(
        self, mock_organization_id, image_prompt_messages, mock_image_response
    ):
        """Test ImageHandler execute_sync with mocked litellm."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="hd/1024-x-1792/dall-e-3",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
        )
        handler = ImageHandler(context)

        with (
            patch("litellm.image_generation") as mock_image_gen,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler.upload_image_to_s3"
            ) as mock_upload,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler.calculate_total_cost"
            ) as mock_cost,
        ):
            mock_image_gen.return_value = mock_image_response
            mock_upload.return_value = "https://s3.example.com/uploaded-image.png"
            mock_cost.return_value = {
                "total_cost": 0.08,
                "prompt_cost": 0.0,
                "completion_cost": 0.08,
            }

            response = handler.execute_sync()

            assert response.response == "https://s3.example.com/uploaded-image.png"
            assert response.model == "hd/1024-x-1792/dall-e-3"
            assert "cost" in response.metadata

            # Verify litellm was called with parsed model name
            mock_image_gen.assert_called_once()
            call_args = mock_image_gen.call_args
            assert call_args.kwargs["model"] == "dall-e-3"
            assert call_args.kwargs["size"] == "1024x1792"

    def test_image_handler_uses_config_overrides(
        self, mock_organization_id, image_prompt_messages, mock_image_response
    ):
        """Test ImageHandler respects run_prompt_config overrides."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="dall-e-3",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            run_prompt_config={
                "size": "512x512",  # Override default
                "quality": "standard",
                "style": "vivid",
                "n": 2,
            },
        )
        handler = ImageHandler(context)

        with (
            patch("litellm.image_generation") as mock_image_gen,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler.upload_image_to_s3"
            ) as mock_upload,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler.calculate_total_cost"
            ) as mock_cost,
        ):
            mock_image_gen.return_value = mock_image_response
            mock_upload.return_value = "https://s3.example.com/image.png"
            mock_cost.return_value = {"total_cost": 0.08}

            handler.execute_sync()

            call_args = mock_image_gen.call_args
            assert call_args.kwargs["size"] == "512x512"
            assert call_args.kwargs["quality"] == "standard"
            assert call_args.kwargs["style"] == "vivid"
            assert call_args.kwargs["n"] == 2


# =============================================================================
# Integration Tests - LLMHandler with Mocked LiteLLM
# =============================================================================


@pytest.mark.integration
class TestLLMHandlerIntegration:
    """Integration tests for LLMHandler with mocked litellm."""

    def test_llm_handler_execute_sync(
        self, mock_organization_id, simple_messages, mock_litellm_response
    ):
        """Test LLMHandler execute_sync with mocked litellm."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            provider="openai",
        )
        handler = LLMHandler(context)

        with (
            patch("litellm.completion") as mock_completion,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.response_formatter.calculate_total_cost"
            ) as mock_cost,
        ):
            mock_completion.return_value = mock_litellm_response
            mock_cost.return_value = {"total_cost": 0.001}

            response = handler.execute_sync(streaming=False)

            assert response.response == "This is a test response."
            assert response.model == "gpt-4o"
            mock_completion.assert_called_once()

    def test_llm_handler_with_tools(
        self, mock_organization_id, simple_messages, mock_litellm_response
    ):
        """Test LLMHandler with tool calling."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            provider="openai",
            tools=tools,
            tool_choice="auto",
        )
        handler = LLMHandler(context)

        with (
            patch("litellm.completion") as mock_completion,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.response_formatter.calculate_total_cost"
            ) as mock_cost,
        ):
            mock_completion.return_value = mock_litellm_response
            mock_cost.return_value = {"total_cost": 0.001}

            response = handler.execute_sync(streaming=False)

            call_args = mock_completion.call_args
            assert "tools" in call_args.kwargs
            assert call_args.kwargs["tools"] == tools


# =============================================================================
# Integration Tests - Full Flow Through Factory
# =============================================================================


@pytest.mark.integration
class TestFullFlowIntegration:
    """Test full flow from factory to handler execution."""

    def test_full_flow_chat_completion(
        self, mock_organization_id, simple_messages, mock_litellm_response
    ):
        """Test full flow for chat completion."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerFactory,
            ModelHandlerContext,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            provider="openai",
        )

        with (
            patch("model_hub.utils.utils.get_model_mode") as mock_mode,
            patch("litellm.completion") as mock_completion,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.response_formatter.calculate_total_cost"
            ) as mock_cost,
        ):
            mock_mode.return_value = "chat"
            mock_completion.return_value = mock_litellm_response
            mock_cost.return_value = {"total_cost": 0.001}

            handler = ModelHandlerFactory.create_handler(context)
            response = handler.execute_sync(streaming=False)

            assert response.response == "This is a test response."

    def test_full_flow_image_generation(
        self, mock_organization_id, image_prompt_messages, mock_image_response
    ):
        """Test full flow for image generation."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerFactory,
            ModelHandlerContext,
        )

        context = ModelHandlerContext(
            model="hd/1024-x-1024/dall-e-3",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
        )

        with (
            patch("model_hub.utils.utils.get_model_mode") as mock_mode,
            patch("litellm.image_generation") as mock_image_gen,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler.upload_image_to_s3"
            ) as mock_upload,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler.calculate_total_cost"
            ) as mock_cost,
        ):
            mock_mode.return_value = "image_generation"
            mock_image_gen.return_value = mock_image_response
            mock_upload.return_value = "https://s3.example.com/image.png"
            mock_cost.return_value = {"total_cost": 0.08}

            handler = ModelHandlerFactory.create_handler(context)
            response = handler.execute_sync()

            assert response.response == "https://s3.example.com/image.png"
            assert "cost" in response.metadata


# =============================================================================
# Tests for Model Mode Detection
# =============================================================================


@pytest.mark.unit
class TestModelModeDetection:
    """Test model mode detection integration."""

    def test_prefixed_dalle3_detected_as_image_generation(self):
        """Test that prefixed DALL-E 3 models are detected correctly."""
        from model_hub.utils.utils import get_model_mode

        mode = get_model_mode("hd/1024-x-1792/dall-e-3")
        assert mode == "image_generation"

    def test_gpt_image_detected_as_image_generation(self):
        """Test that gpt-image models are detected correctly."""
        from model_hub.utils.utils import get_model_mode

        mode = get_model_mode("gpt-image-1")
        assert mode == "image_generation"

    def test_whisper_detected_as_stt(self):
        """Test that whisper is detected as STT."""
        from model_hub.utils.utils import get_model_mode

        mode = get_model_mode("whisper-1")
        assert mode == "stt"

    def test_chat_model_defaults_to_chat(self):
        """Test that chat models default to chat mode."""
        from model_hub.utils.utils import get_model_mode

        mode = get_model_mode("gpt-4o")
        assert mode == "chat"


# =============================================================================
# Tests for Pricing Integration
# =============================================================================


@pytest.mark.unit
class TestPricingIntegration:
    """Test pricing calculation integration."""

    def test_image_pricing_uses_actual_model(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test that pricing uses actual (parsed) model name."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )
        from agentic_eval.core_evals.fi_utils.token_count_helper import (
            calculate_total_cost,
        )

        context = ModelHandlerContext(
            model="hd/1024-x-1792/dall-e-3",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )
        handler = ImageHandler(context)

        # Parse model name
        actual_model, params = handler._parse_image_model_name(context.model)

        # Calculate cost with actual model name (stripped)
        usage = {"images_generated": 1, "quality": "hd"}
        cost = calculate_total_cost(actual_model, usage)

        assert cost["total_cost"] > 0
        assert "pricing_source" in cost


# =============================================================================
# Async Execution Tests
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestAsyncExecution:
    """Test async execution for handlers."""

    async def test_llm_handler_execute_async(
        self, mock_organization_id, simple_messages, mock_litellm_response
    ):
        """Test LLMHandler execute_async with mocked litellm."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            provider="openai",
        )
        handler = LLMHandler(context)

        with (
            patch("litellm.acompletion", new_callable=AsyncMock) as mock_completion,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.response_formatter.calculate_total_cost"
            ) as mock_cost,
        ):
            mock_completion.return_value = mock_litellm_response
            mock_cost.return_value = {"total_cost": 0.001}

            response = await handler.execute_async(streaming=False)

            assert response.response == "This is a test response."
            assert response.model == "gpt-4o"
            mock_completion.assert_called_once()

    async def test_image_handler_execute_async(
        self, mock_organization_id, image_prompt_messages, mock_image_response
    ):
        """Test ImageHandler execute_async with mocked litellm."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="dall-e-3",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
        )
        handler = ImageHandler(context)

        with (
            patch("litellm.image_generation") as mock_image_gen,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler.upload_image_to_s3"
            ) as mock_upload,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler.calculate_total_cost"
            ) as mock_cost,
        ):
            mock_image_gen.return_value = mock_image_response
            mock_upload.return_value = "https://s3.example.com/async-image.png"
            mock_cost.return_value = {"total_cost": 0.04}

            response = await handler.execute_async()

            assert response.response == "https://s3.example.com/async-image.png"
            assert "cost" in response.metadata


# =============================================================================
# STTHandler Execution Tests
# =============================================================================


@pytest.mark.integration
class TestSTTHandlerExecution:
    """Test STTHandler execution with mocked dependencies."""

    def test_standard_stt_handler_execute_sync(
        self, mock_organization_id, audio_messages, mock_transcription_response
    ):
        """Test StandardSTTHandler execute_sync with mocked litellm."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.stt_handler import (
            StandardSTTHandler,
        )

        context = ModelHandlerContext(
            model="whisper-1",
            messages=audio_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            provider="openai",
        )
        handler = StandardSTTHandler(context)

        with (
            patch("litellm.transcription") as mock_transcription,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor.AudioProcessor.normalize_audio_for_stt"
            ) as mock_normalize,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor.AudioProcessor.create_audio_buffer"
            ) as mock_buffer,
        ):
            mock_transcription.return_value = mock_transcription_response
            mock_normalize.return_value = (b"fake audio bytes", "mp3")
            mock_buffer.return_value = MagicMock(name="audio.mp3")

            response = handler.execute_sync()

            assert response.response == "This is the transcribed text from audio."
            assert response.model == "whisper-1"
            mock_transcription.assert_called_once()

    def test_stt_handler_routes_to_standard_handler(
        self, mock_organization_id, audio_messages
    ):
        """Test STTHandler routes to StandardSTTHandler for whisper models."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            STTHandler,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.stt_handler import (
            StandardSTTHandler,
        )

        context = ModelHandlerContext(
            model="whisper-1",
            messages=audio_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            provider="openai",
        )

        with patch(
            "agentic_eval.core_evals.run_prompt.other_services.manager.OtherServicesManager.get_transcription_handler"
        ) as mock_get_handler:
            mock_get_handler.return_value = None  # No custom handler
            handler = STTHandler(context)

            assert isinstance(handler._handler, StandardSTTHandler)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSTTHandlerAsyncExecution:
    """Test STTHandler async execution."""

    async def test_standard_stt_handler_execute_async(
        self, mock_organization_id, audio_messages, mock_transcription_response
    ):
        """Test StandardSTTHandler execute_async with mocked litellm."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.stt_handler import (
            StandardSTTHandler,
        )

        context = ModelHandlerContext(
            model="whisper-1",
            messages=audio_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            provider="openai",
        )
        handler = StandardSTTHandler(context)

        with (
            patch("litellm.transcription") as mock_transcription,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor.AudioProcessor.normalize_audio_for_stt"
            ) as mock_normalize,
            patch(
                "agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor.AudioProcessor.create_audio_buffer"
            ) as mock_buffer,
        ):
            mock_transcription.return_value = mock_transcription_response
            mock_normalize.return_value = (b"fake audio bytes", "mp3")
            mock_buffer.return_value = MagicMock(name="audio.mp3")

            response = await handler.execute_async()

            assert response.response == "This is the transcribed text from audio."
            assert response.model == "whisper-1"


# =============================================================================
# TTSHandler Execution and Routing Tests
# =============================================================================


@pytest.mark.unit
class TestTTSHandlerRouting:
    """Test TTSHandler routing logic."""

    def test_tts_handler_routes_to_speech_api_handler(
        self, mock_organization_id, tts_messages
    ):
        """Test TTSHandler routes to SpeechAPIHandler for standard TTS models."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            TTSHandler,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.speech_api_handler import (
            SpeechAPIHandler,
        )

        context = ModelHandlerContext(
            model="tts-1",
            messages=tts_messages,
            organization_id=mock_organization_id,
            output_format="audio",
            api_key="test-key",
            provider="openai",
        )

        with patch(
            "agentic_eval.core_evals.run_prompt.other_services.manager.OtherServicesManager.get_speech_handler"
        ) as mock_get_handler:
            mock_get_handler.return_value = None
            handler = TTSHandler(context)
            sub_handler = handler._get_sub_handler()

            assert isinstance(sub_handler, SpeechAPIHandler)

    def test_tts_handler_routes_to_audio_preview_for_gpt4o(
        self, mock_organization_id, tts_messages
    ):
        """Test TTSHandler routes to AudioPreviewHandler for gpt-4o-audio-preview."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            TTSHandler,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.audio_preview_handler import (
            AudioPreviewHandler,
        )

        context = ModelHandlerContext(
            model="gpt-4o-audio-preview",
            messages=tts_messages,
            organization_id=mock_organization_id,
            output_format="audio",
            api_key="test-key",
            provider="openai",
        )

        with patch(
            "agentic_eval.core_evals.run_prompt.other_services.manager.OtherServicesManager.get_speech_handler"
        ) as mock_get_handler:
            mock_get_handler.return_value = None
            handler = TTSHandler(context)
            sub_handler = handler._get_sub_handler()

            assert isinstance(sub_handler, AudioPreviewHandler)

    def test_tts_handler_routes_to_speech_api_for_gemini(
        self, mock_organization_id, tts_messages
    ):
        """Test TTSHandler routes to SpeechAPIHandler for Google AI Studio Gemini TTS.

        Note: LiteLLM has a built-in bridge that converts speech() calls to
        completion() calls for Google AI Studio Gemini TTS models (gemini/*-tts).
        """
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            TTSHandler,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.speech_api_handler import (
            SpeechAPIHandler,
        )

        context = ModelHandlerContext(
            model="gemini/gemini-2.5-flash-preview-tts",
            messages=tts_messages,
            organization_id=mock_organization_id,
            output_format="audio",
            api_key="test-key",
            provider="google",
        )

        with patch(
            "agentic_eval.core_evals.run_prompt.other_services.manager.OtherServicesManager.get_speech_handler"
        ) as mock_get_handler:
            mock_get_handler.return_value = None
            handler = TTSHandler(context)
            sub_handler = handler._get_sub_handler()

            assert isinstance(sub_handler, SpeechAPIHandler)

    def test_tts_handler_routes_to_audio_preview_for_vertex_ai_gemini(
        self, mock_organization_id, tts_messages
    ):
        """Test TTSHandler routes to AudioPreviewHandler for Vertex AI Gemini TTS.

        Note: Vertex AI Gemini TTS (vertex_ai/gemini-*-tts) MUST use completion
        endpoint with modalities=["audio"]. litellm.speech() does NOT work.
        See: https://docs.litellm.ai/docs/providers/vertex_speech#gemini-tts
        """
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            TTSHandler,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.audio_preview_handler import (
            AudioPreviewHandler,
        )

        context = ModelHandlerContext(
            model="vertex_ai/gemini-2.5-flash-preview-tts",
            messages=tts_messages,
            organization_id=mock_organization_id,
            output_format="audio",
            api_key="test-key",
            provider="vertex_ai",
        )

        with patch(
            "agentic_eval.core_evals.run_prompt.other_services.manager.OtherServicesManager.get_speech_handler"
        ) as mock_get_handler:
            mock_get_handler.return_value = None
            handler = TTSHandler(context)
            sub_handler = handler._get_sub_handler()

            assert isinstance(sub_handler, AudioPreviewHandler)

    def test_is_audio_preview_model_detection(self, mock_organization_id, tts_messages):
        """Test _is_audio_preview_model correctly detects audio preview models.

        Models that need AudioPreviewHandler (completion endpoint):
        - gpt-4o-audio-preview
        - vertex_ai/gemini-*-tts (Vertex AI Gemini TTS)

        Models that use SpeechAPIHandler:
        - Standard TTS (tts-1, etc.)
        - Google AI Studio Gemini (gemini/*-tts) - has speech-to-completion bridge
        """
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            TTSHandler,
        )

        # Models that MUST use AudioPreviewHandler (completion endpoint)
        audio_preview_models = [
            "gpt-4o-audio-preview",
            "gpt-4o-audio-preview-2024-10-01",
            "vertex_ai/gemini-2.5-flash-preview-tts",  # Vertex AI requires completion
            "vertex_ai/gemini-2.5-pro-preview-tts",  # Vertex AI requires completion
        ]

        # Models that use SpeechAPIHandler
        non_audio_preview_models = [
            "tts-1",
            "tts-1-hd",
            "openai/tts-1",
            "vertex_ai/chirp",  # Standard Vertex AI TTS (not Gemini)
            "gemini/gemini-2.5-flash-preview-tts",  # Google AI Studio has speech bridge
        ]

        for model in audio_preview_models:
            context = ModelHandlerContext(
                model=model,
                messages=tts_messages,
                organization_id=mock_organization_id,
                output_format="audio",
            )
            handler = TTSHandler(context)
            assert handler._is_audio_preview_model(), f"{model} should be audio preview"

        for model in non_audio_preview_models:
            context = ModelHandlerContext(
                model=model,
                messages=tts_messages,
                organization_id=mock_organization_id,
                output_format="audio",
            )
            handler = TTSHandler(context)
            assert not handler._is_audio_preview_model(), (
                f"{model} should NOT be audio preview"
            )


@pytest.mark.integration
class TestTTSHandlerExecution:
    """Test TTSHandler execution with mocked dependencies."""

    def test_tts_handler_validates_output_format(
        self, mock_organization_id, tts_messages
    ):
        """Test TTSHandler validates output_format is 'audio'."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            TTSHandler,
        )

        context = ModelHandlerContext(
            model="tts-1",
            messages=tts_messages,
            organization_id=mock_organization_id,
            output_format="text",  # Wrong format for TTS
            api_key="test-key",
        )

        with patch(
            "agentic_eval.core_evals.run_prompt.other_services.manager.OtherServicesManager.get_speech_handler"
        ) as mock_get_handler:
            mock_get_handler.return_value = None
            handler = TTSHandler(context)
            response = handler.execute_sync()

            # Should fail validation
            assert response.failure is not None
            assert "output_format" in response.failure


# =============================================================================
# Error Handling and Retry Tests
# =============================================================================


@pytest.mark.unit
class TestErrorHandlingAndRetries:
    """Test error handling and retry logic in handlers."""

    def test_is_timeout_error_detection(self, mock_organization_id, simple_messages):
        """Test _is_timeout_error correctly identifies timeout errors."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
        )
        handler = LLMHandler(context)

        # Should be detected as timeout errors
        timeout_errors = [
            Exception("Connection timeout occurred"),
            Exception("Request timed out"),
            Exception("read timeout"),
            Exception("Too many requests"),
        ]

        for error in timeout_errors:
            assert handler._is_timeout_error(error), f"Should detect: {error}"

        # Should NOT be detected as timeout errors
        non_timeout_errors = [
            Exception("Invalid API key"),
            Exception("Model not found"),
            ValueError("Invalid parameter"),
        ]

        for error in non_timeout_errors:
            assert not handler._is_timeout_error(error), f"Should NOT detect: {error}"

    def test_retry_on_timeout_succeeds_after_retry(
        self, mock_organization_id, simple_messages
    ):
        """Test _retry_on_timeout succeeds after a timeout error."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
        )
        handler = LLMHandler(context)

        call_count = 0

        def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Connection timeout")
            return "success"

        with patch("time.sleep"):  # Don't actually sleep in tests
            result = handler._retry_on_timeout(
                flaky_function,
                max_retries=3,
                initial_delay=0.1,
            )

        assert result == "success"
        assert call_count == 2  # First call failed, second succeeded

    def test_retry_on_timeout_exhausts_retries(
        self, mock_organization_id, simple_messages
    ):
        """Test _retry_on_timeout raises after exhausting retries."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
        )
        handler = LLMHandler(context)

        def always_timeout():
            raise Exception("Connection timeout")

        with (
            patch("time.sleep"),
            pytest.raises(Exception, match="Connection timeout"),
        ):
            handler._retry_on_timeout(
                always_timeout,
                max_retries=3,
                initial_delay=0.1,
            )

    def test_retry_does_not_retry_non_timeout_errors(
        self, mock_organization_id, simple_messages
    ):
        """Test _retry_on_timeout does not retry non-timeout errors."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
        )
        handler = LLMHandler(context)

        call_count = 0

        def auth_error():
            nonlocal call_count
            call_count += 1
            raise Exception("Invalid API key")

        with pytest.raises(Exception, match="Invalid API key"):
            handler._retry_on_timeout(
                auth_error,
                max_retries=3,
            )

        # Should only be called once (no retries for non-timeout errors)
        assert call_count == 1

    def test_llm_handler_returns_error_on_failure(
        self, mock_organization_id, simple_messages
    ):
        """Test LLMHandler properly handles and returns errors."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            provider="openai",
        )
        handler = LLMHandler(context)

        with patch("litellm.completion") as mock_completion:
            mock_completion.side_effect = Exception("API Error: Rate limit exceeded")

            # The litellm_try_except wrapper transforms errors to a generic message
            # for user-facing display, so we check that an exception is raised
            with pytest.raises(Exception):
                handler.execute_sync(streaming=False)


@pytest.mark.integration
@pytest.mark.asyncio
class TestAsyncRetries:
    """Test async retry logic."""

    async def test_retry_on_timeout_async_succeeds(
        self, mock_organization_id, simple_messages
    ):
        """Test _retry_on_timeout_async succeeds after retry."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
        )
        handler = LLMHandler(context)

        call_count = 0

        def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("timeout")
            return "async_success"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await handler._retry_on_timeout_async(
                flaky_function,
                max_retries=3,
                initial_delay=0.1,
            )

        assert result == "async_success"
        assert call_count == 2


# =============================================================================
# Streaming Tests
# =============================================================================


@pytest.mark.integration
class TestLLMStreaming:
    """Test LLM streaming functionality."""

    def test_streaming_with_websocket_manager(
        self, mock_organization_id, simple_messages
    ):
        """Test LLM streaming sends chunks via WebSocket."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        # Create mock WebSocket manager
        mock_ws_manager = MagicMock()
        mock_ws_manager.is_streaming_stopped.return_value = False

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            provider="openai",
            ws_manager=mock_ws_manager,
            template_id="test-template",
            version="v1",
            result_index=0,
            num_results=1,
        )
        handler = LLMHandler(context)

        # Create mock streaming response
        mock_chunk1 = MagicMock()
        mock_chunk1.choices = [MagicMock()]
        mock_chunk1.choices[0].delta = MagicMock()
        mock_chunk1.choices[0].delta.content = "Hello"
        mock_chunk1.choices[0].delta.tool_calls = None
        mock_chunk1.model = "gpt-4o"
        mock_chunk1.usage = None

        mock_chunk2 = MagicMock()
        mock_chunk2.choices = [MagicMock()]
        mock_chunk2.choices[0].delta = MagicMock()
        mock_chunk2.choices[0].delta.content = " World!"
        mock_chunk2.choices[0].delta.tool_calls = None
        mock_chunk2.model = "gpt-4o"
        mock_chunk2.usage = MagicMock()
        mock_chunk2.usage.prompt_tokens = 10
        mock_chunk2.usage.completion_tokens = 5
        mock_chunk2.usage.total_tokens = 15

        with patch("litellm.completion") as mock_completion:
            mock_completion.return_value = iter([mock_chunk1, mock_chunk2])

            response = handler.execute_sync(streaming=True)

            # Verify response contains accumulated content
            assert "Hello" in response.response
            assert "World" in response.response

            # Verify WebSocket methods were called
            mock_ws_manager.send_started_message.assert_called_once()
            mock_ws_manager.send_completed_message.assert_called_once()

    def test_streaming_respects_stop_signal(
        self, mock_organization_id, simple_messages
    ):
        """Test LLM streaming stops when stop signal is received."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        mock_ws_manager = MagicMock()
        # First call returns False, second returns True (stop)
        mock_ws_manager.is_streaming_stopped.side_effect = [False, True]

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            api_key="test-key",
            provider="openai",
            ws_manager=mock_ws_manager,
            template_id="test-template",
            version="v1",
            result_index=0,
            num_results=1,
        )
        handler = LLMHandler(context)

        # Create mock chunks
        mock_chunk = MagicMock()
        mock_chunk.choices = [MagicMock()]
        mock_chunk.choices[0].delta = MagicMock()
        mock_chunk.choices[0].delta.content = "Partial"
        mock_chunk.choices[0].delta.tool_calls = None
        mock_chunk.model = "gpt-4o"

        with patch("litellm.completion") as mock_completion:
            mock_completion.return_value = iter([mock_chunk, mock_chunk])

            response = handler.execute_sync(streaming=True)

            # Verify stopped message was sent
            mock_ws_manager.send_stopped_message.assert_called()
            mock_ws_manager.cleanup_streaming_data.assert_called()


# =============================================================================
# Edge Case Tests
# =============================================================================


@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_context_with_none_tools(self, mock_organization_id, simple_messages):
        """Test handler works when tools is None."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            LLMHandler,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            tools=None,  # Explicitly None
        )

        # Should not raise
        handler = LLMHandler(context)
        assert handler.context.tools is None or handler.context.tools == []

    def test_empty_run_prompt_config(self, mock_organization_id, simple_messages):
        """Test handler works with empty run_prompt_config."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )

        context = ModelHandlerContext(
            model="gpt-4o",
            messages=simple_messages,
            organization_id=mock_organization_id,
            run_prompt_config={},
        )

        assert context.voice is None
        assert context.voice_id is None
        assert context.audio_format is None

    def test_extract_text_from_multipart_messages(self, mock_organization_id):
        """Test text extraction from multipart messages with images."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        multipart_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image:"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/img.png"},
                    },
                ],
            }
        ]

        context = ModelHandlerContext(
            model="gpt-image-1",
            messages=multipart_messages,
            organization_id=mock_organization_id,
        )
        handler = ImageHandler(context)

        text = handler._extract_text_from_messages()
        assert "Describe this image:" in text

    def test_handler_response_runtime_calculation(self):
        """Test HandlerResponse calculates runtime correctly."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            HandlerResponse,
        )

        start = 1000.0
        end = 1002.5  # 2.5 seconds later

        response = HandlerResponse(
            response="test",
            start_time=start,
            end_time=end,
            model="test-model",
        )

        # Runtime should be in milliseconds
        assert response.runtime == 2500.0  # 2.5 * 1000

    def test_image_handler_with_max_size_prefix(
        self, mock_organization_id, image_prompt_messages
    ):
        """Test ImageHandler handles max-x-max size prefix."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
            ImageHandler,
        )

        context = ModelHandlerContext(
            model="max-x-max/gpt-image-1",
            messages=image_prompt_messages,
            organization_id=mock_organization_id,
        )
        handler = ImageHandler(context)

        actual_model, params = handler._parse_image_model_name("max-x-max/gpt-image-1")

        assert actual_model == "gpt-image-1"
        assert params["size"] == "maxxmax"

    def test_audio_preview_handler_detects_vertex_ai_gemini(
        self, mock_organization_id, tts_messages
    ):
        """Test AudioPreviewHandler correctly detects Vertex AI Gemini models."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
            ModelHandlerContext,
        )
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.audio_preview_handler import (
            AudioPreviewHandler,
        )

        # Test Vertex AI Gemini model
        context = ModelHandlerContext(
            model="vertex_ai/gemini-2.5-pro-preview-tts",
            messages=tts_messages,
            organization_id=mock_organization_id,
            output_format="audio",
        )
        handler = AudioPreviewHandler(context)
        assert handler._is_gemini_model(), (
            "vertex_ai/gemini should be detected as Gemini"
        )

        # Test direct Gemini model
        context2 = ModelHandlerContext(
            model="gemini/gemini-2.5-flash-preview-tts",
            messages=tts_messages,
            organization_id=mock_organization_id,
            output_format="audio",
        )
        handler2 = AudioPreviewHandler(context2)
        assert handler2._is_gemini_model(), "gemini/ should be detected as Gemini"

        # Test non-Gemini model
        context3 = ModelHandlerContext(
            model="gpt-4o-audio-preview",
            messages=tts_messages,
            organization_id=mock_organization_id,
            output_format="audio",
        )
        handler3 = AudioPreviewHandler(context3)
        assert not handler3._is_gemini_model(), (
            "gpt-4o should NOT be detected as Gemini"
        )

    def test_build_speech_params_for_vertex_ai_gemini_tts(self):
        """Test AudioProcessor.build_speech_params handles Vertex AI Gemini TTS correctly."""
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor import (
            AudioProcessor,
        )

        # Test Vertex AI Gemini TTS - should use string voice like Gemini, not dict
        params = AudioProcessor.build_speech_params(
            model="vertex_ai/gemini-2.5-pro-preview-tts",
            input_text="Hello world",
            api_key="test-key",
            run_prompt_config={"voice": "Kore"},
        )

        assert params["model"] == "vertex_ai/gemini-2.5-pro-preview-tts"
        assert params["input"] == "Hello world"
        assert params["voice"] == "Kore"  # String voice, not dict

        # Test standard Vertex AI Chirp - should use dict voice
        params_chirp = AudioProcessor.build_speech_params(
            model="vertex_ai/chirp",
            input_text="Hello world",
            api_key="test-key",
            run_prompt_config={
                "voice": {"languageCode": "en-US", "name": "en-US-Chirp3-HD-Charon"}
            },
        )

        assert params_chirp["model"] == "vertex_ai/chirp"
        assert isinstance(params_chirp["voice"], dict)  # Dict voice for Chirp


# =============================================================================
# Unit Tests - ResponseFormatter output_format parsing
# =============================================================================


@pytest.mark.unit
class TestResponseFormatterOutputFormat:
    """format_llm_response parses output_format into a structured value."""

    def _response(self, content):
        from unittest.mock import Mock

        mock = Mock()
        mock.choices = [Mock()]
        mock.choices[0].message = Mock()
        mock.choices[0].message.content = content
        mock.choices[0].message.tool_calls = None
        mock.usage = Mock()
        mock.usage.prompt_tokens = 10
        mock.usage.completion_tokens = 20
        mock.usage.total_tokens = 30
        mock.model = "gpt-4o"
        return mock

    def _format(self, content, output_format):
        from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.response_formatter import (  # noqa: E501
            ResponseFormatter,
        )

        formatted, _ = ResponseFormatter.format_llm_response(
            response=self._response(content),
            model="gpt-4o",
            start_time=time.time(),
            output_format=output_format,
        )
        return formatted

    def test_array_output_format_returns_list(self):
        """A JSON-array response is parsed to a list, not left as a string."""
        formatted = self._format('["Paris", "France"]', "array")
        assert formatted == ["Paris", "France"]
        assert isinstance(formatted, list)

    def test_array_output_format_strips_code_fence(self):
        """A fenced ```json array is unwrapped and parsed to a list."""
        content = '```json\n["Paris", "France"]\n```'
        formatted = self._format(content, "array")
        assert formatted == ["Paris", "France"]

    def test_array_output_format_parses_python_literal(self):
        """Single-quoted array (Python literal, not valid JSON) still parses."""
        formatted = self._format("['Paris', 'France']", "array")
        assert formatted == ["Paris", "France"]

    def test_array_output_format_empty_array(self):
        """An empty array parses to an empty list, not a raw string."""
        formatted = self._format("[]", "array")
        assert formatted == []
        assert isinstance(formatted, list)

    def test_array_output_format_unparseable_falls_back_to_raw(self):
        """Malformed array content degrades to the raw string, never raises."""
        formatted = self._format("not an array at all", "array")
        assert formatted == "not an array at all"

    def test_object_output_format_returns_dict(self):
        """A JSON-object response is parsed to a dict."""
        formatted = self._format('{"city": "Paris"}', "object")
        assert formatted == {"city": "Paris"}
        assert isinstance(formatted, dict)

    def test_number_output_format_returns_float(self):
        """A numeric response is parsed to a float."""
        formatted = self._format("42.5", "number")
        assert formatted == 42.5
        assert isinstance(formatted, float)

    def test_string_output_format_passthrough_unchanged(self):
        """The common string path is unchanged — raw content is returned."""
        formatted = self._format("This is a plain answer.", "string")
        assert formatted == "This is a plain answer."


# =============================================================================
# Smallest AI TTS + STT Tests
# =============================================================================


@pytest.mark.unit
class TestSmallestAITTSParameters:
    """Unit tests for Smallest AI TTS parameter schema helpers."""

    def test_tts_parameters_lightning_v3_1(self):
        from agentic_eval.core_evals.run_prompt.other_services.smallest_ai_response import (
            get_smallest_ai_tts_parameters,
        )

        params = get_smallest_ai_tts_parameters("lightning_v3.1")
        assert "dropdowns" in params
        assert "sliders" in params
        voice_dropdown = next(d for d in params["dropdowns"] if d["label"] == "voice_id")
        assert "quinn" in voice_dropdown["options"]
        assert "neel" in voice_dropdown["options"]
        speed_slider = next(s for s in params["sliders"] if s["label"] == "speed")
        assert speed_slider["min"] == 0.5
        assert speed_slider["max"] == 2.0

    def test_tts_parameters_lightning_v3_1_pro(self):
        from agentic_eval.core_evals.run_prompt.other_services.smallest_ai_response import (
            get_smallest_ai_tts_parameters,
        )

        params = get_smallest_ai_tts_parameters("lightning_v3.1_pro")
        voice_dropdown = next(d for d in params["dropdowns"] if d["label"] == "voice_id")
        # Pro voices only — no US voices like avery
        assert "benedict" in voice_dropdown["options"]
        assert "rhea" in voice_dropdown["options"]
        # Pro supports 29 languages (en, hi, + 27 more), not just en/hi/auto
        lang_dropdown = next(d for d in params["dropdowns"] if d["label"] == "language")
        assert "en" in lang_dropdown["options"]
        assert "hi" in lang_dropdown["options"]
        assert "ar" in lang_dropdown["options"]
        assert "auto" not in lang_dropdown["options"]
        assert len(lang_dropdown["options"]) == 29

    def test_stt_parameters_pulse(self):
        from agentic_eval.core_evals.run_prompt.other_services.smallest_ai_response import (
            get_smallest_ai_stt_parameters,
        )

        params = get_smallest_ai_stt_parameters("pulse")
        assert "dropdowns" in params
        lang_dropdown = next(d for d in params["dropdowns"] if d["label"] == "language")
        assert "en" in lang_dropdown["options"]
        assert "hi" in lang_dropdown["options"]


@pytest.mark.unit
class TestSmallestAIWavHelper:
    """Unit tests for _ensure_wav_container helper."""

    def test_passthrough_when_riff_present(self):
        from agentic_eval.core_evals.run_prompt.other_services.smallest_ai_response import (
            _ensure_wav_container,
        )
        import wave
        from io import BytesIO

        buf = BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * 100)
        wav_bytes = buf.getvalue()

        result = _ensure_wav_container(wav_bytes)
        assert result is wav_bytes  # unchanged

    def test_wraps_raw_pcm(self):
        from agentic_eval.core_evals.run_prompt.other_services.smallest_ai_response import (
            _ensure_wav_container,
        )

        raw_pcm = b"\x00\x01" * 500
        result = _ensure_wav_container(raw_pcm, sample_rate=24000)
        assert result[:4] == b"RIFF"
        assert result[8:12] == b"WAVE"


@pytest.mark.unit
class TestSmallestAIManagerRegistration:
    """Verify smallest_ai is registered in OtherServicesManager."""

    def test_speech_handler_registered(self):
        from agentic_eval.core_evals.run_prompt.other_services.manager import (
            OtherServicesManager,
        )

        mgr = OtherServicesManager()
        handler = mgr.get_speech_handler("smallest_ai")
        assert handler is not None

    def test_transcription_handler_registered(self):
        from agentic_eval.core_evals.run_prompt.other_services.manager import (
            OtherServicesManager,
        )

        mgr = OtherServicesManager()
        handler = mgr.get_transcription_handler("smallest_ai")
        assert handler is not None

    def test_tts_parameter_handler_registered(self):
        from agentic_eval.core_evals.run_prompt.other_services.manager import (
            TTS_PARAMETER_HANDLERS,
        )

        assert "smallest_ai" in TTS_PARAMETER_HANDLERS

    def test_stt_parameter_handler_registered(self):
        from agentic_eval.core_evals.run_prompt.other_services.manager import (
            STT_PARAMETER_HANDLERS,
        )

        assert "smallest_ai" in STT_PARAMETER_HANDLERS


@pytest.mark.integration
class TestSmallestAIHandlerExecution:
    """Integration tests for Smallest AI TTS + STT handlers with mocked HTTP."""

    def test_tts_handler_returns_audio(self, tts_messages):
        import wave
        from io import BytesIO
        from unittest.mock import patch, MagicMock
        from agentic_eval.core_evals.run_prompt.other_services.smallest_ai_response import (
            smallest_ai_speech_response,
        )

        # Build a minimal valid WAV as the "PCM" the API returns
        buf = BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x01\x02" * 1000)
        fake_pcm = buf.getvalue()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = iter([fake_pcm])
        mock_response.raise_for_status.return_value = None

        stub = MagicMock()
        stub.model = "smallest_ai/lightning_v3.1"
        stub.run_prompt_config = {"voice_id": "avery", "language": "en", "sample_rate": 24000}
        stub._get_input_text_from_messages.return_value = "Hello world"
        stub._format_audio_output.return_value = (fake_pcm, {"model": "smallest_ai/lightning_v3.1"})

        with patch("requests.post", return_value=mock_response):
            import time
            result = smallest_ai_speech_response(stub, time.time(), "test-api-key")

        stub._format_audio_output.assert_called_once()
        audio_bytes = stub._format_audio_output.call_args[0][0]
        assert audio_bytes[:4] == b"RIFF"

    def test_stt_handler_returns_transcript(self, audio_messages):
        import time
        from unittest.mock import patch, MagicMock
        from agentic_eval.core_evals.run_prompt.other_services.smallest_ai_response import (
            smallest_ai_transcription_response,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "status": "success",
            "transcription": "Hello world",
            "words": [],
            "utterances": [],
            "metadata": {"duration": 1.0, "fileSize": 1000},
        }

        stub = MagicMock()
        stub.model = "smallest_ai/pulse"
        stub.run_prompt_config = {"language": "en"}
        stub._get_input_audio_from_messages.return_value = base64.b64encode(b"\x00\x01" * 100).decode()

        with (
            patch("requests.post", return_value=mock_response),
            patch(
                "agentic_eval.core_evals.run_prompt.other_services.smallest_ai_response.audio_bytes_from_url_or_base64",
                return_value=b"\x00\x01" * 100,
            ),
            patch(
                "agentic_eval.core_evals.run_prompt.other_services.smallest_ai_response.get_audio_duration",
                return_value=1.0,
            ),
        ):
            transcript, info = smallest_ai_transcription_response(stub, time.time(), "test-api-key")

        assert transcript == "Hello world"
        assert info["data"]["response"] == "Hello world"
