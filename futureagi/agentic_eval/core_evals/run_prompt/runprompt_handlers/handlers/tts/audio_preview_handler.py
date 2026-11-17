"""
Audio Preview Handler - TTS via completion endpoint with audio modalities.

This handler supports audio-preview models that generate speech through
the completion endpoint rather than the speech() API:
- OpenAI gpt-4o-audio-preview
- Gemini models with audio modality

These models use the completion endpoint with:
- modalities: ["audio"] or ["text", "audio"]
- audio: {voice: "...", format: "..."}

Features:
- OpenAI audio-preview with multimodal message format
- Gemini audio via completion with plain string messages
- Provider-specific payload adaptation
"""

# Pre-existing lint debt, exempted file-wide so a security fix can touch this
# module behaviour-neutrally (import reordering, exception chaining and
# typing rewrites).
# ruff: noqa: B904, I001, UP006, UP035

import copy
import time
from typing import Any, Dict

import structlog
from asgiref.sync import sync_to_async

from agentic_eval.core_evals.run_prompt.error_handler import litellm_try_except
from model_hub.utils.model_endpoints import (
    guarded_acompletion,
    guarded_completion,
    is_endpoint_error,
)

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    BaseModelHandler,
    HandlerResponse,
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor import (
    AudioProcessor,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.payload_builder import (
    PayloadBuilder,
)

logger = structlog.get_logger(__name__)

# Default TTS voice and format constants
DEFAULT_GEMINI_VOICE = "Kore"
DEFAULT_GEMINI_FORMAT = "pcm16"
DEFAULT_OPENAI_VOICE = "alloy"
DEFAULT_OPENAI_FORMAT = "mp3"

# Gemini TTS input limits (per Google Cloud docs)
# Individual text field: <= 4,000 bytes, combined total: <= 8,000 bytes
GEMINI_TTS_MAX_BYTES_PER_FIELD = 4000
GEMINI_TTS_MAX_BYTES_TOTAL = 8000


class AudioPreviewHandler(BaseModelHandler):
    """
    Handler for audio-preview models using completion endpoint.

    Supports:
    - gpt-4o-audio-preview: Uses multimodal message format
    - gemini/: Uses plain string messages with audio modality

    The key difference from SpeechAPIHandler is that these models
    generate audio as a side effect of text completion, not via
    a dedicated speech API.
    """

    def __init__(self, context: ModelHandlerContext):
        """
        Initialize audio preview handler.

        Args:
            context: ModelHandlerContext with TTS configuration
        """
        super().__init__(context)
        self._validate_context()

    def _is_gemini_model(self) -> bool:
        """Check if model is a Gemini model (including Vertex AI Gemini)."""
        model = self.context.model.lower()
        return model.startswith("gemini/") or model.startswith("vertex_ai/gemini")

    def _build_audio_preview_payload(
        self, base_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build payload for audio-preview completion.

        Modifies the base LLM payload to include audio modalities
        and format messages appropriately for the provider.

        Args:
            base_payload: Base LLM payload from PayloadBuilder

        Returns:
            Modified payload for audio generation
        """
        payload = copy.deepcopy(base_payload)
        config = self.context.run_prompt_config or {}

        if self._is_gemini_model():
            return self._build_gemini_audio_payload(payload, config)
        else:
            return self._build_openai_audio_payload(payload, config)

    def _build_gemini_audio_payload(
        self, payload: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build Gemini-specific audio payload.

        Gemini audio completion:
        - Enforces modalities: ["audio"]
        - Uses plain string messages (de-normalizes from list format)
        - Strips OpenAI-specific parameters
        - Sets allowed_openai_params for litellm

        Args:
            payload: Base payload to modify
            config: Run prompt configuration

        Returns:
            Gemini-specific audio payload
        """
        # Set modalities for audio output
        payload["modalities"] = ["audio"]

        # Build audio configuration
        # Use voice_id if present (resolved provider ID), otherwise use voice
        voice_val = config.get("voice_id") or config.get("voice", DEFAULT_GEMINI_VOICE)
        payload["audio"] = {
            "voice": voice_val if isinstance(voice_val, str) else DEFAULT_GEMINI_VOICE,
            "format": DEFAULT_GEMINI_FORMAT,  # Gemini uses pcm16 format
        }

        # Strip OpenAI-only parameters that Gemini TTS doesn't support
        params_to_strip = (
            "response_format",
            "tools",
            "tool_choice",
            "functions",
            "max_tokens",
            "max_completion_tokens",
            "frequency_penalty",
            "presence_penalty",
            "top_p",
        )
        for param in params_to_strip:
            payload.pop(param, None)

        # De-normalize messages to plain strings (Gemini prefers this)
        for msg in payload.get("messages", []):
            content = msg.get("content")
            if isinstance(content, list):
                # Extract text parts and join them
                parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                if parts:
                    msg["content"] = " ".join(parts)

        # Validate message sizes against Gemini TTS limits
        total_bytes = 0
        for msg in payload.get("messages", []):
            content = msg.get("content", "")
            if isinstance(content, str):
                content_bytes = len(content.encode("utf-8"))
                if content_bytes > GEMINI_TTS_MAX_BYTES_PER_FIELD:
                    raise ValueError(
                        f"Gemini TTS input exceeds the maximum of "
                        f"{GEMINI_TTS_MAX_BYTES_PER_FIELD} bytes per message "
                        f"({content_bytes} bytes provided). "
                        f"Please shorten your input text."
                    )
                total_bytes += content_bytes
        if total_bytes > GEMINI_TTS_MAX_BYTES_TOTAL:
            raise ValueError(
                f"Gemini TTS input exceeds the maximum combined limit of "
                f"{GEMINI_TTS_MAX_BYTES_TOTAL} bytes "
                f"({total_bytes} bytes provided). "
                f"Please shorten your input text."
            )

        # Allow audio/modalities params and drop unknowns
        payload["allowed_openai_params"] = ["audio", "modalities"]

        return payload

    def _build_openai_audio_payload(
        self, payload: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build OpenAI-specific audio-preview payload.

        OpenAI audio-preview models:
        - Use modalities: ["text", "audio"]
        - Expect content as list format for multimodal
        - Support voice and format in audio config

        Args:
            payload: Base payload to modify
            config: Run prompt configuration

        Returns:
            OpenAI-specific audio payload
        """
        # Set modalities (text + audio)
        payload["modalities"] = config.get("modalities", ["text", "audio"])

        # Build audio configuration
        if "audio" in config and isinstance(config["audio"], dict):
            # Use nested audio config if provided
            payload["audio"] = config["audio"]
        else:
            # Build from top-level config
            voice_val = config.get("voice_id") or config.get(
                "voice", DEFAULT_OPENAI_VOICE
            )
            payload["audio"] = {
                "voice": voice_val,
                "format": config.get("format", DEFAULT_OPENAI_FORMAT),
            }

        # Normalize messages to multimodal list format (OpenAI expects this)
        for msg in payload.get("messages", []):
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = [{"type": "text", "text": content}]

        return payload

    def _extract_audio_from_response(self, response) -> bytes:
        """
        Extract audio data from completion response.

        For Gemini models, the audio is PCM16 format which needs to be
        converted to WAV for proper processing by FFmpeg and other tools.

        Args:
            response: LiteLLM completion response

        Returns:
            Audio bytes (WAV format for Gemini, raw bytes for others)

        Raises:
            ValueError: If no audio found in response
        """
        import base64

        try:
            audio_data = response.choices[0].message.audio.data
            if not audio_data:
                raise ValueError("Audio data is empty in response")

            # Decode base64 to bytes
            audio_bytes = base64.b64decode(audio_data)

            # For Gemini models, convert PCM16 to WAV format
            # Gemini TTS only supports PCM16 which is raw audio without headers
            # FFmpeg cannot detect raw PCM, so we add WAV headers
            if self._is_gemini_model():
                self.logger.info(
                    "Converting Gemini PCM16 audio to WAV format",
                    model=self.context.model,
                    pcm_size=len(audio_bytes),
                )
                audio_bytes = AudioProcessor.pcm16_to_wav(
                    audio_bytes,
                    sample_rate=24000,  # Gemini uses 24kHz
                    num_channels=1,  # Mono
                    sample_width=2,  # 2 bytes = 16-bit
                )
                self.logger.info(
                    "PCM16 to WAV conversion complete",
                    wav_size=len(audio_bytes),
                )

            return audio_bytes

        except AttributeError as e:
            raise ValueError(f"No audio found in response: {str(e)}")

    def execute_sync(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute audio-preview TTS request synchronously.

        Args:
            streaming: Not used for audio-preview TTS

        Returns:
            HandlerResponse with audio URL and metadata
        """
        start_time = time.time()

        try:
            # Build base LLM payload
            base_payload = PayloadBuilder.build_llm_payload(
                self.context,
                self.context.provider or "openai",
                self.context.api_key,
            )

            # Modify for audio generation
            payload = self._build_audio_preview_payload(base_payload)

            self.logger.info(
                "Executing audio-preview TTS via litellm.completion()",
                model=self.context.model,
                is_gemini=self._is_gemini_model(),
            )

            # Execute completion with drop_params=True
            completion_response = None
            with litellm_try_except():
                completion_response = guarded_completion(**payload, drop_params=True)

            # Validate response is not None
            self._validate_response_not_empty(
                completion_response, response_type="TTS (audio-preview)"
            )

            # Extract audio data
            audio_data = self._extract_audio_from_response(completion_response)

            # Validate audio data is not empty
            if not audio_data:
                self.logger.warning(
                    "TTS (audio-preview) returned no audio data",
                    model=self.context.model,
                )
                raise Exception(
                    "TTS (audio-preview) returned no audio data. This may be due to API issues."
                )

            # Get input text for metadata
            input_text = AudioProcessor.extract_text_from_messages(
                self.context.messages
            )

            # Format output
            s3_url, value_info = AudioProcessor.format_audio_output(
                audio_bytes=audio_data,
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
            if is_endpoint_error(e):
                # A tenant endpoint's error: generic already; logged without
                # text or traceback.
                self.logger.warning(
                    "Audio-preview TTS execution failed",
                    error_type=type(e).__name__,
                    category=getattr(e, "category", None),
                )
            else:
                self.logger.exception(f"Audio-preview TTS execution failed: {str(e)}")
            return self._build_handler_response(
                response=None,
                start_time=start_time,
                failure=str(e),
            )

    async def execute_async(self, streaming: bool = False) -> HandlerResponse:
        """
        Execute audio-preview TTS request asynchronously.

        Args:
            streaming: Not used for audio-preview TTS

        Returns:
            HandlerResponse with audio URL and metadata
        """
        start_time = time.time()

        try:
            # Build base LLM payload
            base_payload = PayloadBuilder.build_llm_payload(
                self.context,
                self.context.provider or "openai",
                self.context.api_key,
            )

            # Modify for audio generation
            payload = self._build_audio_preview_payload(base_payload)

            self.logger.info(
                "Executing audio-preview TTS via litellm.acompletion()",
                model=self.context.model,
                is_gemini=self._is_gemini_model(),
            )

            # Execute async completion with drop_params=True
            completion_response = None
            if self._is_gemini_model():
                # Gemini TTS only supports sync litellm.completion (acompletion not supported)
                with litellm_try_except():
                    completion_response = await sync_to_async(
                        guarded_completion, thread_sensitive=False
                    )(**payload, drop_params=True)
            else:
                with litellm_try_except():
                    completion_response = await guarded_acompletion(
                        **payload, drop_params=True
                    )

            # Validate response is not None
            self._validate_response_not_empty(
                completion_response, response_type="TTS (audio-preview)"
            )

            # Extract audio data
            audio_data = self._extract_audio_from_response(completion_response)

            # Validate audio data is not empty
            if not audio_data:
                self.logger.warning(
                    "TTS (audio-preview) returned no audio data",
                    model=self.context.model,
                )
                raise Exception(
                    "TTS (audio-preview) returned no audio data. This may be due to API issues."
                )

            # Get input text for metadata
            input_text = AudioProcessor.extract_text_from_messages(
                self.context.messages
            )

            # Format output (sync operation, wrap if needed)
            s3_url, value_info = await sync_to_async(
                AudioProcessor.format_audio_output
            )(
                audio_bytes=audio_data,
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
            if is_endpoint_error(e):
                # A tenant endpoint's error: generic already; logged without
                # text or traceback.
                self.logger.warning(
                    "Audio-preview TTS async execution failed",
                    error_type=type(e).__name__,
                    category=getattr(e, "category", None),
                )
            else:
                self.logger.exception(
                    f"Audio-preview TTS async execution failed: {str(e)}"
                )
            return self._build_handler_response(
                response=None,
                start_time=start_time,
                failure=str(e),
            )
