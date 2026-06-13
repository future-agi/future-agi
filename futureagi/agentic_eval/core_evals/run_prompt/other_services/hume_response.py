import base64
import time

import requests
import structlog

from tfc.utils.http_timeouts import LLM_HTTP_TIMEOUT

logger = structlog.get_logger(__name__)


def get_hume_tts_parameters(model_name: str):
    """Returns parameters for Hume TTS models.

    Updated for 2026 API specifications:
    - speed: 0.75-1.5 recommended range (supports 0.25-3.0)
    - trailing_silence: 0.0-5.0 seconds, default 0.35
    - description: Natural language acting instructions
    """
    return {
        "sliders": [
            {
                "label": "speed",
                "min": 0.75,
                "max": 1.5,
                "step": 0.05,
                "default": 1.0,
            },
            {
                "label": "trailing_silence",
                "min": 0.0,
                "max": 5.0,
                "step": 0.05,
                "default": 0.35,
            },
        ],
        "text_inputs": [
            {
                "label": "description",
                "default": "",
            },
        ],
    }


def hume_speech_response(run_prompt_instance, start_time, api_key):
    """Handles Text-to-Speech generation using the Hume AI API.

    Updated for 2026 Hume AI Octave TTS API:
    - API endpoint: https://api.hume.ai/v0/tts
    - Voice library: HUME_AI provider with 100+ voices
    - Default voice: "ITO" (one of the curated Octave voices)
    - Supports: speed (0.75-1.5), trailing_silence (0.0-5.0s), description (acting instructions)
    - Output formats: mp3, wav, pcm (default: mp3)
    - Version 2 (Octave 2) requires explicit voice specification
    """
    input_text = run_prompt_instance._get_input_text_from_messages()
    cfg = run_prompt_instance.run_prompt_config or {}

    url = "https://api.hume.ai/v0/tts"
    headers = {"X-Hume-Api-Key": api_key, "Content-Type": "application/json"}

    # Default to ITO voice from Hume's Voice Library
    # Note: "Colton Rivers" may not be available in 2026 library
    # Popular voices include: ITO, STELLA, DACHER, WHIMSY, AURA, KORA
    # Support both voice and voice_id fields
    voice_name = cfg.get("voice") or cfg.get("voice_id") or "ITO"

    # Build utterance object with optional parameters
    utterance = {
        "text": input_text,
        "voice": {"name": voice_name, "provider": "HUME_AI"},
    }

    # Add optional parameters if provided
    # Speed: recommended range 0.75-1.5 (supports 0.25-3.0)
    if cfg.get("speed"):
        utterance["speed"] = float(cfg.get("speed"))

    # Trailing silence: 0.0-5.0 seconds, default 0.35
    if cfg.get("trailing_silence") is not None:
        utterance["trailing_silence"] = float(cfg.get("trailing_silence"))

    # Description: natural language acting instructions for voice delivery
    if cfg.get("description"):
        utterance["description"] = cfg.get("description")

    # Build payload with format specification
    # Supported formats: mp3, wav, pcm
    audio_format = cfg.get("format", "mp3")
    payload = {"utterances": [utterance], "format": {"type": audio_format}}

    # Optionally specify Octave version (1 or 2)
    # Version 2 requires voice specification and provides latest features
    if cfg.get("version"):
        payload["version"] = str(cfg.get("version"))

    logger.info(
        f"Hume AI TTS request initiated - voice: {voice_name}, "
        f"format: {audio_format}, input_length: {len(input_text)}"
    )

    response = requests.post(
        url, json=payload, headers=headers, timeout=LLM_HTTP_TIMEOUT
    )
    response.raise_for_status()

    response_data = response.json()

    if not response_data.get("generations") or not response_data["generations"][0].get(
        "audio"
    ):
        raise Exception("Invalid response from Hume AI API: no audio data found.")

    audio_base64 = response_data["generations"][0]["audio"]
    audio_bytes = base64.b64decode(audio_base64)

    return run_prompt_instance._format_audio_output(audio_bytes, start_time, input_text)
