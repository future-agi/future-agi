"""
Adapter utilities for bridging ModelHandlerContext to legacy handler interfaces.

This module provides adapter classes that make the new context-based interface
compatible with existing handler implementations (ElevenLabs, Deepgram, etc.)
that expect a RunPrompt-like object.
"""

from typing import Any, Dict

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.audio_processor import (
    AudioProcessor,
)


class RunPromptAdapter:
    """
    Adapter that provides RunPrompt-like interface for custom handlers.

    The existing speech and transcription handlers (ElevenLabs, Deepgram, etc.)
    expect a run_prompt_instance with specific methods. This adapter bridges
    the ModelHandlerContext to that interface.

    This is a shared adapter used by both TTS and STT handlers.
    """

    def __init__(self, context: ModelHandlerContext):
        """
        Initialize adapter from context.

        Args:
            context: ModelHandlerContext with TTS/STT configuration
        """
        self.model = context.model
        self.messages = context.messages
        self.organization_id = context.organization_id
        self.workspace_id = context.workspace_id
        self.output_format = context.output_format
        self.run_prompt_config = context.run_prompt_config
        self._context = context

    def _get_input_text_from_messages(self) -> str:
        """
        Extract text from messages for TTS input.

        Returns:
            Concatenated text content from messages
        """
        return AudioProcessor.extract_literal_speech_script(self.messages)

    def _get_input_audio_from_messages(self) -> str:
        """
        Extract audio from messages for STT input.

        Returns:
            Audio data as URL string or base64 encoded string
        """
        return AudioProcessor.extract_audio_from_messages(self.messages)

    def _format_audio_output(
        self, audio_bytes: bytes, start_time: float, input_text: str
    ) -> tuple:
        """
        Format audio output with metadata.

        Args:
            audio_bytes: Generated audio bytes
            start_time: Request start time
            input_text: Original input text

        Returns:
            Tuple of (s3_url, value_info dict)
        """
        return AudioProcessor.format_audio_output(
            audio_bytes=audio_bytes,
            model=self.model,
            start_time=start_time,
            input_text=input_text,
        )
