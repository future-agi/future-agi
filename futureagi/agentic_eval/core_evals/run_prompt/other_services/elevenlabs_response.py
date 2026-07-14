import time

import structlog


# `elevenlabs` lives in the `voice` extra (OSS-light skips it). Import is
# lazy — the client factory raises a clear ImportError at call time instead
# of breaking module load for OSS boots that never touch TTS/STT.
def _elevenlabs_client(api_key):
    try:
        from elevenlabs.client import ElevenLabs
    except ImportError as e:
        raise ImportError(
            "ElevenLabs TTS/STT requires the `voice` extra. "
            "Install with: pip install 'core-backend[voice]'"
        ) from e
    return ElevenLabs(api_key=api_key)

logger = structlog.get_logger(__name__)
from tfc.utils.storage import (
    get_audio_duration,
    audio_bytes_from_url_or_base64,
)
from io import BytesIO

# ElevenLabs premade voices (updated Feb 2026)
# Validated against ElevenLabs API - only includes voices that exist
# Legacy voices still work but may be deprecated
VOICE_ID_MAP = {
    # Current valid premade voices (20 voices)
    "Adam": "pNInz6obpgDQGcFmaJgB",
    "Alice": "Xb7hH8MSUJpSbSDYk0k2",
    "Bill": "pqHfZKP75CvOlQylNhV4",
    "Brian": "nPczCjzI2devNBz1zQrb",
    "Callum": "N2lVS1w4EtoT3dr4eOWO",
    "Charlie": "IKne3meq5aSn9XLyUdCD",
    "Chris": "iP95p4xoKVk53GoZ742B",
    "Daniel": "onwK4e9ZLuTAKqWW03F9",
    "Eric": "cjVigY5qzO86Huf0OWal",
    "George": "JBFqnCBsd6RMkjVDRZzb",
    "Harry": "SOYHLrjzK2X1ezoPC6cr",
    "Jessica": "cgSgspJ2msm6clMCkdW9",
    "Laura": "FGY2WhTYpPnrIDTdsKH5",
    "Liam": "TX3LPaxmHKxFdv7VOQHJ",
    "Lily": "pFZP5JQG7iQjIQuC4Bku",
    "Matilda": "XrExE9yKIg1WjnnlVkGX",
    "River": "SAz9YHcvj6GT2YYXdXww",
    "Roger": "CwhRBWXzGAHq8TQ4Fs17",
    "Sarah": "EXAVITQu4vr4xnSDxMaL",
    "Will": "bIHbv24MWmeRgasZH58o",
    # Additional voices from API (not in original list)
    "Bella": "hpp4J3VqNfWAUOO0d1Us",
    # Legacy voices (still work but may be deprecated)
    "Antoni": "ErXwobaYiN019PkySvjV",
    "Arnold": "VR6AewLTigWG4xSOukaG",
    "Aria": "9BWtsMINqrJLrRacOk9x",
    "Charlotte": "XB0fDUnXU5powFXDhCwa",
    "Clyde": "2EiwWnXFnvU5JabPnv8n",
    "Dave": "CYw3kZ02Hs0563khs1Fj",
    "Domi": "AZnzlk1XvdvUeBnXmlld",
    "Dorothy": "ThT5KcBeYPX3keUQqHPh",
    "Drew": "29vD33N1CtxCmqQRPOHJ",
    "Emily": "LcfcDJNUP1GQjkzn1xUU",
    "Ethan": "g5CIjZEefAph4nQFvHAz",
    "Fin": "D38z5RcWu1voky8WS1ja",
    "Freya": "jsCqWAovK2LkecY7zXl4",
    "Gigi": "jBpfuIE2acCO8z3wKNLl",
    "Giovanni": "zcAOhNBS3c14rBihAFp1",
    "Glinda": "z9fAnlkpzviPz146aGWa",
    "Grace": "oWAxZDx7w5VEj9dCyTzz",
    "James": "ZQe5CZNOzWyzPSCn5a3c",
    "Jeremy": "bVMeCyTHy58xNoL34h3p",
    "Jessie": "t0jbNlBVZ17f02VDIeMI",
    "Joseph": "Zlb1dXrM653N07WRdFW3",
    "Josh": "TxGEqnHWrfWFTfGW9XjX",
    "Michael": "flq6f7yk4E4fJM5XTYuZ",
    "Mimi": "zrHiDhphv9ZnVXBqCLjz",
    "Nicole": "piTKgcLEGmPE4e6mEKli",
    "Patrick": "ODq5zmih8GrVes37Dizd",
    "Paul": "5Q0t7uMcjvnagumLfvZi",
    "Rachel": "21m00Tcm4TlvDq8ikWAM",
    "Sam": "yoZ06aMxZJJ28mfd3POQ",
    "Serena": "pMsXgVXv3BLzUgSXRplE",
    "Thomas": "GBv7mTt0atIp3Br8iCZE",
}


