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
- REST endpoint: POST https://api.smallest.ai/waves/v1/stt/
  (unified endpoint; supersedes the legacy POST /waves/v1/pulse/get_text)
- WebSocket streaming: wss://api.smallest.ai/waves/v1/stt/live
- Models: pulse (multilingual, supports both WebSocket streaming and REST/batch),
  pulse-pro (English-only, REST/batch only)  — passed as ?model= query param
- Auth: Authorization: Bearer {api_key}
- Body: raw audio bytes, Content-Type: application/octet-stream
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
_STT_ENDPOINT = f"{_BASE_URL}/stt/"

# Voice IDs per docs.smallest.ai model cards (lightning-v-3-1 / lightning-v-3-1-pro).
# The model cards document "featured/best" voices per language and accent, not a
# literal row-by-row dump of the full catalog (217 voices for lightning_v3.1,
# 149 for lightning_v3.1_pro). For the complete, exact catalog, call the live
# GET /waves/v1/{model}/get_voices endpoint.
LIGHTNING_V3_1_VOICES = [
    # English (US) — best voices
    "quinn", "mia", "magnus", "olivia", "daniel", "rachel", "nicole", "elizabeth",
    # English (Canadian / Australian)
    "william", "erica", "chloe",
    # Hindi / English (Indian accent) — best voices
    "neel", "maithili", "devansh", "sameera", "mihir", "aarush", "sakshi", "vivaan", "srishti",
    # Spanish — best voices
    "daniella", "camilla", "alba", "marcos", "david", "nerea", "miguel",
    # Other Indian languages (Tamil, Malayalam, Telugu, Marathi, Gujarati, Kannada)
    "jeevan", "rajeshwari", "vaisakh", "shibi", "srihari", "padmaja",
    "rupali", "nilesh", "niharika", "dhruvit", "deepashri", "pranav",
    # Additional curated — American English
    "jordan", "robert", "johnny", "lucas", "ronald", "blofeld", "zorin", "felix", "malcolm",
    "lauren", "hannah", "vanessa", "brooke", "ilsa", "christine",
    # Additional curated — Indic
    "wasim", "rehan", "parth", "atharv",
    "sunidhi", "chinmayi", "aanya", "siya", "anuja", "avni", "ishani", "yuvika", "advika", "sana", "maya",
]

LIGHTNING_V3_1_PRO_VOICES = [
    # Indian accent
    "rhea", "zariya", "kareena", "mishka", "inaaya", "saira", "meher", "aarini",
    "aviraj", "vyom", "zoravar", "reyansh", "ahan",
    # British accent
    "sophie", "ellie", "cressida", "ottilie", "elowen", "seraphina",
    "sam", "henry", "benedict", "cormac", "rupert", "finley",
    # American accent
    "kaitlyn", "savannah", "amelia", "zoe", "ruby", "leah", "jenna", "kate", "molly", "sara", "fiona",
    "blake", "austin", "jack", "leo", "luke", "owen",
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
    sample_rate = int(cfg.get("sample_rate", 44100))
    language = cfg.get("language", "en")
    speed = cfg.get("speed", 1.0)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "audio/wav",
        "X-Source": "future-agi",
    }

    payload = {
        "text": input_text,
        "voice_id": voice_id,
        "model": model_id,
        "sample_rate": sample_rate,
        "speed": speed,
        "language": language,
        "output_format": "wav",
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
    # zeroed-out body. Streaming bypasses the cache and delivers real audio frames.
    response = requests.post(
        _TTS_ENDPOINT, json=payload, headers=headers, timeout=30, stream=True
    )
    response.raise_for_status()

    raw_bytes = b"".join(response.iter_content(chunk_size=4096))

    # output_format="wav" should return an already-wrapped RIFF/WAV container,
    # but earlier API behavior returned raw PCM regardless of the requested
    # format — fall back to wrapping it ourselves if no RIFF header is present.
    audio_bytes = _ensure_wav_container(raw_bytes, sample_rate=sample_rate)

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

    Uses the unified POST /waves/v1/stt/ endpoint, sending audio as raw bytes
    with Content-Type: application/octet-stream.
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
        "Content-Type": "application/octet-stream",
        "X-Source": "future-agi",
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

    lightning_v3.1     — 217 voices, 12 languages
    lightning_v3.1_pro — 149 voices, 29 languages
    """
    is_pro = "pro" in model_name.lower()
    voices = LIGHTNING_V3_1_PRO_VOICES if is_pro else LIGHTNING_V3_1_VOICES

    language_options = (
        [
            "en", "hi", "mr", "ta", "ml", "te", "kn", "pa", "bn", "or", "gu",
            "ar", "zh", "id", "ja", "ko", "ms", "tr", "vi", "de", "es", "fr",
            "it", "pt", "ru", "el", "fi", "no", "pl",
        ]
        if is_pro
        else ["en", "hi", "ta", "es", "kn", "mr", "te", "or", "pa", "ml", "gu", "bn"]
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
                "description": "Synthesis language. 'hi' supports automatic Hindi/English code-switching.",
            },
            {
                "label": "sample_rate",
                "options": [8000, 16000, 24000, 44100],
                "default": 44100,
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
    }


def get_smallest_ai_stt_parameters(model_name: str) -> dict:
    """UI parameters for Smallest AI Pulse STT models (pulse, pulse-pro) via REST."""
    is_pro = "pro" in model_name.lower()

    # pulse-pro is English-only. pulse supports 26 individual language codes
    # plus 3 regional auto-detect aggregators (multi-eu, multi-asian, multi-indic).
    language_options = (
        ["en"]
        if is_pro
        else [
            "en", "hi", "de", "es", "ru", "it", "fr", "nl", "pt", "uk", "pl",
            "cs", "sk", "lv", "et", "ro", "fi", "sv", "bg", "hu", "da", "lt",
            "mt", "zh", "ja", "ko",
            "multi-eu", "multi-asian", "multi-indic",
        ]
    )

    return {
        "dropdowns": [
            {
                "label": "language",
                "options": language_options,
                "default": "en",
                "description": (
                    "Audio language. pulse-pro is English-only; pulse supports "
                    "26 languages plus multi-eu/multi-asian/multi-indic auto-detect."
                ),
            },
        ],
        "booleans": [
            {"label": "word_timestamps", "default": False},
            {"label": "diarize", "default": False},
        ],
    }
