import base64
import time

import requests
import structlog

from tfc.utils.http_timeouts import LLM_HTTP_TIMEOUT

logger = structlog.get_logger(__name__)


def get_lmnt_tts_parameters(model_name: str):
    """Returns parameters for LMNT TTS models.

    Updated to support latest LMNT API (2026) parameters including:
    - speed: Controls speech rate (0.25-2.0)
    - length: Target duration in seconds (max 300)
    - model: Synthesis model selection
    - format: Output audio format
    - return_durations: Include word-level timing data

    Note: speed and length are mutually exclusive parameters.
    """
    return {
        "sliders": [
            {
                "label": "speed",
                "min": 0.25,
                "max": 2.0,
                "step": 0.05,
                "default": 1.0,
                "description": "Speech rate (0.25=slow, 2.0=fast). Mutually exclusive with length.",
            },
            {
                "label": "length",
                "min": 0.0,
                "max": 300.0,
                "step": 0.5,
                "default": 0.0,
                "description": "Target duration in seconds (max 5 min). 0=disabled. Mutually exclusive with speed.",
            },
            {
                "label": "seed",
                "min": 0,
                "max": 2147483647,
                "step": 1,
                "default": 0,
                "description": "Seed for deterministic generation (0=random).",
            },
        ],
        "dropdowns": [
            {
                "label": "format",
                "options": ["mp3", "wav", "pcm_16", "pcm_f32", "mulaw", "aac", "webm"],
                "default": "mp3",
                "description": "Output audio format. Streamable: mp3, webm, pcm_16, pcm_f32, mulaw.",
            },
            {
                "label": "sample_rate",
                "options": [8000, 16000, 22050, 24000, 44100],
                "default": 24000,
                "description": "Output sample rate in Hz (mulaw defaults to 8000).",
            },
            {
                "label": "language",
                "options": [
                    "auto",
                    "en",
                    "es",
                    "fr",
                    "de",
                    "it",
                    "pt",
                    "zh",
                    "ja",
                    "ko",
                    "nl",
                    "pl",
                    "ru",
                    "sv",
                    "tr",
                    "uk",
                    "ar",
                    "hi",
                    "cs",
                    "da",
                    "fi",
                ],
                "default": "auto",
                "description": "Two-letter ISO 639-1 language code. Specifying improves speed.",
            },
            {
                "label": "model",
                "options": ["aurora", "blizzard"],
                "default": "aurora",
                "description": "Synthesis model (blizzard doesn't support length parameter).",
            },
        ],
        "checkboxes": [
            {
                "label": "return_durations",
                "default": False,
                "description": "Return word-level timing data with audio.",
            },
        ],
    }


def lmnt_speech_response(run_prompt_instance, start_time, api_key):
    """Handles Text-to-Speech generation using the LMNT API.

    Updated to use latest LMNT API (2026) with improved parameter support:
    - speed/length parameters for speech pacing control
    - model selection (aurora/blizzard)
    - enhanced format options including streamable formats
    - return_durations for word-level timing data
    - expanded language support (21 languages)

    """
    input_text = run_prompt_instance._get_input_text_from_messages()
    cfg = run_prompt_instance.run_prompt_config or {}

    url = "https://api.lmnt.com/v1/ai/speech"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    # Support both voice and voice_id fields
    voice_name = cfg.get("voice") or cfg.get("voice_id") or "leah"

    payload = {
        "text": input_text,
        "voice": voice_name,
        "format": cfg.get("format", "mp3"),
    }

    # Add optional parameters if provided
    if cfg.get("sample_rate"):
        payload["sample_rate"] = int(cfg.get("sample_rate"))

    if cfg.get("language") and cfg.get("language") != "auto":
        payload["language"] = cfg.get("language")

    if cfg.get("model"):
        payload["model"] = cfg.get("model")

    # Handle speed/length parameters (mutually exclusive)
    speed = cfg.get("speed")
    length = cfg.get("length")

    if speed and length and float(length) > 0:
        logger.warning(
            "Both 'speed' and 'length' parameters provided. Using 'speed' only to avoid API error. "
            "These parameters are mutually exclusive."
        )
        payload["speed"] = float(speed)
    elif speed:
        payload["speed"] = float(speed)
    elif length and float(length) > 0:
        payload["length"] = float(length)

    if cfg.get("seed") and int(cfg.get("seed")) > 0:
        payload["seed"] = int(cfg.get("seed"))

    if cfg.get("return_durations"):
        payload["return_durations"] = bool(cfg.get("return_durations"))

    # Legacy parameter support (for backward compatibility)
    # Note: temperature and top_p are not standard LMNT API parameters as of 2026
    if cfg.get("temperature"):
        logger.warning(
            f"'temperature' parameter is not supported by LMNT API. Ignoring value: {cfg.get('temperature')}"
        )
    if cfg.get("top_p"):
        logger.warning(
            f"'top_p' parameter is not supported by LMNT API. Ignoring value: {cfg.get('top_p')}"
        )

    logger.info(
        f"LMNT TTS request initiated - voice: {voice_name}, input_length: {len(input_text)}, "
        f"format: {payload.get('format')}, model: {payload.get('model', 'aurora')}"
    )

    response = requests.post(
        url, json=payload, headers=headers, timeout=LLM_HTTP_TIMEOUT
    )
    response.raise_for_status()

    response_data = response.json()

    if not response_data.get("audio"):
        raise Exception("Invalid response from LMNT AI API: no audio data found.")

    audio_base64 = response_data["audio"]
    audio_bytes = base64.b64decode(audio_base64)

    # Log durations if returned (useful for debugging/analysis)
    if response_data.get("durations"):
        logger.debug(
            f"Received word-level durations: {len(response_data['durations'])} words"
        )

    return run_prompt_instance._format_audio_output(audio_bytes, start_time, input_text)
