"""
Smallest AI Integration — Waves TTS and Pulse STT

Source: https://docs.smallest.ai

TTS (Waves):
- REST endpoint: POST https://api.smallest.ai/waves/v1/{model-slug}/get_speech
- WebSocket streaming: wss://api.smallest.ai/waves/v1/tts/live
- Models: lightning_v3.1, lightning_v3.1_pro
- Auth: Authorization: Bearer {api_key}
- Body: {"text", "voice_id", "sample_rate", "speed", "add_wav_header", "language"}

STT (Pulse):
- REST endpoint: POST https://api.smallest.ai/waves/v1/stt/
- Models: pulse (streaming-only WebSocket), pulse-pro (REST, higher accuracy)
- Auth: Authorization: Bearer {api_key}
- Body: multipart/form-data with audio file; ?model=pulse-pro as query param
"""

import time
from io import BytesIO

import requests
import structlog

from tfc.utils.storage import audio_bytes_from_url_or_base64, get_audio_duration

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.smallest.ai/waves/v1"

# Voice IDs as returned by GET /waves/v1/lightning-v3.1/get_voices (all lowercase).
# Both lightning_v3.1 and lightning_v3.1_pro share the same endpoint; pro is a
# curated subset of the full catalog.
LIGHTNING_V3_1_VOICES = [
    # English (US)
    "avery", "mia", "magnus", "olivia", "daniel", "rachel", "nicole", "elizabeth",
    "quinn", "sophia", "robert", "sandra", "brian", "ella", "alex", "lucas",
    "natasha", "harper", "alice", "jessica", "jordan", "kyle", "tara",
    # English (British)
    "liam", "noah", "edward", "isla", "julia", "alistair",
    # English (Australian)
    "chloe", "cooper", "sienna", "flynn", "nyah",
    # English (Canadian)
    "william", "erica", "alec",
    # Hindi / English (Indian accent)
    "neel", "maithili", "devansh", "sameera", "mihir", "aarush", "sakshi", "vivaan",
    "srishti", "maya", "anika", "sanjay", "arjun", "advika", "aisha", "gaurav",
    "ishani", "yuvika", "sana", "kunal", "meher", "saira", "kareena", "chinmayi",
    "sunidhi", "aarini", "inaaya", "rhea", "zariya", "mishka", "aviraj", "vyom",
    "zoravar", "reyansh", "ahan",
    # Spanish
    "jose", "mariana", "luis", "daniella", "lucia", "miguel", "javier",
    "camilla", "carlos", "emilio", "rodrigo", "marcos", "david", "isabella",
    "nerea", "alba",
    # South Indian languages
    "anitha", "raju", "shrihari", "padmaja", "deepashri", "jeevan", "rajeshwari",
    "shibi", "vaisakh", "pranav",
    # British (pro-catalog voices, same endpoint)
    "benedict", "cormac", "everett", "finley", "rupert", "winston", "caspian",
    "cressida", "elowen", "ottilie", "seraphina", "tabitha", "arabella",
    # American (pro-catalog voices)
    "maverick", "brooks", "asher", "wesley", "hunter", "colton",
    "willow", "autumn", "skylar", "savannah", "kennedy", "reagan", "sierra",
]

LIGHTNING_V3_1_PRO_VOICES = [
    # Indian (curated)
    "rhea", "zariya", "kareena", "mishka", "inaaya", "saira", "meher", "aarini",
    "aviraj", "vyom", "zoravar", "reyansh", "ahan",
    # British (curated)
    "cressida", "elowen", "ottilie", "seraphina", "tabitha", "arabella",
    "benedict", "cormac", "everett", "finley", "rupert", "winston", "caspian",
    # American (curated)
    "willow", "autumn", "skylar", "savannah", "kennedy", "reagan", "sierra",
    "maverick", "brooks", "hunter", "colton", "wesley", "asher",
]

# Both models use the same REST endpoint; lightning_v3.1_pro just curates a voice subset.
_TTS_ENDPOINT_SLUG = "lightning-v3.1"


def smallest_ai_speech_response(run_prompt_instance, start_time, api_key):
    """Text-to-Speech via Smallest AI Waves REST API."""
    input_text = run_prompt_instance._get_input_text_from_messages()

    model_name_with_provider = run_prompt_instance.model
    model_id = (
        model_name_with_provider.split("/")[-1]
        if "/" in model_name_with_provider
        else model_name_with_provider
    )

    cfg = run_prompt_instance.run_prompt_config or {}
    voice_id = cfg.get("voice_id") or cfg.get("voice") or "avery"
    sample_rate = int(cfg.get("sample_rate", 24000))
    language = cfg.get("language", "en")
    speed = cfg.get("speed", 1.0)
    add_wav_header = cfg.get("add_wav_header", True)

    # Both lightning_v3.1 and lightning_v3.1_pro share the same REST endpoint.
    endpoint = f"{_BASE_URL}/{_TTS_ENDPOINT_SLUG}/get_speech"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "text": input_text,
        "voice_id": voice_id,
        "sample_rate": sample_rate,
        "speed": speed,
        "add_wav_header": add_wav_header,
        "language": language,
    }

    logger.info(
        "smallest_ai_tts_request",
        model=model_id,
        voice_id=voice_id,
        language=language,
        input_length=len(input_text),
        sample_rate=sample_rate,
    )

    response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
    response.raise_for_status()

    audio_bytes = response.content
    return run_prompt_instance._format_audio_output(audio_bytes, start_time, input_text)


