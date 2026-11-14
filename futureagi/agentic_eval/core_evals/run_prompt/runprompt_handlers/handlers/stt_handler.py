"""
STT Handler - Handles speech-to-text transcription.

Supports:
1. Custom transcription handlers (ElevenLabs, Deepgram) via OtherServicesManager
2. Standard transcription via litellm.transcription() API
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
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.adapters import (
    RunPromptAdapter,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor import (
    AudioProcessor,
)

logger = structlog.get_logger(__name__)


class StandardSTTHandler(BaseModelHandler):
    """
    Handler for standard STT via litellm.transcription().

    Handles:
    - Audio normalization (format conversion, extension mapping)
    - OpenAI Whisper API compatibility
    - Response formatting with usage metadata
    """

    def _execute_transcription(self, api_key: str) -> tuple[str, Dict[str, Any]]:
        """
        Execute STT transcription via litellm.transcription().

        Args:
            api_key: API key for the provider

        Returns:
            Tuple of (transcription_text, value_info dict)
        """
        start_time = time.time()

        # Extract audio input
        audio_input = self._adapter._get_input_audio_from_messages()

        try:
            if isinstance(audio_input, dict):
                logger.info(
                    f"[STT] Raw input: dict keys={list(audio_input.keys())}, "
                    f"has_data={'data' in audio_input and bool(audio_input.get('data'))}, "
                    f"has_url={'url' in audio_input}"
                )
            else:
                logger.info(
                    f"[STT] Raw input: type={type(audio_input).__name__}, "
                    f"preview={str(audio_input)[:200]}"
                )
        except Exception:
            pass

        # Normalize audio input
        audio_bytes, file_ext = AudioProcessor.normalize_audio_for_stt(audio_input)

        # Create audio buffer with proper name attribute
        buf = AudioProcessor.create_audio_buffer(audio_bytes, file_ext)

        # Call litellm.transcription with drop_params=True
        response = litellm.transcription(
            model=self.context.model, file=buf, api_key=api_key, drop_params=True
        )

        # Validate response is not None
        self._validate_response_not_empty(response, response_type="STT")

        # Validate response has transcription text
        if not hasattr(response, "text") or not response.text:
            self.logger.warning(
                "STT returned no transcription text",
                model=self.context.model,
            )
            raise Exception(
                "STT returned no transcription text. This may be due to API issues or empty audio."
            )

        # Format response with metadata
        return AudioProcessor.format_transcription_output(
            transcription_text=response.text,
            model=self.context.model,
            start_time=start_time,
            audio_bytes=audio_bytes,
        )

    def execute_sync(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute STT transcription synchronously.

        Args:
            streaming: Not used for STT

        Returns:
            HandlerResponse with transcription text and metadata
        """
        start_time = time.time()

        try:
            self._adapter = RunPromptAdapter(self.context)

            self.logger.info(
                "Executing standard STT transcription",
                model=self.context.model,
            )

            # Execute transcription
            transcription_text, value_info = self._execute_transcription(
                self.context.api_key
            )

            # Convert to HandlerResponse
            return HandlerResponse(
                response=transcription_text,
                start_time=start_time,
                end_time=time.time(),
                model=self.context.model,
                metadata=value_info.get("metadata", {}),
            )

        except Exception as e:
            # Expected user misconfiguration (text-only input to an STT eval)
            # is handled gracefully as a failed HandlerResponse; downgrade only
            # that case to warning so real STT failures stay as errors.
            if "No audio input found in messages for STT." in str(e):
                self.logger.warning(f"Standard STT execution failed: {str(e)}")
            else:
                self.logger.exception(f"Standard STT execution failed: {str(e)}")
            return self._build_handler_response(
                response=None,
                start_time=start_time,
                failure=str(e),
            )

    async def execute_async(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute STT transcription asynchronously.

        Args:
            streaming: Not used for STT

        Returns:
            HandlerResponse with transcription text and metadata
        """
        start_time = time.time()

        try:
            self._adapter = RunPromptAdapter(self.context)

            self.logger.info(
                "Executing standard STT transcription (async)",
                model=self.context.model,
            )

            # Execute transcription (wrapped in sync_to_async)
            transcription_text, value_info = await sync_to_async(
                self._execute_transcription
            )(self.context.api_key)

            # Convert to HandlerResponse
            return HandlerResponse(
                response=transcription_text,
                start_time=start_time,
                end_time=time.time(),
                model=self.context.model,
                metadata=value_info.get("metadata", {}),
            )

        except Exception as e:
            # Expected user misconfiguration (text-only input to an STT eval)
            # is handled gracefully as a failed HandlerResponse; downgrade only
            # that case to warning so real STT failures stay as errors.
            if "No audio input found in messages for STT." in str(e):
                self.logger.warning(f"Standard STT async execution failed: {str(e)}")
            else:
                self.logger.exception(f"Standard STT async execution failed: {str(e)}")
            return self._build_handler_response(
                response=None,
                start_time=start_time,
                failure=str(e),
            )


class CustomSTTHandler(BaseModelHandler):
    """
    Handler for custom STT providers via OtherServicesManager.

    Delegates to provider-specific handlers like ElevenLabs, Deepgram.
    Uses RunPromptAdapter to bridge the context-based interface.
    """

    def _get_transcription_handler(self):
        """
        Get the transcription handler for the current provider.

        Returns:
            Transcription handler function or None

        Raises:
            ValueError: If no handler found for provider
        """
        from agentic_eval.core_evals.run_prompt.other_services.manager import (
            OtherServicesManager,
        )

        manager = OtherServicesManager()
        handler = manager.get_transcription_handler(self.context.provider)

        if not handler:
            raise ValueError(
                f"No custom transcription handler found for provider: {self.context.provider}"
            )

        return handler

    # Note: _retry_on_timeout is inherited from BaseModelHandler

    def execute_sync(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute custom STT transcription synchronously.

        Args:
            streaming: Not used for STT

        Returns:
            HandlerResponse with transcription text and metadata
        """
        start_time = time.time()

        try:
            # Get the appropriate transcription handler
            transcription_handler = self._get_transcription_handler()
            adapter = RunPromptAdapter(self.context)

            self.logger.info(
                f"Executing custom STT via {self.context.provider}",
                model=self.context.model,
            )

            # Execute with retry on timeout
            transcription_text, value_info = self._retry_on_timeout(
                transcription_handler,
                adapter,
                start_time,
                self.context.api_key,
            )

            # Validate response is not empty
            self._validate_response_not_empty(
                transcription_text,
                response_type=f"STT (custom/{self.context.provider})",
            )

            # Convert to HandlerResponse
            return HandlerResponse(
                response=transcription_text,
                start_time=start_time,
                end_time=time.time(),
                model=self.context.model,
                metadata=value_info.get("metadata", {}),
            )

        except Exception as e:
            # Expected user misconfiguration (text-only input to an STT eval)
            # is handled gracefully as a failed HandlerResponse; downgrade only
            # that case to warning so real STT failures stay as errors.
            if "No audio input found in messages for STT." in str(e):
                self.logger.warning(f"Custom STT execution failed: {str(e)}")
            else:
                self.logger.exception(f"Custom STT execution failed: {str(e)}")
            return self._build_handler_response(
                response=None,
                start_time=start_time,
                failure=str(e),
            )

    async def execute_async(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute custom STT transcription asynchronously.

        Args:
            streaming: Not used for STT

        Returns:
            HandlerResponse with transcription text and metadata
        """
        start_time = time.time()

        try:
            # Get the appropriate transcription handler
            transcription_handler = self._get_transcription_handler()
            adapter = RunPromptAdapter(self.context)

            self.logger.info(
                f"Executing custom STT via {self.context.provider} (async)",
                model=self.context.model,
            )

            # Execute with async retry (uses asyncio.sleep instead of blocking time.sleep)
            transcription_text, value_info = await self._retry_on_timeout_async(
                transcription_handler,
                adapter,
                start_time,
                self.context.api_key,
            )

            # Validate response is not empty
            self._validate_response_not_empty(
                transcription_text,
                response_type=f"STT (custom/{self.context.provider})",
            )

            # Convert to HandlerResponse
            return HandlerResponse(
                response=transcription_text,
                start_time=start_time,
                end_time=time.time(),
                model=self.context.model,
                metadata=value_info.get("metadata", {}),
            )

        except Exception as e:
            # Expected user misconfiguration (text-only input to an STT eval)
            # is handled gracefully as a failed HandlerResponse; downgrade only
            # that case to warning so real STT failures stay as errors.
            if "No audio input found in messages for STT." in str(e):
                self.logger.warning(f"Custom STT async execution failed: {str(e)}")
            else:
                self.logger.exception(f"Custom STT async execution failed: {str(e)}")
            return self._build_handler_response(
                response=None,
                start_time=start_time,
                failure=str(e),
            )


class STTHandler(BaseModelHandler):
    """
    Main STT handler that routes to appropriate sub-handler.

    Routing logic:
    1. Custom provider (ElevenLabs, Deepgram) → CustomSTTHandler
    2. Standard STT API → StandardSTTHandler
    """

    def __init__(self, context: ModelHandlerContext):
        """
        Initialize STT handler.

        Args:
            context: ModelHandlerContext with STT configuration
        """
        super().__init__(context)
        self._determine_sub_handler()

    def _determine_sub_handler(self):
        """
        Determine which sub-handler to use based on provider.

        Sets self._handler to the appropriate instance.
        """
        from agentic_eval.core_evals.run_prompt.other_services.manager import (
            OtherServicesManager,
        )

        manager = OtherServicesManager()
        has_custom_handler = manager.get_transcription_handler(self.context.provider)

        if has_custom_handler:
            self._handler = CustomSTTHandler(self.context)
        else:
            self._handler = StandardSTTHandler(self.context)

    def execute_sync(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute STT transcription synchronously.

        Args:
            streaming: Not used for STT

        Returns:
            HandlerResponse with transcription text and metadata
        """
        return self._handler.execute_sync(streaming=streaming)

    async def execute_async(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute STT transcription asynchronously.

        Args:
            streaming: Not used for STT

        Returns:
            HandlerResponse with transcription text and metadata
        """
        return await self._handler.execute_async(streaming=streaming)
