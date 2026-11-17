"""
Custom TTS Handler - Delegates to OtherServicesManager speech handlers.

This handler wraps the existing custom TTS providers:
- ElevenLabs
- Cartesia
- Deepgram
- Inworld
- Rime
- Neuphonic
- Hume
- LMNT

The existing handlers expect a RunPrompt-like interface, so we create an
adapter that bridges the ModelHandlerContext to the expected interface.
"""

import time

import structlog

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    BaseModelHandler,
    HandlerResponse,
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.adapters import (
    RunPromptAdapter,
)
from model_hub.utils.model_endpoints import _capture_llm_messages

logger = structlog.get_logger(__name__)


class CustomTTSHandler(BaseModelHandler):
    """
    Handler for custom TTS providers via OtherServicesManager.

    Delegates to provider-specific handlers like ElevenLabs, Cartesia, etc.
    Uses RunPromptAdapter to bridge the context-based interface.
    """

    def __init__(self, context: ModelHandlerContext):
        """
        Initialize custom TTS handler.

        Args:
            context: ModelHandlerContext with TTS configuration
        """
        super().__init__(context)
        self._validate_context()
        self._adapter = RunPromptAdapter(context)

    def _get_speech_handler(self):
        """
        Get the speech handler for the current provider.

        Returns:
            Speech handler function or None

        Raises:
            ValueError: If no handler found for provider
        """
        from agentic_eval.core_evals.run_prompt.other_services.manager import (
            OtherServicesManager,
        )

        manager = OtherServicesManager()
        handler = manager.get_speech_handler(self.context.provider)

        if not handler:
            raise ValueError(
                f"No custom speech handler found for provider: {self.context.provider}"
            )

        return handler

    # Note: _retry_on_timeout and _retry_on_timeout_async are inherited from BaseModelHandler

    def execute_sync(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute custom TTS request synchronously.

        Args:
            streaming: Not used for TTS

        Returns:
            HandlerResponse with audio URL and metadata
        """
        start_time = time.time()

        try:
            # Get the appropriate speech handler
            speech_handler = self._get_speech_handler()

            self.logger.info(
                f"Executing custom TTS via {self.context.provider}",
                model=self.context.model,
            )

            # Execute with retry on timeout
            _capture_llm_messages({})
            s3_url, value_info = self._retry_on_timeout(
                speech_handler,
                self._adapter,
                start_time,
                self.context.api_key,
            )

            # Validate response is not empty
            self._validate_response_not_empty(
                s3_url,
                response_type=f"TTS (custom/{self.context.provider})",
            )

            # Convert to HandlerResponse
            return HandlerResponse(
                response=s3_url,
                start_time=start_time,
                end_time=time.time(),
                model=self.context.model,
                metadata=value_info.get("metadata", {}),
            )

        except Exception as e:
            self.logger.exception(f"Custom TTS execution failed: {str(e)}")
            return self._build_handler_response(
                response=None,
                start_time=start_time,
                failure=str(e),
            )

    async def execute_async(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute custom TTS request asynchronously.

        Args:
            streaming: Not used for TTS

        Returns:
            HandlerResponse with audio URL and metadata
        """
        start_time = time.time()

        try:
            # Get the appropriate speech handler
            speech_handler = self._get_speech_handler()

            self.logger.info(
                f"Executing custom TTS via {self.context.provider} (async)",
                model=self.context.model,
            )

            # Execute with async retry (uses asyncio.sleep instead of blocking time.sleep)
            _capture_llm_messages({})
            s3_url, value_info = await self._retry_on_timeout_async(
                speech_handler,
                self._adapter,
                start_time,
                self.context.api_key,
            )

            # Validate response is not empty
            self._validate_response_not_empty(
                s3_url,
                response_type=f"TTS (custom/{self.context.provider})",
            )

            # Convert to HandlerResponse
            return HandlerResponse(
                response=s3_url,
                start_time=start_time,
                end_time=time.time(),
                model=self.context.model,
                metadata=value_info.get("metadata", {}),
            )

        except Exception as e:
            self.logger.exception(f"Custom TTS async execution failed: {str(e)}")
            return self._build_handler_response(
                response=None,
                start_time=start_time,
                failure=str(e),
            )
