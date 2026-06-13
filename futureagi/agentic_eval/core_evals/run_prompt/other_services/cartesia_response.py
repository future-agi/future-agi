import time

import requests
import structlog

from tfc.utils.http_timeouts import HEALTHCHECK_HTTP_TIMEOUT, LLM_HTTP_TIMEOUT

logger = structlog.get_logger(__name__)

VOICE_ID_MAP = {
    # Recommended voices for voice agents (stable, realistic)
    "Katie": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
    "Kiefer": "228fca29-3a0a-435c-8728-5cb483251068",
    # Emotive voices (expressive, character work)
    "Tessa": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
    "Kyle": "c961b81c-a935-4c17-bfb3-ba2239de8c2f",
    # Legacy voice names (backwards compatibility)
    "Katie - Friendly Fixer": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
    "Brooke - Big Sister": "e07c00bc-4134-4eae-9ea4-1a55fb45746b",
    "Jacqueline - Reassuring Agent": "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
    "Caroline - Southern Guide": "f9836c6e-a0bd-460e-9d3c-f7299fa60f94",
    "Ronald - Thinker": "5ee9feff-1265-424a-9d7f-8e4d431a12c7",
    "Linda - Conversational Guide": "829ccd10-f8b3-43cd-b8a0-4aeaa81f3b30",
    "Brandon": "5cad89c9-d88a-4832-89fb-55f2f16d13d3",
    "Ariana": "ec1e269e-9ca0-402f-8a18-58e0e022355a",
}


def get_cartesia_tts_parameters(model_name: str):
    """Returns parameters for Cartesia TTS models (Sonic-3).

    Sonic-3 (latest stable: sonic-3-2026-01-12) supports:
    - Fine-grained control on volume, speed, and emotion
    - 50+ emotions with realistic expression
    - 42 languages with native voices
    - AI laughter via [laughter] tags in text
    """
    return {
        "sliders": [
            {
                "label": "volume",
                "min": 0.5,
                "max": 2.0,
                "step": 0.1,
                "default": 1.0,
            },
            {
                "label": "speed",
                "min": 0.6,
                "max": 1.5,
                "step": 0.1,
                "default": 1.0,
            },
        ],
        "dropdowns": [
            {
                "label": "emotion",
                "options": [
                    "neutral",
                    "happy",
                    "excited",
                    "enthusiastic",
                    "elated",
                    "euphoric",
                    "triumphant",
                    "amazed",
                    "surprised",
                    "flirtatious",
                    "curious",
                    "content",
                    "peaceful",
                    "serene",
                    "calm",
                    "grateful",
                    "affectionate",
                    "trust",
                    "sympathetic",
                    "anticipation",
                    "mysterious",
                    "angry",
                    "mad",
                    "outraged",
                    "frustrated",
                    "agitated",
                    "threatened",
                    "disgusted",
                    "contempt",
                    "envious",
                    "sarcastic",
                    "ironic",
                    "sad",
                    "dejected",
                    "melancholic",
                    "disappointed",
                    "hurt",
                    "guilty",
                    "bored",
                    "tired",
                    "rejected",
                    "nostalgic",
                    "wistful",
                    "apologetic",
                    "hesitant",
                    "insecure",
                    "confused",
                    "resigned",
                    "anxious",
                    "panicked",
                    "alarmed",
                    "scared",
                    "proud",
                    "confident",
                    "distant",
                    "skeptical",
                    "contemplative",
                    "determined",
                ],
                "default": "neutral",
            },
            {
                "label": "language",
                "options": [
                    "en",
                    "fr",
                    "de",
                    "es",
                    "pt",
                    "zh",
                    "ja",
                    "hi",
                    "it",
                    "ko",
                    "nl",
                    "pl",
                    "ru",
                    "sv",
                    "tr",
                    "tl",
                    "bg",
                    "ro",
                    "ar",
                    "cs",
                    "el",
                    "fi",
                    "hr",
                    "ms",
                    "sk",
                    "da",
                    "ta",
                    "uk",
                    "hu",
                    "no",
                    "vi",
                    "bn",
                    "th",
                    "he",
                    "ka",
                    "id",
                    "te",
                    "gu",
                    "kn",
                    "ml",
                    "mr",
                    "pa",
                ],
                "default": "en",
            },
        ],
    }


