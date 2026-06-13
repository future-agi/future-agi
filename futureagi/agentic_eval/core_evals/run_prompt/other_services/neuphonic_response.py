"""
Neuphonic TTS Integration

This module provides integration with the Neuphonic Text-to-Speech API.
API Documentation: https://docs.neuphonic.com
Last Updated: 2026

Neuphonic API Features:
- Server-Side Events (SSE) streaming for low-latency TTS
- WebSocket support for continuous streaming (not used in this implementation)
- Multiple voices with UUID-based identification
- Configurable speed, sampling rate, and encoding
- Supports multiple languages (en, es, de, nl, etc.)

Authentication: API key via X-API-KEY header
Endpoint: https://api.neuphonic.com/sse/speak/{lang_code}
"""

import base64
import io
import json
import time
import wave

import requests
import structlog

from tfc.utils.http_timeouts import LLM_HTTP_TIMEOUT

logger = structlog.get_logger(__name__)

# Voice ID mapping - Maps friendly voice names to Neuphonic voice UUIDs
# Note: To get the latest available voices, you can query the API:
#   - Python SDK: client.voices.list()
#   - REST API: GET https://api.neuphonic.com/voices (with X-API-KEY header)
# Voice availability may depend on your subscription level and selected language.
VOICE_ID_MAP = {
    "Aditya": "7b2486ca-a177-455a-ab9f-7440fc91c98c",
    "Albert": "f8698a9e-947a-43cd-a897-57edd4070a78",
    "Annie": "e564ba7e-aa8d-46a2-96a8-8dffedade48f",
    "Antoine": "7205f431-2448-403c-8e32-aaeb806d8a2b",
    "Callum": "fa634f3b-4ccf-41ae-8162-202570cebef4",
    "Charlotte": "74c8ea3b-6a87-422a-826e-2b98a5979ea2",
    "Dave": "caba5581-7452-4523-8421-97793e90807a",
    "Emily": "fc854436-2dac-4d21-aa69-ae17b54e98eb",
    "Hannah": "ab40ec57-356a-4238-9be3-b39259483cf7",
    "Holly": "8e9c4bc8-3979-48ab-8626-df53befc2090",
    "Ishita": "7f674d6e-8f32-4e32-8e98-4df3e88610ef",
    "Jack": "24a451b8-30b6-4bbe-9646-63e6900e10de",
    "Jo": "06fde793-8929-45f6-8a87-643196d376e4",
    "Julia": "b8778b12-dc38-452b-b9c5-74a5d1c4f106",
    "Liam": "5f581579-ce62-4634-9f04-2c0815dd2496",
    "Liz": "79ffd956-872a-4b89-b25b-d99bb4335b82",
    "Miles": "b19687fd-c5c9-4bda-9d52-756c3b10c88e",
    "Rebecca": "04378987-5871-4dbf-bacc-a7a8715b6e35",
    "Richard": "6c8ad62b-1356-42df-ac82-706590b7ff43",
    "Wyatt": "f2185de7-e09b-46d7-9b20-8c82ef90524f",
}


def get_neuphonic_tts_parameters(model_name: str):
    """
    Returns parameters for Neuphonic TTS models.

    Supported parameters as of 2026 API version:
    - speed: Playback speed multiplier (0.7-2.0)
    - sampling_rate: Audio quality setting (8000, 16000, 22050 Hz)
    - encoding: Audio encoding format (pcm_linear or pcm_mulaw)

    Args:
        model_name: The TTS model name (currently unused, reserved for future use)

    Returns:
        Dictionary containing parameter definitions for UI configuration
    """
    return {
        "sliders": [
            {
                "label": "speed",
                "min": 0.7,
                "max": 2.0,
                "step": 0.1,
                "default": 1.0,
            }
        ],
        "dropdowns": [
            {
                "label": "sampling_rate",
                "options": [8000, 16000, 22050],
                "default": 22050,
            },
            {
                "label": "encoding",
                "options": ["pcm_linear", "pcm_mulaw"],
                "default": "pcm_linear",
            },
        ],
    }


