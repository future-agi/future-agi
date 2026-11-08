"""
TTS Handler - Main router for text-to-speech operations.

This handler routes TTS requests to the appropriate sub-handler based on:
1. Custom provider (ElevenLabs, Cartesia, etc.) -> CustomTTSHandler
2. Audio-preview models (gpt-4o-audio-preview, Gemini) -> AudioPreviewHandler
3. Standard TTS API (litellm.speech()) -> SpeechAPIHandler

Features:
- Unified interface for all TTS providers
- Automatic routing based on model/provider
- Consistent error handling and response formatting
- Support for voice configuration and audio formats
"""

import time

import structlog

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    BaseModelHandler,
    HandlerResponse,
    ModelHandlerContext,
)

logger = structlog.get_logger(__name__)

# TTSHandler sub-handler lanes.
TTS_LANE_CUSTOM = "custom"  # CustomTTSHandler: provider speech API reads the text
TTS_LANE_COMPLETION = "completion"  # AudioPreviewHandler: litellm.completion
TTS_LANE_SPEECH = "speech"  # SpeechAPIHandler: litellm.speech reads the text


def requires_completion_audio(model: str) -> bool:
    """
    Check if a model must generate audio through the completion endpoint.

    Models that MUST use AudioPreviewHandler (litellm.completion with audio modalities):
    - OpenAI gpt-4o-audio-preview models
    - Vertex AI Gemini TTS (vertex_ai/gemini-*-tts) - requires completion endpoint
      See: https://docs.litellm.ai/docs/providers/vertex_speech#gemini-tts

    Models that can use SpeechAPIHandler (litellm.speech with built-in bridge):
    - Google AI Studio Gemini TTS (gemini/*-tts) - has speech-to-completion bridge
      See: https://docs.litellm.ai/docs/text_to_speech#gemini-text-to-speech
    """
    model = model.lower()

    # OpenAI audio-preview models
    # Use exact match or prefix match to avoid matching unintended model names
    if model == "gpt-4o-audio-preview" or model.startswith("gpt-4o-audio-preview-"):
        return True

    # Vertex AI Gemini TTS - MUST use completion endpoint with modalities=["audio"]
    # litellm.speech() does NOT support vertex_ai/gemini-*-tts models
    if model.startswith("vertex_ai/gemini") and "tts" in model:
        return True

    # Note: Google AI Studio Gemini TTS (gemini/*-tts without vertex_ai/ prefix)
    # can use SpeechAPIHandler as LiteLLM has a working bridge for these models.

    return False


def tts_lane(model: str, provider: str | None) -> str:
    """
    Name the sub-handler TTSHandler routes a model and resolved provider to.

    A provider with a registered speech handler wins, then completion-audio
    models, then the standard speech API.
    """
    from agentic_eval.core_evals.run_prompt.other_services.manager import (
        OtherServicesManager,
    )

    if provider and OtherServicesManager().get_speech_handler(provider):
        return TTS_LANE_CUSTOM
    if requires_completion_audio(model):
        return TTS_LANE_COMPLETION
    return TTS_LANE_SPEECH


def speaks_text_literally(model: str, provider: str | None) -> bool:
    """
    Check if TTSHandler sends the prompt's text to a speech API to be read aloud.

    True for custom speech providers and every SpeechAPIHandler model: that
    handler sends the text to the speech endpoint first, even for a name it
    may later retry through completion. False for the completion-audio lane.
    """
    return tts_lane(model, provider) in (TTS_LANE_CUSTOM, TTS_LANE_SPEECH)