def cartesia_speech_response(run_prompt_instance, start_time, api_key):
    """Handles Text-to-Speech generation using the Cartesia AI API.

    Uses Sonic-3 model with support for:
    - Volume control (0.5x-2.0x)
    - Speed adjustment (0.6x-1.5x)
    - 50+ emotions
    - 42 languages
    - AI laughter via [laughter] tags
    """
    input_text = run_prompt_instance._get_input_text_from_messages()
    cfg = run_prompt_instance.run_prompt_config or {}

    url = "https://api.cartesia.ai/tts/bytes"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Cartesia-Version": "2025-04-16",  # Latest API version
        "Content-Type": "application/json",
    }

    # Frontend sends: { "voice": "My Custom Voice", "voice_id": "resolved-provider-id" }
    # or { "voice": "Katie" } for system voices (no voice_id)
    voice_val = cfg.get("voice")
    voice_id_raw = cfg.get("voice_id")

    # Determine the final voice_id with proper mapping
    voice_id = None

    if voice_val:
        voice_id = VOICE_ID_MAP.get(voice_val) or VOICE_ID_MAP.get(voice_val.title())

    # If voice name mapping didn't work, check voice_id field
    if not voice_id and voice_id_raw:
        voice_id = VOICE_ID_MAP.get(voice_id_raw) or VOICE_ID_MAP.get(
            voice_id_raw.title()
        )
        if not voice_id and voice_id_raw and len(voice_id_raw) > 20:
            voice_id = voice_id_raw  # Already a valid UUID

    # Final fallback to default voice (Katie)
    if not voice_id:
        voice_id = VOICE_ID_MAP.get("Katie")

    payload = {
        "model_id": "sonic-3",  # Latest stable model (auto-updates to sonic-3-2026-01-12)
        "transcript": input_text,
        "voice": {"mode": "id", "id": voice_id},
        "output_format": {"container": "mp3", "encoding": "mp3", "sample_rate": 44100},
    }

    # Add generation_config for sonic-3 parameters
    generation_config = {}
    if cfg.get("volume"):
        generation_config["volume"] = float(cfg.get("volume"))
    if cfg.get("speed"):
        generation_config["speed"] = float(cfg.get("speed"))
    if cfg.get("emotion"):
        generation_config["emotion"] = cfg.get("emotion")

    if generation_config:
        payload["generation_config"] = generation_config

    # Add language if specified
    if cfg.get("language"):
        payload["language"] = cfg.get("language")

    logger.info(
        f"Cartesia AI TTS request initiated - voice: {voice_id}, input_length: {len(input_text)}"
    )

    response = requests.post(
        url, json=payload, headers=headers, timeout=LLM_HTTP_TIMEOUT
    )
    response.raise_for_status()

    audio_bytes = response.content

    return run_prompt_instance._format_audio_output(audio_bytes, start_time, input_text)


def validate_cartesia_voice(voice_id, api_key):
    """Validates if a voice ID exists in Cartesia.

    Uses GET /voices/{voice_id} endpoint to verify voice existence.
    """
    url = f"https://api.cartesia.ai/voices/{voice_id}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Cartesia-Version": "2025-04-16",  # Updated to latest API version
    }
    response = requests.get(url, headers=headers, timeout=HEALTHCHECK_HTTP_TIMEOUT)

    if response.status_code == 200:
        return True
    elif response.status_code == 404:
        raise ValueError(f"Voice ID '{voice_id}' not found in Cartesia.")
    else:
        raise ValueError(f"Cartesia API error: {response.status_code} {response.text}")
