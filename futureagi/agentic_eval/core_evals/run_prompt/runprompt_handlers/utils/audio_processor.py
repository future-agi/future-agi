"""
Audio processing utilities for TTS and STT handlers.

This module provides utilities for:
- Extracting audio from messages
- Processing and uploading audio output
- Estimating audio duration
- Normalizing audio formats for different providers
- Converting raw PCM16 to WAV format
"""

import base64
import io
import time
import wave
from typing import Any, Dict, List, Optional, Tuple, Union

import av
import structlog

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from tfc.utils.storage import (
    audio_bytes_from_url_or_base64,
    convert_to_mp3,
    detect_audio_format,
    get_audio_duration,
    upload_audio_to_s3,
)
try:
    from ee.usage.utils.usage_entries import count_tiktoken_tokens
except ImportError:
    from tfc.oss_stubs.usage import count_tiktoken_tokens

logger = structlog.get_logger(__name__)


# Constants
MAX_TTS_TEXT_LENGTH = 4000  # OpenAI TTS has 4096 char limit, use conservative 4000
AUDIO_TOKENS_PER_SECOND = 32  # Standard rate for audio token estimation

# Allowed audio formats for STT (Whisper API compatible)
ALLOWED_STT_FORMATS = {
    "flac",
    "m4a",
    "mp3",
    "mp4",
    "mpeg",
    "mpga",
    "oga",
    "ogg",
    "wav",
    "webm",
}

# Format extension mapping for STT
STT_FORMAT_EXTENSION_MAP = {"mpeg": "mp3", "mpga": "mp3"}