def elevenlabs_speech_response(run_prompt_instance, start_time, api_key):
    """Handles Text-to-Speech generation using the ElevenLabs SDK."""
    input_text = run_prompt_instance._get_input_text_from_messages()

    client = _elevenlabs_client(api_key)

    # The model name from RunPrompt includes the provider, e.g., "elevenlabs/eleven_v3".
    # We need to strip the "elevenlabs/" prefix for the SDK.
    model_name_with_provider = run_prompt_instance.model
    model_id = (
        model_name_with_provider.split("/")[-1]
        if "/" in model_name_with_provider
        else model_name_with_provider
    )

    # Get voice name/id from config
    # Frontend sends: { "voice": "My Custom Voice", "voice_id": "resolved-provider-id" }
    # or { "voice": "Clyde" } for system voices (no voice_id)
    cfg = run_prompt_instance.run_prompt_config or {}

    # First, try to map voice name to ID (for system voices)
    voice_val = cfg.get("voice")
    voice_id_raw = cfg.get("voice_id")  # Could be a voice name OR a voice_id

    # Debug log the incoming config
    logger.info(
        f"ElevenLabs TTS voice config: voice={voice_val}, voice_id={voice_id_raw}"
    )

    # Determine the final voice_id
    # Priority: 1) voice name mapped to ID, 2) voice_id field (check if it's a name or UUID)
    voice_id = None

    if voice_val:
        # Try exact match first, then case-insensitive
        voice_id = VOICE_ID_MAP.get(voice_val)
        if not voice_id:
            voice_id = VOICE_ID_MAP.get(voice_val.title())
        if not voice_id:
            voice_id = VOICE_ID_MAP.get(voice_val.lower())
        if not voice_id:
            for name, vid in VOICE_ID_MAP.items():
                if name.lower() == voice_val.lower():
                    voice_id = vid
                    break

    # If voice name mapping didn't work, check voice_id field
    # It might be a voice name (not UUID) passed via voice_id instead of voice
    if not voice_id and voice_id_raw:
        # Check if voice_id_raw is actually a voice name we can map
        voice_id = VOICE_ID_MAP.get(voice_id_raw)
        if not voice_id:
            voice_id = VOICE_ID_MAP.get(voice_id_raw.title())
        if not voice_id:
            voice_id = VOICE_ID_MAP.get(voice_id_raw.lower())
        if not voice_id:
            # Check if it's already a valid UUID (skip mapping)
            if voice_id_raw and len(voice_id_raw) > 20:
                voice_id = voice_id_raw
            else:
                # Last resort: search for case-insensitive match
                for name, vid in VOICE_ID_MAP.items():
                    if name.lower() == voice_id_raw.lower():
                        voice_id = vid
                        break

    # Final fallback to default voice (George)
    if not voice_id:
        voice_id = VOICE_ID_MAP.get("George")

    logger.info(f"ElevenLabs TTS resolved voice_id: {voice_id}")

    # Extract TTS parameters from config
    stability = cfg.get("stability")
    similarity_boost = cfg.get("similarity_boost")
    style = cfg.get("style")
    use_speaker_boost = cfg.get("use_speaker_boost")
    output_format = cfg.get("output_format")

    # If the format is the generic 'audio', use a specific default for ElevenLabs.
    # The API requires formats like 'mp3_44100_128'.
    if not output_format or output_format == "audio":
        output_format = "mp3_44100_128"

    logger.info(
        f"ElevenLabs TTS request initiated - model: {model_id}, voice_id: {voice_id}, input_length: {len(input_text)}"
    )

    tts_kwargs = {
        "text": input_text,
        "voice_id": voice_id,
        "model_id": model_id,
    }
    if output_format:
        tts_kwargs["output_format"] = output_format

    # Nest voice settings parameters
    voice_settings = {}
    if stability is not None:
        voice_settings["stability"] = stability
    if similarity_boost is not None:
        voice_settings["similarity_boost"] = similarity_boost
    if style is not None:
        voice_settings["style"] = style
    if use_speaker_boost is not None:
        voice_settings["use_speaker_boost"] = use_speaker_boost

    if voice_settings:
        tts_kwargs["voice_settings"] = voice_settings

    audio_stream = client.text_to_speech.convert(**tts_kwargs)

    audio_bytes = b"".join(list(audio_stream))

    return run_prompt_instance._format_audio_output(audio_bytes, start_time, input_text)


