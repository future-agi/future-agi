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

import time
from typing import Any, Dict

import litellm
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
        return AudioProcessor.build_speech_params(
            model=self.context.model,
            input_text=input_text,
            api_key=self.context.api_key,
            run_prompt_config=self.context.run_prompt_config,
        )

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
        error_str = str(error).lower()
        is_404_error = "404" in error_str or "invalid url" in error_str
        is_audio_preview = "gpt-4o-audio-preview" in self.context.model

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
            input_text = AudioProcessor.extract_literal_speech_script(
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
            audio_response = litellm.speech(**speech_params, drop_params=True)

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
            input_text = AudioProcessor.extract_literal_speech_script(
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
            audio_response = await litellm.aspeech(**speech_params, drop_params=True)

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