class AudioProcessor:
    """
    Utilities for processing audio in TTS and STT handlers.

    Provides methods for:
    - Extracting text input from messages for TTS
    - Extracting audio input from messages for STT
    - Processing and uploading generated audio
    - Estimating audio duration for billing
    """

    @staticmethod
    def extract_text_from_messages(messages: List[Dict[str, Any]]) -> str:
        """
        Extract and concatenate text content from messages for TTS input.

        Handles standard message formats:
        - {"role": "user", "content": "Hello world"}
        - {"role": "user", "content": [{"type": "text", "text": "..."}]}

        Args:
            messages: List of message dictionaries

        Returns:
            Concatenated text from all messages

        Raises:
            ValueError: If no text found or text exceeds max length
        """
        text_parts = []

        for msg in messages:
            content = msg.get("content")

            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))

        input_text = " ".join(text_parts)

        if not input_text.strip():
            raise ValueError("No text found in messages to generate speech.")

        # Validate length
        if len(input_text) > MAX_TTS_TEXT_LENGTH:
            raise ValueError(
                f"Text too long for TTS: {len(input_text)} characters "
                f"(max: {MAX_TTS_TEXT_LENGTH})"
            )

        return input_text

    @staticmethod
    def extract_audio_from_messages(messages: List[Dict[str, Any]]) -> str:
        """
        Extract audio data (URL or base64 string) from messages.

        Handles formats from:
        1. Placeholder replacement: {"type": "input_audio", "input_audio": {"data": "..."}}
        2. Direct file upload: {"type": "audio_url", "audioUrl": {"url": "..."}}

        Args:
            messages: List of message dictionaries

        Returns:
            Audio data as URL string or base64 encoded string

        Raises:
            ValueError: If no audio input found in messages
        """
        logger.info("[STT] Searching for audio in messages")

        for msg in messages:
            content = msg.get("content")

            if not isinstance(content, list):
                continue

            for part in content:
                if not isinstance(part, dict):
                    continue

                part_type = part.get("type")

                # Case 1: From placeholder replacement
                if part_type == "input_audio":
                    input_audio_payload = part.get("input_audio")
                    if (
                        isinstance(input_audio_payload, dict)
                        and "data" in input_audio_payload
                    ):
                        logger.info("[STT] Found audio data in 'input_audio' part.")
                        return input_audio_payload["data"]

                # Case 2: From direct upload
                if part_type == "audio_url":
                    audio_url_payload = part.get("audioUrl") or part.get("audio_url")
                    if (
                        isinstance(audio_url_payload, dict)
                        and "url" in audio_url_payload
                    ):
                        logger.info("[STT] Found audio URL in 'audio_url' part.")
                        return audio_url_payload["url"]

        logger.error(
            f"[STT] No audio input found in messages. Sample: {str(messages)[:500]}"
        )
        raise ValueError("No audio input found in messages for STT.")

    @staticmethod
    def format_audio_output(
        audio_bytes: Union[bytes, str],
        model: str,
        start_time: float,
        input_text: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Process generated audio and upload to S3.

        Args:
            audio_bytes: Raw audio bytes or base64 encoded string
            model: Model name for cost calculation
            start_time: Request start time
            input_text: Original text input (for token counting)

        Returns:
            Tuple of (s3_url, value_info dict)
        """
        # Handle base64 string input
        if isinstance(audio_bytes, str):
            audio_data = base64.b64decode(audio_bytes)
        else:
            audio_data = audio_bytes

        # Estimate audio duration
        duration_seconds = AudioProcessor.estimate_audio_duration(audio_data)

        # Upload to S3
        s3_url = upload_audio_to_s3(audio_data)

        end_time = time.time()
        completion_time = (end_time - start_time) * 1000

        # Calculate token usage
        try:
            prompt_tokens = count_tiktoken_tokens(input_text)
        except Exception:
            prompt_tokens = None

        completion_tokens = (
            int(duration_seconds * AUDIO_TOKENS_PER_SECOND)
            if duration_seconds
            else None
        )

        total_tokens = (
            ((prompt_tokens or 0) + (completion_tokens or 0))
            if (prompt_tokens is not None or completion_tokens is not None)
            else None
        )

        # Build usage/cost
        usage_payload = {
            "prompt_tokens": prompt_tokens or 0,
            "completion_tokens": completion_tokens or 0,
            "input_characters": len(input_text),
        }

        try:
            cost_payload = (
                calculate_total_cost(model, usage_payload)
                if (prompt_tokens is not None and completion_tokens is not None)
                else {"total_cost": 0.0, "prompt_cost": 0.0, "completion_cost": 0.0}
            )
        except Exception:
            cost_payload = {
                "total_cost": 0.0,
                "prompt_cost": 0.0,
                "completion_cost": 0.0,
            }

        metadata = {
            "usage": {
                **usage_payload,
                "total_tokens": total_tokens,
                "input_characters": len(input_text),
            },
            "cost": cost_payload,
            "response_time": completion_time,
        }

        if duration_seconds:
            metadata["audio_duration"] = duration_seconds

        value_info = {
            "name": None,
            "data": {"response": s3_url},
            "failure": None,
            "runtime": completion_time,
            "model": model,
            "metrics": [],
            "metadata": metadata,
            "output": None,
        }

        return s3_url, value_info

    @staticmethod
    def estimate_audio_duration(audio_data: bytes) -> Optional[float]:
        """
        Estimate audio duration in seconds using PyAV.

        Tries multiple methods:
        1. Container duration
        2. Audio stream duration
        3. Frame counting (fallback)

        Args:
            audio_data: Raw audio bytes

        Returns:
            Duration in seconds, or None if unable to determine
        """
        if not audio_data:
            return None

        try:
            with io.BytesIO(audio_data) as buf:
                container = av.open(buf)
                try:
                    # Method 1: Container duration (most reliable)
                    if container.duration:
                        return container.duration / 1_000_000.0

                    # Method 2: Audio stream duration
                    audio_stream = container.streams.audio[0]
                    if audio_stream.duration and audio_stream.time_base:
                        return float(audio_stream.duration * audio_stream.time_base)

                    # Method 3: Frame counting (fallback)
                    total_frames = 0
                    for packet in container.demux(audio_stream):
                        for frame in packet.decode():
                            total_frames += frame.samples

                    if total_frames > 0 and audio_stream.rate:
                        return total_frames / float(audio_stream.rate)

                finally:
                    container.close()

        except Exception as e:
            logger.warning(f"Unable to estimate audio duration: {str(e)}")

        return None

    @staticmethod
    def normalize_audio_for_stt(
        audio_input: Union[str, bytes, Dict],
    ) -> Tuple[bytes, str]:
        """
        Normalize audio input for STT API compatibility.

        - Converts URLs and base64 to bytes
        - Detects format
        - Converts to MP3 if format not supported

        Args:
            audio_input: URL, base64 string, or dict with audio data

        Returns:
            Tuple of (audio_bytes, file_extension)
        """
        # Get raw bytes
        audio_bytes = audio_bytes_from_url_or_base64(audio_input)

        logger.info(f"[STT] Normalized bytes length={len(audio_bytes)}")

        # Detect format
        try:
            detected = (detect_audio_format(audio_bytes) or "").lower()
        except Exception:
            detected = ""

        # Check if format is supported
        file_ext = detected if detected in ALLOWED_STT_FORMATS else None

        if not file_ext:
            try:
                audio_bytes, _ = convert_to_mp3(audio_bytes)
                file_ext = "mp3"
            except Exception:
                # Fallback: try with wav extension
                file_ext = "wav"

        # Apply extension mapping
        file_ext = STT_FORMAT_EXTENSION_MAP.get(file_ext, file_ext)

        return audio_bytes, file_ext

    @staticmethod
    def create_audio_buffer(audio_bytes: bytes, file_ext: str) -> io.BytesIO:
        """
        Create a BytesIO buffer with proper name attribute for API upload.

        The name attribute is used by OpenAI multipart upload to infer content type.

        Args:
            audio_bytes: Audio data bytes
            file_ext: File extension (without dot)

        Returns:
            BytesIO object with name attribute set
        """
        buf = io.BytesIO(audio_bytes)
        buf.name = f"audio.{file_ext}"
        buf.seek(0)
        return buf

    @staticmethod
    def build_speech_params(
        model: str,
        input_text: str,
        api_key: Any,
        run_prompt_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Build provider-specific speech parameters for TTS.

        Handles voice configuration for different providers:
        - vertex_ai/chirp: Requires voice as dict (standard Vertex AI TTS)
        - vertex_ai/gemini-*-tts: Uses Gemini TTS via completion bridge
        - gemini/: Uses string voice (Google AI Studio Gemini)
        - OpenAI/others: Uses string voice and format

        Args:
            model: TTS model name
            input_text: Text to convert to speech
            api_key: API key for the provider
            run_prompt_config: Additional configuration including voice settings

        Returns:
            Dictionary of speech parameters
        """
        speech_params = {
            "model": model,
            "input": input_text,
            "api_key": api_key,
        }

        model_lower = model.lower()

        # Check if this is a Vertex AI Gemini TTS model (uses completion bridge)
        # These models use the same voice format as Google AI Studio Gemini
        is_vertex_gemini_tts = (
            model_lower.startswith("vertex_ai/gemini") and "tts" in model_lower
        )

        if model_lower.startswith("vertex_ai/") and not is_vertex_gemini_tts:
            # Standard Vertex AI TTS (Chirp) - requires voice as a dict
            cfg_voice = run_prompt_config.get("voice")
            if isinstance(cfg_voice, dict):
                speech_params["voice"] = cfg_voice

            # Support audioConfig if provided
            audio_config = run_prompt_config.get("audioConfig")
            if isinstance(audio_config, dict):
                speech_params["audioConfig"] = audio_config

        elif model_lower.startswith("gemini/") or is_vertex_gemini_tts:
            # Gemini TTS models (both Google AI Studio and Vertex AI)
            # These use litellm's speech-to-completion bridge internally
            # Use voice_id if present (resolved provider ID), otherwise use voice
            cfg_voice = run_prompt_config.get("voice_id") or run_prompt_config.get(
                "voice"
            )
            if isinstance(cfg_voice, str):
                speech_params["voice"] = cfg_voice

            cfg_format = run_prompt_config.get("format")
            if isinstance(cfg_format, str):
                speech_params["format"] = cfg_format

        else:
            # OpenAI and others accept simple string voice/format
            # Use voice_id if present (resolved provider ID), otherwise use voice
            cfg_voice = run_prompt_config.get("voice_id") or run_prompt_config.get(
                "voice"
            )
            if isinstance(cfg_voice, str):
                speech_params["voice"] = cfg_voice

            cfg_format = run_prompt_config.get("format")
            if isinstance(cfg_format, str):
                speech_params["format"] = cfg_format

        return speech_params

    @staticmethod
    def format_transcription_output(
        transcription_text: str,
        model: str,
        start_time: float,
        audio_bytes: bytes,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Format STT transcription response with proper metadata.

        Args:
            transcription_text: Transcribed text
            model: STT model name
            start_time: Request start time
            audio_bytes: Original audio bytes (for duration calculation)

        Returns:
            Tuple of (transcription_text, value_info dict)
        """
        end_time = time.time()
        completion_time = (end_time - start_time) * 1000

        # Get audio duration
        duration_seconds = get_audio_duration(audio_bytes)

        metadata = {
            "usage": {
                "audio_seconds": duration_seconds,
            },
            "cost": calculate_total_cost(
                model_name=model, token_usage={"audio_seconds": duration_seconds}
            ),
            "response_time": completion_time,
        }

        value_info = {
            "name": None,
            "data": {"response": transcription_text},
            "failure": None,
            "runtime": completion_time,
            "model": model,
            "metrics": [],
            "metadata": metadata,
            "output": None,
        }

        return transcription_text, value_info

    @staticmethod
    def pcm16_to_wav(
        pcm_data: bytes,
        sample_rate: int = 24000,
        num_channels: int = 1,
        sample_width: int = 2,
    ) -> bytes:
        """
        Convert raw PCM16 audio data to WAV format.

        Gemini TTS returns raw PCM16 audio without container format.
        FFmpeg and most audio tools require a container format (like WAV)
        to detect audio properties.

        Args:
            pcm_data: Raw PCM16 audio bytes
            sample_rate: Sample rate in Hz (default: 24000 for Gemini)
            num_channels: Number of audio channels (default: 1 for mono)
            sample_width: Bytes per sample (default: 2 for 16-bit)

        Returns:
            WAV-formatted audio bytes
        """
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(num_channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_data)
        return buffer.getvalue()
