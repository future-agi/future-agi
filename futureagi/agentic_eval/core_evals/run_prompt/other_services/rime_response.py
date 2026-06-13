import requests
import time
import structlog

from tfc.utils.http_timeouts import LLM_HTTP_TIMEOUT

logger = structlog.get_logger(__name__)


def get_rime_tts_parameters(model_name: str):
    """Returns parameters for Rime TTS models.

    Updated for 2026 API specifications supporting:
    - arcana: Highly expressive voices with emotional nuance (April 2025)
    - mistv2: Multi-lingual with ultra-fast latency ~70ms (February 2025)
    - mist: Original conversational speech engine (default if unspecified)
    """
    return {
        "sliders": [
            {
                "label": "repetition_penalty",
                "min": 1.0,
                "max": 2.0,
                "step": 0.1,
                "default": 1.5,
            },
            {
                "label": "temperature",
                "min": 0.0,
                "max": 1.0,
                "step": 0.1,
                "default": 0.5,
            },
            {
                "label": "top_p",
                "min": 0.0,
                "max": 1.0,
                "step": 0.1,
                "default": 1.0,
            },
            {
                "label": "max_tokens",
                "min": 200,
                "max": 5000,
                "step": 100,
                "default": 1200,
            },
            {
                "label": "speed_alpha",
                "min": 0.5,
                "max": 2.0,
                "step": 0.1,
                "default": 1.0,
            },
        ],
        "dropdowns": [
            {
                "label": "samplingRate",
                "options": [8000, 16000, 22050, 24000, 44100, 48000, 96000],
                "default": 24000,
            },
            {
                "label": "lang",
                "options": ["eng", "spa"],
                "default": "eng",
            },
        ],
        "checkboxes": [
            {
                "label": "reduce_latency",
                "default": False,
            },
            {
                "label": "phonemize_between_brackets",
                "default": False,
            },
        ],
    }


def rime_speech_response(run_prompt_instance, start_time, api_key):
    """Handles Text-to-Speech generation using the Rime AI API.

    Updated for 2026 API with support for:
    - Latest models: arcana, mistv2, mist
    - Speed control via speed_alpha parameter
    - Language selection (English, Spanish)
    - Latency optimization options
    - Phonemization for custom pronunciation

    API Endpoint: https://users.rime.ai/v1/rime-tts
    Documentation: https://docs.rime.ai/api-reference/quickstart
    """
    input_text = run_prompt_instance._get_input_text_from_messages()

    url = "https://users.rime.ai/v1/rime-tts"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "audio/mp3",
    }

    # Rime uses 'speaker' for voice selection
    cfg = run_prompt_instance.run_prompt_config or {}
    # Support both voice and voice_id fields
    speaker_name = cfg.get("voice") or cfg.get("voice_id") or "celeste"

    # Build payload with required and optional parameters
    payload = {
        "text": input_text,
        "speaker": speaker_name,
        "modelId": run_prompt_instance.model,  # arcana, mistv2, or mist (default)
        "samplingRate": cfg.get("samplingRate", 24000),
        "lang": cfg.get("lang", "eng"),  # Language: eng or spa
        "repetition_penalty": cfg.get("repetition_penalty"),
        "temperature": cfg.get("temperature"),
        "top_p": cfg.get("top_p"),
        "max_tokens": cfg.get("max_tokens"),
        "speed_alpha": cfg.get("speed_alpha"),  # Speed control (new parameter)
        "reduce_latency": cfg.get(
            "reduce_latency"
        ),  # Disable text normalization for speed
        "phonemize_between_brackets": cfg.get(
            "phonemize_between_brackets"
        ),  # Custom pronunciation
    }

    # Remove None values from payload
    payload = {k: v for k, v in payload.items() if v is not None}

    logger.info(
        f"Rime AI TTS request initiated - model: {run_prompt_instance.model}, speaker: {speaker_name}, "
        f"lang: {payload.get('lang', 'eng')}, input_length: {len(input_text)}"
    )

    response = requests.post(url, json=payload, headers=headers, timeout=LLM_HTTP_TIMEOUT)
    response.raise_for_status()
    audio_content = response.content

    return run_prompt_instance._format_audio_output(
        audio_content, start_time, input_text
    )