def neuphonic_speech_response(run_prompt_instance, start_time, api_key):
    """
    Handles Text-to-Speech generation using the Neuphonic AI API.

    Uses Server-Side Events (SSE) endpoint for streaming TTS synthesis.
    API Documentation: https://docs.neuphonic.com
    Updated: 2026

    Args:
        run_prompt_instance: The run prompt instance containing configuration
        start_time: Start time for tracking generation duration
        api_key: Neuphonic API key for authentication

    Returns:
        Formatted audio output with metadata
    """
    input_text = run_prompt_instance._get_input_text_from_messages()

    # SSE endpoint - supports streaming audio generation
    # Language code can be: en, es, de, nl, etc.
    url = "https://api.neuphonic.com/sse/speak/en"

    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    cfg = run_prompt_instance.run_prompt_config or {}
    voice_name = cfg.get("voice", "Holly")
    voice_id_raw = cfg.get("voice_id")

    # Try case-insensitive lookup from voice name
    voice_id = VOICE_ID_MAP.get(voice_name.strip()) or VOICE_ID_MAP.get(
        voice_name.strip().title()
    )

    # If voice name mapping didn't work, check voice_id field
    if not voice_id and voice_id_raw:
        voice_id = VOICE_ID_MAP.get(voice_id_raw) or VOICE_ID_MAP.get(
            voice_id_raw.title()
        )
        if not voice_id and voice_id_raw and len(voice_id_raw) > 20:
            voice_id = voice_id_raw  # Already a valid UUID

    # Final fallback to default voice (Holly)
    if not voice_id:
        voice_id = VOICE_ID_MAP.get("Holly")

    # Build payload according to Neuphonic API specification (2026)
    # Required: text, voice_id
    # Optional: sampling_rate (default: 22050), speed (default: 1.0), encoding (default: pcm_linear)
    payload = {
        "text": input_text,
        "voice_id": voice_id,
        "sampling_rate": cfg.get("sampling_rate", 22050),
        "speed": cfg.get("speed"),
        "encoding": cfg.get("encoding"),
    }

    # Remove None values from payload to use API defaults
    payload = {k: v for k, v in payload.items() if v is not None}

    logger.info(
        f"Neuphonic AI TTS request initiated - voice: {voice_name}, input_length: {len(input_text)}"
    )

    # Send request to SSE endpoint
    response = requests.post(
        url, json=payload, headers=headers, stream=True, timeout=LLM_HTTP_TIMEOUT
    )
    response.raise_for_status()

    # Process SSE stream - audio chunks are base64-encoded and sent in 'data:' events
    audio_content = b""
    for line in response.iter_lines():
        if line:
            decoded_line = line.decode("utf-8")
            # SSE events start with "data:" prefix
            if decoded_line.startswith("data:"):
                try:
                    data_str = decoded_line[5:].strip()
                    if not data_str:
                        continue

                    json_data = json.loads(data_str)

                    # Extract base64-encoded audio chunk from nested structure
                    if "data" in json_data and "audio" in json_data["data"]:
                        audio_chunk = base64.b64decode(json_data["data"]["audio"])
                        audio_content += audio_chunk
                except (json.JSONDecodeError, IndexError, base64.binascii.Error) as e:
                    logger.warning(
                        f"Neuphonic SSE: Could not process line: '{decoded_line}', error: {e}"
                    )

    if not audio_content:
        raise Exception("No audio data received from Neuphonic API")

    # Wrap raw PCM data in a WAV container to make it a valid audio file
    # The API returns raw PCM samples that need WAV headers for proper playback
    sampling_rate = payload.get("sampling_rate", 22050)

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wf:
        wf.setnchannels(1)  # Mono channel
        wf.setsampwidth(2)  # 16-bit PCM (2 bytes per sample)
        wf.setframerate(sampling_rate)  # Use the requested sampling rate
        wf.writeframes(audio_content)

    wav_bytes = wav_buffer.getvalue()

    return run_prompt_instance._format_audio_output(wav_bytes, start_time, input_text)
