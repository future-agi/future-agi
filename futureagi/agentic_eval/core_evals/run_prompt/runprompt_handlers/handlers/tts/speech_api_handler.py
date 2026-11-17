"""
Speech API Handler - Standard TTS via litellm.speech().

This handler uses the standard speech API for TTS:
- OpenAI TTS (tts-1, tts-1-hd)
- Azure TTS
- Vertex AI TTS
- Other litellm-supported TTS providers

Features:
- Provider-specific voice configuration
- Support for various audio formats
- Fallback to audio-preview for 404 errors (optional)
"""

# Pre-existing lint debt, exempted file-wide so a security fix can touch this
# module behaviour-neutrally (text without placeholders and typing rewrites).
# ruff: noqa: F541, UP006, UP035

import time
from typing import Any, Dict

import structlog
from asgiref.sync import sync_to_async

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    BaseModelHandler,
    HandlerResponse,
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor import (
    AudioProcessor,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.payload_builder import (
    vertex_credentials_and_location,
)
from model_hub.utils.model_endpoints import (
    EndpointCallError,
    ModelConfigurationRefused,
    audio_model_kwargs,
    guarded_aspeech,
    guarded_speech,
    is_endpoint_error,
    is_vertex_provider,
    refuse_vertex_call_without_a_service_account_key,
    refuse_vertex_model_sent_to_another_provider,
)
from model_hub.utils.model_key import ModelKeyMissing

logger = structlog.get_logger(__name__)


class SpeechAPIHandler(BaseModelHandler):
    """
    Handler for standard TTS via litellm.speech() API.

    Supports all litellm TTS-capable providers:
    - OpenAI (tts-1, tts-1-hd)
    - Azure
    - Vertex AI
    - And others

    Provider-specific handling:
    - vertex_ai/: Voice as dict, optional audioConfig
    - gemini/: Simple string voice/format
    - OpenAI/others: String voice/format
    """

    def __init__(self, context: ModelHandlerContext):
        """
        Initialize speech API handler.

        Args:
            context: ModelHandlerContext with TTS configuration
        """
        super().__init__(context)
        self._validate_context()

    def _build_speech_params(self, input_text: str) -> Dict[str, Any]:
        """
        Build provider-specific speech parameters.

        Uses AudioProcessor.build_speech_params for provider-specific
        voice and format configuration.

        Args:
            input_text: Text to convert to speech

        Returns:
            Dictionary of speech parameters for litellm.speech()
        """
        speech_params = AudioProcessor.build_speech_params(
            model=self.context.model,
            input_text=input_text,
            api_key=self.context.api_key,
            run_prompt_config=self.context.run_prompt_config,
        )
        api_key = self.context.api_key
        if is_vertex_provider(self.context.provider) and isinstance(api_key, dict):
            # A customer's Vertex model is called with the customer's own
            # service-account key and its location, as the chat payload is,
            # and not with the stored configuration as a plain key.
            creds, vertex_location = vertex_credentials_and_location(api_key)
            speech_params.pop("api_key", None)
            speech_params["vertex_credentials"] = creds
            if vertex_location:
                speech_params["vertex_location"] = vertex_location
        else:
            speech_params.update(audio_model_kwargs(self.context.provider, api_key))
        # A Vertex model given no service-account key of the customer's own
        # (a key alone, no configuration, an empty one or a location alone)
        # is refused before these calls.
        refuse_vertex_call_without_a_service_account_key(
            self.context.provider, speech_params
        )
        # So is a Vertex model whose name the model library sends to another
        # provider.
        refuse_vertex_model_sent_to_another_provider(
            self.context.provider, speech_params
        )
        return speech_params

    def _should_fallback_to_completion(self, error: Exception) -> bool:
        """
        Check if we should fallback to completion endpoint for audio-preview.

        Some audio-preview models may fail with 404 on the speech endpoint
        and need to use the completion endpoint instead.

        Args:
            error: The exception that occurred

        Returns:
            True if we should try the completion endpoint fallback
        """
        is_audio_preview = "gpt-4o-audio-preview" in self.context.model
        if isinstance(error, EndpointCallError):
            return (
                is_audio_preview
                and error.category == "status"
                and error.status_code == 404
            )
        if is_endpoint_error(error) or isinstance(
            error, (ModelConfigurationRefused, ModelKeyMissing)
        ):
            return False
        error_str = str(error).lower()
        is_404_error = "404" in error_str or "invalid url" in error_str
        return is_404_error and is_audio_preview

    def execute_sync(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute TTS request synchronously via litellm.speech().

        Args:
            streaming: Not used for TTS

        Returns:
            HandlerResponse with audio URL and metadata
        """
        start_time = time.time()

        try:
            # Extract input text
            input_text = AudioProcessor.extract_text_from_messages(
                self.context.messages
            )

            # Build speech parameters
            speech_params = self._build_speech_params(input_text)

            self.logger.info(
                f"TTS request initiated via litellm.speech()",
                model=self.context.model,
                input_length=len(input_text),
                voice=speech_params.get("voice", "default"),
            )

            # Execute speech API call with drop_params=True to handle provider differences
            audio_response = guarded_speech(**speech_params, drop_params=True)

            # Validate response is not None
            self._validate_response_not_empty(audio_response, response_type="TTS")

            # Validate response has audio content
            if not hasattr(audio_response, "content") or not audio_response.content:
                self.logger.warning(
                    "TTS returned no audio content",
                    model=self.context.model,
                )
                raise Exception(
                    "TTS returned no audio content. This may be due to API issues."
                )

            # Format output
            s3_url, value_info = AudioProcessor.format_audio_output(
                audio_bytes=audio_response.content,
                model=self.context.model,
                start_time=start_time,
                input_text=input_text,
            )

            return HandlerResponse(
                response=s3_url,
                start_time=start_time,
                end_time=time.time(),
                model=self.context.model,
                metadata=value_info.get("metadata", {}),
            )

        except Exception as e:
            # Check if we should fallback to completion endpoint
            if self._should_fallback_to_completion(e):
                self.logger.warning(
                    f"litellm.speech() failed with 404, falling back to completion endpoint",
                    model=self.context.model,
                    error=str(e),
                )
                return self._fallback_to_completion_sync(start_time)

            if is_endpoint_error(e) or isinstance(
                e, (ModelConfigurationRefused, ModelKeyMissing)
            ):
                raise
            self.logger.exception(f"Speech API TTS execution failed: {str(e)}")
            return self._build_handler_response(
                response=None,
                start_time=start_time,
                failure=str(e),
            )

    async def execute_async(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute TTS request asynchronously via litellm.speech().

        Note: litellm.speech() doesn't have an async version, so we use
        sync_to_async wrapper.

        Args:
            streaming: Not used for TTS

        Returns:
            HandlerResponse with audio URL and metadata
        """
        start_time = time.time()

        try:
            # Extract input text
            input_text = AudioProcessor.extract_text_from_messages(
                self.context.messages
            )

            # Build speech parameters
            speech_params = self._build_speech_params(input_text)

            self.logger.info(
                f"TTS request initiated via litellm.speech() (async)",
                model=self.context.model,
                input_length=len(input_text),
                voice=speech_params.get("voice", "default"),
            )

            # Execute speech API call with drop_params=True to handle provider differences
            audio_response = await guarded_aspeech(**speech_params, drop_params=True)

            # Validate response is not None
            self._validate_response_not_empty(audio_response, response_type="TTS")

            # Validate response has audio content
            if not hasattr(audio_response, "content") or not audio_response.content:
                self.logger.warning(
                    "TTS returned no audio content",
                    model=self.context.model,
                )
                raise Exception(
                    "TTS returned no audio content. This may be due to API issues."
                )

            # Format output
            s3_url, value_info = await sync_to_async(
                AudioProcessor.format_audio_output
            )(
                audio_bytes=audio_response.content,
                model=self.context.model,
                start_time=start_time,
                input_text=input_text,
            )

            return HandlerResponse(
                response=s3_url,
                start_time=start_time,
                end_time=time.time(),
                model=self.context.model,
                metadata=value_info.get("metadata", {}),
            )

        except Exception as e:
            # Check if we should fallback to completion endpoint
            if self._should_fallback_to_completion(e):
                self.logger.warning(
                    f"litellm.speech() failed with 404, falling back to completion endpoint",
                    model=self.context.model,
                    error=str(e),
                )
                return await self._fallback_to_completion_async(start_time)

            if is_endpoint_error(e) or isinstance(
                e, (ModelConfigurationRefused, ModelKeyMissing)
            ):
                raise
            self.logger.exception(f"Speech API TTS async execution failed: {str(e)}")
            return self._build_handler_response(
                response=None,
                start_time=start_time,
                failure=str(e),
            )

    def _fallback_to_completion_sync(self, start_time: float) -> HandlerResponse:
        """
        Fallback to completion endpoint for audio generation.

        Used when speech() API returns 404 for audio-preview models.

        Args:
            start_time: Original request start time

        Returns:
            HandlerResponse from AudioPreviewHandler
        """
        from .audio_preview_handler import AudioPreviewHandler

        self.logger.info("Falling back to AudioPreviewHandler for TTS")
        handler = AudioPreviewHandler(self.context)
        return handler.execute_sync()

    async def _fallback_to_completion_async(self, start_time: float) -> HandlerResponse:
        """
        Async fallback to completion endpoint for audio generation.

        Used when speech() API returns 404 for audio-preview models.

        Args:
            start_time: Original request start time

        Returns:
            HandlerResponse from AudioPreviewHandler
        """
        from .audio_preview_handler import AudioPreviewHandler

        self.logger.info("Falling back to AudioPreviewHandler for TTS (async)")
        handler = AudioPreviewHandler(self.context)
        return await handler.execute_async()