def get_elevenlabs_tts_parameters(model_name: str):
    """Returns parameters for ElevenLabs TTS models (eleven_v3, eleven_turbo_v2_5, etc.)."""
    return {
        "sliders": [
            {
                "label": "stability",
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "default": 0.5,
                "description": "Controls voice consistency. Lower (0.30-0.50) = more emotional/dynamic, Higher (0.60-0.85) = more stable/consistent",
            },
            {
                "label": "similarity_boost",
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "default": 0.75,
                "description": "How closely AI adheres to original voice (Clarity + Similarity Enhancement)",
            },
            {
                "label": "style",
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "default": 0.0,
                "description": "Amplifies style of original speaker (0 recommended for lower latency, uses more compute if > 0)",
            },
        ],
        "boolean": [
            {"label": "use_speaker_boost", "default": True},
        ],
        "dropdowns": [
            {
                "label": "output_format",
                "options": [
                    "mp3_22050_32",
                    "mp3_44100_32",
                    "mp3_44100_64",
                    "mp3_44100_96",
                    "mp3_44100_128",
                    "mp3_44100_192",
                    "pcm_16000",
                    "pcm_22050",
                    "pcm_24000",
                    "pcm_44100",
                    "ulaw_8000",
                ],
                "default": "mp3_44100_128",
                "description": "Audio output format (192kbps requires Creator+, PCM 44.1kHz requires Pro+)",
            }
        ],
    }


def get_elevenlabs_stt_parameters(model_name: str):
    """Returns parameters for ElevenLabs STT models (scribe_v1 and scribe_v2)."""
    # Check if model is scribe_v2 to enable advanced features
    is_scribe_v2 = "scribe_v2" in model_name.lower() if model_name else False

    params = {
        "booleans": [
            {"label": "diarize", "default": False},
            {"label": "tag_audio_events", "default": True},
        ],
        "sliders": [
            {
                "label": "num_speakers",
                "min": 1,
                "max": 32,
                "step": 1,
                "default": 2,
            },
            {
                "label": "diarization_threshold",
                "min": 0.1,
                "max": 0.4,
                "step": 0.05,
                "default": 0.22,
            },
            {
                "label": "temperature",
                "min": 0.0,
                "max": 2.0,
                "step": 0.1,
                "default": 0.0,
            },
            {
                "label": "seed",
                "min": 0,
                "max": 2147483647,
                "step": 1,
                "default": 0,
            },
        ],
        "dropdowns": [
            {
                "label": "timestamps_granularity",
                "options": ["none", "word", "character"],
                "default": "word",
            }
        ],
    }

    # Add scribe_v2-specific parameters (2026 features)
    if is_scribe_v2:
        params["text_arrays"] = [
            {
                "label": "keyterms",
                "default": [],
                "max_items": 100,
                "description": "Words/phrases to bias transcription toward (max 100)",
            }
        ]
        params["dropdowns"].append(
            {
                "label": "entity_detection",
                "options": [
                    "none",
                    "all",
                    "credit_card",
                    "ssn",
                    "medical",
                    "name",
                    "email",
                    "phone",
                    "address",
                ],
                "default": "none",
                "description": "Detect specific entity types in transcription",
            }
        )

    return params


