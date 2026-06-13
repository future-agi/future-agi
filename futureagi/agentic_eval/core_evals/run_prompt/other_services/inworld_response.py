import requests
import base64
import time
import structlog

from tfc.utils.http_timeouts import LLM_HTTP_TIMEOUT

logger = structlog.get_logger(__name__)


def inworld_speech_response(run_prompt_instance, start_time, api_key):
    """Handles Text-to-Speech generation using the Inworld AI API.

    Updated to use Inworld TTS API v1 with support for:
    - Audio configuration (encoding, sample rate, speaking rate)
    - Temperature control (0-2 range)
    - Timestamp alignment
    - Text normalization
    """
    input_text = run_prompt_instance._get_input_text_from_messages()

    # Validate input text length (max 2000 characters per API spec)
    if len(input_text) > 2000:
        logger.warning(
            f"Input text exceeds 2000 character limit: {len(input_text)} characters"
        )
        input_text = input_text[:2000]

    url = "https://api.inworld.ai/tts/v1/voice"

    headers = {"Authorization": f"Basic {api_key}", "Content-Type": "application/json"}

    cfg = run_prompt_instance.run_prompt_config or {}
    # Support both voice and voice_id fields
    voice_name = cfg.get("voice") or cfg.get("voice_id") or "Dennis"
    temperature = cfg.get("temperature", 1.1)  # Default to 1.1 per API spec

    # Build payload with required fields
    payload = {
        "text": input_text,
        "voiceId": voice_name,
        "modelId": run_prompt_instance.model,
        "temperature": temperature,
    }

    # Build audioConfig with optional parameters
    audio_config = {}

    # Audio encoding (default: MP3 per API spec)
    audio_encoding = cfg.get("audio_encoding", "MP3")
    if audio_encoding:
        audio_config["audioEncoding"] = audio_encoding

    # Sample rate (default: 48000 Hz, range: 8000-48000)
    sample_rate = cfg.get("sample_rate_hertz", 48000)
    if sample_rate:
        audio_config["sampleRateHertz"] = sample_rate

    # Speaking rate (default: 1.0, range: 0.5-1.5)
    speaking_rate = cfg.get("speaking_rate")
    if speaking_rate is not None:
        audio_config["speakingRate"] = speaking_rate

    # Bit rate for compressed formats (default: 128000)
    bit_rate = cfg.get("bit_rate")
    if bit_rate is not None:
        audio_config["bitRate"] = bit_rate

    if audio_config:
        payload["audioConfig"] = audio_config

    # Optional timestamp alignment (WORD, CHARACTER, or TIMESTAMP_TYPE_UNSPECIFIED)
    timestamp_type = cfg.get("timestamp_type")
    if timestamp_type:
        payload["timestampType"] = timestamp_type

    # Optional text normalization (ON, OFF, or UNSPECIFIED)
    text_normalization = cfg.get("apply_text_normalization")
    if text_normalization:
        payload["applyTextNormalization"] = text_normalization

    logger.info(
        f"Inworld TTS request initiated - model: {run_prompt_instance.model}, voiceId: {voice_name}, temperature: {temperature}, input_length: {len(input_text)}"
    )

    response = requests.post(url, json=payload, headers=headers, timeout=LLM_HTTP_TIMEOUT)
    response.raise_for_status()
    result = response.json()
    audio_content = base64.b64decode(result["audioContent"])

    return run_prompt_instance._format_audio_output(
        audio_content, start_time, input_text
    )


def get_inworld_tts_parameters(model_name: str):
    """Returns parameters for Inworld TTS models.

    Updated to include new API parameters for TTS 1.5:
    - Temperature: Voice variability control (0-2 range, default 1.1)
    - Speaking Rate: Speed control (0.5-1.5 range, default 1.0)
    - Audio Encoding: Format selection (MP3, LINEAR16, OGG_OPUS, etc.)
    - Sample Rate: Audio quality (8000-48000 Hz, default 48000)
    """
    return {
        "sliders": [
            {
                "label": "temperature",
                "min": 0.0,
                "max": 2.0,
                "step": 0.1,
                "default": 1.1,
            },
            {
                "label": "speaking_rate",
                "min": 0.5,
                "max": 1.5,
                "step": 0.1,
                "default": 1.0,
            },
            {
                "label": "sample_rate_hertz",
                "min": 8000,
                "max": 48000,
                "step": 1000,
                "default": 48000,
            },
        ],
        "dropdowns": [
            {
                "label": "audio_encoding",
                "options": ["MP3", "LINEAR16", "OGG_OPUS", "ALAW", "MULAW", "FLAC"],
                "default": "MP3",
            },
            {
                "label": "timestamp_type",
                "options": ["TIMESTAMP_TYPE_UNSPECIFIED", "WORD", "CHARACTER"],
                "default": "TIMESTAMP_TYPE_UNSPECIFIED",
            },
            {
                "label": "apply_text_normalization",
                "options": ["UNSPECIFIED", "ON", "OFF"],
                "default": "UNSPECIFIED",
            },
        ],
    }
