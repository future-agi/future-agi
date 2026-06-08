"""
Smallest AI Integration — Waves TTS and Pulse STT

Source: https://docs.smallest.ai

TTS (Waves):
- REST endpoint: POST https://api.smallest.ai/waves/v1/tts
- WebSocket streaming: wss://api.smallest.ai/waves/v1/tts/live
- Models: lightning_v3.1, lightning_v3.1_pro  (passed in request body)
- Auth: Authorization: Bearer {api_key}
- Body: {"text", "voice_id", "model", "sample_rate", "speed", "language"}

STT (Pulse):
- REST endpoint: POST https://api.smallest.ai/waves/v1/pulse/get_text
- Models: pulse  (passed as ?model= query param)
- Auth: Authorization: Bearer {api_key}
- Body: raw audio bytes, Content-Type: audio/wav
"""

import time
import wave
from io import BytesIO

import requests
import structlog

from tfc.utils.storage import audio_bytes_from_url_or_base64, get_audio_duration

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.smallest.ai/waves/v1"
_TTS_ENDPOINT = f"{_BASE_URL}/tts"
_STT_ENDPOINT = f"{_BASE_URL}/pulse/get_text"

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

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "text": input_text,
        "voice_id": voice_id,
        "model": model_id,
        "sample_rate": sample_rate,
        "speed": speed,
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

    # stream=True is required — without it the AWS ALB layer can return a cached
    # zeroed-out body. Streaming bypasses the cache and delivers real PCM frames.
    response = requests.post(
        _TTS_ENDPOINT, json=payload, headers=headers, timeout=30, stream=True
    )
    response.raise_for_status()

    pcm_bytes = b"".join(response.iter_content(chunk_size=4096))

    # The API returns raw 16-bit PCM regardless of add_wav_header=True.
    # Wrap in a proper RIFF/WAV container so downstream consumers (STT, players)
    # can handle it correctly.
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)       # 16-bit
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes)
    audio_bytes = buf.getvalue()

    return run_prompt_instance._format_audio_output(audio_bytes, start_time, input_text)


def _ensure_wav_container(audio_bytes: bytes, sample_rate: int = 24000) -> bytes:
    """Wrap raw PCM bytes in a RIFF/WAV container if no header is present."""
    if audio_bytes[:4] == b"RIFF":
        return audio_bytes
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(audio_bytes)
    return buf.getvalue()


def smallest_ai_transcription_response(run_prompt_instance, start_time, api_key):
    """Speech-to-Text via Smallest AI Pulse REST API.

    Sends audio as raw bytes with Content-Type: audio/wav.
    Multipart form upload returns only {"status":"success"} without a transcript.
    """
    raw_input = run_prompt_instance._get_input_audio_from_messages()
    audio_bytes = audio_bytes_from_url_or_base64(raw_input)

    model_name_with_provider = run_prompt_instance.model
    model_id = (
        model_name_with_provider.split("/")[-1]
        if "/" in model_name_with_provider
        else model_name_with_provider
    )

    cfg = run_prompt_instance.run_prompt_config or {}
    language = cfg.get("language") or cfg.get("language_code") or "en"
    word_timestamps = cfg.get("word_timestamps", False)
    diarize = cfg.get("diarize", False)

    params = {"model": model_id, "language": language}
    if word_timestamps:
        params["word_timestamps"] = "true"
    if diarize:
        params["diarize"] = "true"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "audio/wav",
    }

    wav_bytes = _ensure_wav_container(audio_bytes)

    logger.info(
        "smallest_ai_stt_request",
        audio_size_bytes=len(wav_bytes),
        language=language,
        word_timestamps=word_timestamps,
        diarize=diarize,
    )

    response = requests.post(
        _STT_ENDPOINT, headers=headers, params=params, data=wav_bytes, timeout=60
    )
    response.raise_for_status()

    result = response.json()
    transcript_text = (
        result.get("transcription")
        or result.get("text")
        or result.get("transcript")
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