def elevenlabs_transcription_response(run_prompt_instance, start_time, api_key):
    """Handles Speech-to-Text using the ElevenLabs SDK (scribe_v1 or scribe_v2)."""
    # Extract audio from messages
    raw_input = run_prompt_instance._get_input_audio_from_messages()

    # Normalize audio input using shared utility
    audio_bytes = audio_bytes_from_url_or_base64(raw_input)

    client = _elevenlabs_client(api_key)

    # Extract model_id from the model string (e.g., "elevenlabs/scribe_v2" -> "scribe_v2")
    model_name_with_provider = run_prompt_instance.model
    model_id = (
        model_name_with_provider.split("/")[-1]
        if "/" in model_name_with_provider
        else model_name_with_provider
    )
    # Default to scribe_v1 if no specific model specified
    if not model_id or model_id == "elevenlabs":
        model_id = "scribe_v1"

    cfg = run_prompt_instance.run_prompt_config or {}
    # Accept both 'language' and 'language_code'; None => auto-detect
    _raw_lc = cfg.get("language_code", cfg.get("language"))
    effective_language_code = (
        str(_raw_lc).strip()
        if _raw_lc is not None and str(_raw_lc).strip() != ""
        else None
    )

    diarization_threshold = cfg.get("diarization_threshold")
    diarize = bool(cfg.get("diarize", False)) or (
        diarization_threshold is not None and diarization_threshold > 0
    )

    tag_audio_events = bool(cfg.get("tag_audio_events", True))
    num_speakers = cfg.get("num_speakers")

    # If using diarization_threshold, num_speakers must be None
    if diarization_threshold is not None and diarization_threshold > 0:
        num_speakers = None

    temperature = cfg.get("temperature")
    seed = cfg.get("seed")
    timestamps_granularity = cfg.get("timestamps_granularity")

    # New scribe_v2 parameters (January 2026 features)
    keyterms = cfg.get("keyterms", [])
    entity_detection = cfg.get("entity_detection")

    # Prepare file-like object
    audio_file = BytesIO(audio_bytes)

    logger.info(
        f"ElevenLabs STT request - model: {model_id}, language_code: {effective_language_code}, "
        f"diarize: {diarize}, tag_audio_events: {tag_audio_events}, "
        f"keyterms: {len(keyterms) if keyterms else 0}, entity_detection: {entity_detection}"
    )

    stt_kwargs = {
        "file": audio_file,
        "model_id": model_id,
        "tag_audio_events": tag_audio_events,
        "diarize": diarize,
    }
    if effective_language_code:
        stt_kwargs["language_code"] = effective_language_code
    if num_speakers:
        stt_kwargs["num_speakers"] = num_speakers
    if diarize and diarization_threshold:
        stt_kwargs["diarization_threshold"] = diarization_threshold
    if temperature:
        stt_kwargs["temperature"] = temperature
    if seed:
        stt_kwargs["seed"] = seed
    if timestamps_granularity:
        stt_kwargs["timestamps_granularity"] = timestamps_granularity

    # Add scribe_v2-specific parameters (only if provided)
    if keyterms and isinstance(keyterms, list) and len(keyterms) > 0:
        # Limit to 100 keyterms as per API spec
        stt_kwargs["keyterms"] = keyterms[:100]
    if entity_detection and entity_detection != "none":
        stt_kwargs["entity_detection"] = entity_detection

    transcription = client.speech_to_text.convert(**stt_kwargs)

    # Calculate response time
    end_time = time.time()
    response_time_ms = (end_time - start_time) * 1000

    # Extract text robustly
    transcript_text = None
    try:
        if isinstance(transcription, dict):
            transcript_text = (
                transcription.get("text")
                or transcription.get("transcript")
                or str(transcription)
            )
        else:
            transcript_text = getattr(transcription, "text", None) or str(transcription)
    except Exception:
        transcript_text = str(transcription)

    # Get audio duration using shared utility
    duration_seconds = get_audio_duration(audio_bytes)

    metadata = {
        "usage": {
            "audio_seconds": duration_seconds,
        },
        "response_time": response_time_ms,
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


def validate_elevenlabs_voice(voice_id, api_key):
    """Validates if a voice ID exists in ElevenLabs."""
    try:
        client = _elevenlabs_client(api_key)
        client.voices.get(voice_id=voice_id)
        return True
    except Exception as e:
        if "404" in str(e):
            raise ValueError(f"Voice ID '{voice_id}' not found in ElevenLabs.")
        raise ValueError(f"ElevenLabs validation failed: {str(e)}")