def smallest_ai_transcription_response(run_prompt_instance, start_time, api_key):
    """Speech-to-Text via Smallest AI Pulse REST API (pulse-pro, non-streaming).

    Uses pulse-pro which supports HTTP POST. The streaming `pulse` model
    requires a WebSocket connection and is not used here.
    """
    raw_input = run_prompt_instance._get_input_audio_from_messages()
    audio_bytes = audio_bytes_from_url_or_base64(raw_input)

    cfg = run_prompt_instance.run_prompt_config or {}
    language = cfg.get("language") or cfg.get("language_code") or None
    word_timestamps = cfg.get("word_timestamps", False)
    diarize = cfg.get("diarize", False)

    endpoint = f"{_BASE_URL}/stt/"
    params = {"model": "pulse-pro"}

    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    files = {"file": ("audio.wav", BytesIO(audio_bytes), "audio/wav")}
    data = {}
    if language:
        data["language"] = language
    if word_timestamps:
        data["word_timestamps"] = "true"
    if diarize:
        data["diarize"] = "true"

    logger.info(
        "smallest_ai_stt_request",
        audio_size_bytes=len(audio_bytes),
        language=language,
        word_timestamps=word_timestamps,
        diarize=diarize,
    )

    response = requests.post(
        endpoint, headers=headers, params=params, files=files, data=data, timeout=60
    )
    response.raise_for_status()

    result = response.json()
    transcript_text = (
        result.get("text")
        or result.get("transcript")
        or result.get("transcription")
        or str(result)
    )

    end_time = time.time()
    response_time_ms = (end_time - start_time) * 1000
    duration_seconds = get_audio_duration(audio_bytes)

    metadata = {
        "usage": {"audio_seconds": duration_seconds},
        "response_time": response_time_ms,
        "language": result.get("language"),
        "words": result.get("words"),
    }

    value_info = {
        "name": None,
        "data": {"response": transcript_text},
        "failure": None,
        "runtime": response_time_ms,
        "model": run_prompt_instance.model,
        "metrics": [],
        "metadata": metadata,
        "output": None,
    }

    return transcript_text, value_info


def get_smallest_ai_tts_parameters(model_name: str) -> dict:
    """UI parameters for Smallest AI Waves TTS models.

    lightning_v3.1     — 217 voices, 15+ languages including Indian languages
    lightning_v3.1_pro — 36 curated voices, en/hi/auto
    """
    is_pro = "pro" in model_name.lower()
    voices = LIGHTNING_V3_1_PRO_VOICES if is_pro else LIGHTNING_V3_1_VOICES

    # Pro only supports en, hi, auto; base supports full language set
    language_options = (
        ["en", "hi", "auto"]
        if is_pro
        else ["en", "hi", "auto", "ta", "te", "mr", "kn", "ml", "gu", "bn", "es", "fr", "de", "ja"]
    )

    return {
        "dropdowns": [
            {
                "label": "voice_id",
                "options": voices,
                "default": voices[0],
                "description": "Voice to use for speech synthesis. Full catalog: GET /waves/v1/lightning-v3.1/get_voices",
            },
            {
                "label": "language",
                "options": language_options,
                "default": "en",
                "description": "Synthesis language. Use 'auto' for automatic Hindi/English switching (Indian voices).",
            },
            {
                "label": "sample_rate",
                "options": [8000, 16000, 24000, 44100],
                "default": 24000,
                "description": "Output audio sample rate in Hz",
            },
        ],
        "sliders": [
            {
                "label": "speed",
                "min": 0.5,
                "max": 2.0,
                "step": 0.1,
                "default": 1.0,
                "description": "Speaking speed multiplier (1.0 = normal)",
            },
        ],
        "boolean": [
            {
                "label": "add_wav_header",
                "default": True,
                "description": "Prepend WAV header to response audio bytes",
            },
        ],
    }


def get_smallest_ai_stt_parameters(model_name: str) -> dict:
    """UI parameters for Smallest AI Pulse STT model (pulse-pro, REST)."""
    return {
        "dropdowns": [
            {
                "label": "language",
                "options": [
                    "en", "hi", "ta", "te", "mr", "kn", "ml", "gu", "bn",
                    "es", "fr", "de", "ja", "ko", "zh", "ar", "pt", "ru",
                ],
                "default": "en",
                "description": "Audio language. Leave as 'en' or set for better accuracy. Pulse supports 38 languages.",
            },
        ],
        "booleans": [
            {"label": "word_timestamps", "default": False},
            {"label": "diarize", "default": False},
        ],
    }