class TTSHandler(BaseModelHandler):
    """
    Main TTS handler that routes to appropriate sub-handlers.

    Routing logic:
    1. If custom provider handler exists (ElevenLabs, etc.) -> CustomTTSHandler
    2. If audio-preview model (gpt-4o-audio-preview) -> AudioPreviewHandler
    3. Otherwise -> SpeechAPIHandler (litellm.speech())
    """

    def __init__(self, context: ModelHandlerContext):
        """
        Initialize TTS handler.

        Args:
            context: ModelHandlerContext with TTS configuration
        """
        super().__init__(context)
        self._sub_handler = None

    def _get_sub_handler(self) -> BaseModelHandler:
        """
        Determine and return the appropriate sub-handler.

        Returns:
            Sub-handler instance (CustomTTSHandler, AudioPreviewHandler, or SpeechAPIHandler)
        """
        if self._sub_handler is not None:
            return self._sub_handler

        provider = self.context.provider
        lane = tts_lane(self.context.model, provider)

        # 1. Custom provider handler (ElevenLabs, Cartesia, ...)
        if lane == TTS_LANE_CUSTOM:
            from .tts.custom_tts_handler import CustomTTSHandler

            self.logger.info(f"Routing to CustomTTSHandler for provider: {provider}")
            self._sub_handler = CustomTTSHandler(self.context)
            return self._sub_handler

        # 2. Audio-preview model
        if lane == TTS_LANE_COMPLETION:
            from .tts.audio_preview_handler import AudioPreviewHandler

            self.logger.info(
                f"Routing to AudioPreviewHandler for model: {self.context.model}"
            )
            self._sub_handler = AudioPreviewHandler(self.context)
            return self._sub_handler

        # 3. Default: Standard speech API
        from .tts.speech_api_handler import SpeechAPIHandler

        self.logger.info(f"Routing to SpeechAPIHandler for model: {self.context.model}")
        self._sub_handler = SpeechAPIHandler(self.context)
        return self._sub_handler

    def _is_audio_preview_model(self) -> bool:
        """
        Check if model is an audio-preview model that MUST use completion endpoint.

        See requires_completion_audio.

        Returns:
            True if model must use AudioPreviewHandler, False otherwise
        """
        return requires_completion_audio(self.context.model)

    def _validate_context(self):
        """
        Validate TTS-specific context requirements.

        Raises:
            ValueError: If context is missing required TTS fields
        """
        super()._validate_context()

        if self.context.output_format != "audio":
            raise ValueError(
                f"TTSHandler requires output_format='audio', got: {self.context.output_format}"
            )

    def execute_sync(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute TTS request synchronously.

        Delegates to the appropriate sub-handler based on provider/model.

        Args:
            streaming: Not used for TTS (audio is returned complete)

        Returns:
            HandlerResponse with audio URL and metadata
        """
        start_time = time.time()

        try:
            self._validate_context()

            # Get and execute sub-handler
            sub_handler = self._get_sub_handler()
            return sub_handler.execute_sync(streaming=streaming)

        except Exception as e:
            # Expected, handled validation failure (empty messages) is user
            # misconfiguration, not a bug; it is returned as a failed
            # HandlerResponse. Downgrade only that case to warning so real TTS
            # failures keep creating Sentry issues.
            if "Messages are required" in str(e):
                self.logger.warning(f"TTS sync execution failed: {str(e)}")
            else:
                self.logger.exception(f"TTS sync execution failed: {str(e)}")
            return self._build_handler_response(
                response=None,
                start_time=start_time,
                failure=str(e),
            )

    async def execute_async(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute TTS request asynchronously.

        Delegates to the appropriate sub-handler based on provider/model.

        Args:
            streaming: Not used for TTS (audio is returned complete)

        Returns:
            HandlerResponse with audio URL and metadata
        """
        start_time = time.time()

        try:
            self._validate_context()

            # Get and execute sub-handler
            sub_handler = self._get_sub_handler()
            return await sub_handler.execute_async(streaming=streaming)

        except Exception as e:
            # Expected, handled validation failure (empty messages) is user
            # misconfiguration, not a bug; it is returned as a failed
            # HandlerResponse. Downgrade only that case to warning so real TTS
            # failures keep creating Sentry issues.
            if "Messages are required" in str(e):
                self.logger.warning(f"TTS async execution failed: {str(e)}")
            else:
                self.logger.exception(f"TTS async execution failed: {str(e)}")
            return self._build_handler_response(
                response=None,
                start_time=start_time,
                failure=str(e),
            )
