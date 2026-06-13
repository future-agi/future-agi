"""
Deepgram API Integration - Text-to-Speech (Aura/Aura-2) and Speech-to-Text (Flux)

Updated: February 2026

Text-to-Speech (TTS):
- Model: Aura and Aura-2
- Endpoint: https://api.deepgram.com/v1/speak
- Voice format: {model}-{voice}-{language} (e.g., aura-2-thalia-en)
- Available voices:
  - Aura-2 English: 40+ voices (thalia, andromeda, helena, apollo, arcas, aries, luna, etc.)
  - Aura-2 Spanish: 17 voices (celeste, estrella, nestor, etc.) with multiple accents
  - Aura-2 Other: Dutch (9), French (2), German (7), Italian (10), Japanese (5)
  - Aura 1 English: 12 voices (asteria, luna, stella, athena, hera, orion, etc.)
- Encodings: linear16, mulaw, alaw, mp3, opus, flac, aac
- Sample rates: 8000, 16000, 24000, 32000, 48000 Hz
- Default: linear16, wav container, 24000 Hz

Speech-to-Text (STT):
- Model: flux-general-en
- Endpoint: wss://api.deepgram.com/v2/listen (WebSocket)
- Features: End-of-turn detection, interruption handling, Nova-3 accuracy
- Recommended: 16kHz linear16 PCM, 80ms chunks
- EOT parameters: eot_threshold (0.5-0.9), eot_timeout_ms (500-10000ms)
- EagerEOT: Optional faster response with eager_eot_threshold (0.3-0.9)
"""

import time

import requests
import structlog

logger = structlog.get_logger(__name__)
import io
import json
import ssl
import threading
from urllib.parse import urlencode

import websocket
from pydub import AudioSegment

from tfc.utils.http_timeouts import LLM_HTTP_TIMEOUT
from tfc.utils.storage import audio_bytes_from_url_or_base64


def deepgram_speech_response(run_prompt_instance, start_time, api_key):
    """Handles Text-to-Speech generation using the Deepgram API."""
    input_text = run_prompt_instance._get_input_text_from_messages()

    # Base model name from RunPrompt, e.g., "aura-2"
    base_model_id = run_prompt_instance.model

    # Get voice name from config. Default to a versatile voice like "thalia".
    # Support both voice and voice_id fields
    cfg = run_prompt_instance.run_prompt_config or {}
    voice_name = cfg.get("voice") or cfg.get("voice_id") or "thalia"

    # Construct the full model ID for the API, e.g., "aura-2-thalia-en"
    # For now, we only support English.
    full_model_id = f"{base_model_id}-{voice_name}-en"

    # Extract TTS parameters from config
    encoding = cfg.get("encoding")
    sample_rate = cfg.get("sample_rate")
    bit_rate = cfg.get("bit_rate")
    container = cfg.get("container")

    url = f"https://api.deepgram.com/v1/speak?model={full_model_id}"
    if encoding:
        url += f"&encoding={encoding}"
    if sample_rate:
        url += f"&sample_rate={sample_rate}"
    if bit_rate:
        url += f"&bit_rate={bit_rate}"
    if container:
        url += f"&container={container}"

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }

    payload = {"text": input_text}

    logger.info(
        f"Deepgram TTS request initiated - model: {full_model_id}, input_length: {len(input_text)}"
    )

    response = requests.post(
        url, headers=headers, json=payload, timeout=LLM_HTTP_TIMEOUT
    )

    if response.status_code == 200:
        audio_bytes = response.content
        return run_prompt_instance._format_audio_output(
            audio_bytes, start_time, input_text
        )
    else:
        error_message = f"Deepgram API error: {response.status_code} - {response.text}"
        logger.error(error_message)
        raise Exception(error_message)


def get_deepgram_tts_parameters(model_name: str):
    """Returns parameters for Deepgram TTS models (Aura and Aura-2)."""
    return {
        "dropdowns": [
            {
                "label": "encoding",
                "options": ["linear16", "mulaw", "alaw", "mp3", "opus", "flac", "aac"],
                "default": "linear16",
            },
            {
                "label": "sample_rate",
                "options": [8000, 16000, 24000, 32000, 48000],
                "default": 24000,
            },
            {
                "label": "container",
                "options": ["wav", "ogg", "none"],
                "default": "wav",
            },
        ],
        "text_inputs": [
            {
                "label": "bit_rate",
                "default": "",
                "description": "Bit rate in bps (e.g., 32000 for MP3, 4000-650000 for Opus). Leave empty for defaults.",
            },
        ],
    }


def get_deepgram_flux_parameters(model_name: str):
    """Returns parameters for the Deepgram Flux model (Speech-to-Text)."""
    return {
        "dropdowns": [
            {
                "label": "encoding",
                "options": [
                    "linear16",
                    "linear32",
                    "mulaw",
                    "alaw",
                    "opus",
                    "ogg-opus",
                ],
                "default": "linear16",
            },
            {
                "label": "sample_rate",
                "options": [8000, 16000, 24000, 44100, 48000],
                "default": 16000,
            },
        ],
        "sliders": [
            {
                "label": "eager_eot_threshold",
                "min": 0.3,
                "max": 0.9,
                "step": 0.05,
                "default": None,
                "description": "Confidence for EagerEndOfTurn events. Lower = faster response. None = disabled.",
            },
            {
                "label": "eot_threshold",
                "min": 0.5,
                "max": 0.9,
                "step": 0.05,
                "default": 0.7,
                "description": "Confidence required to trigger EndOfTurn. Lower = sooner turns.",
            },
            {
                "label": "eot_timeout_ms",
                "min": 500,
                "max": 10000,
                "step": 100,
                "default": 5000,
                "description": "Maximum silence (ms) before forcing EndOfTurn.",
            },
        ],
        "text_inputs": [
            {
                "label": "keyterm",
                "default": "",
                "description": "Boost recognition for specific terms.",
            },
        ],
        "booleans": [
            {
                "label": "mip_opt_out",
                "default": False,
                "description": "Opt out of Model Improvement Program.",
            },
        ],
    }


def deepgram_transcription_response(run_prompt_instance, start_time, api_key):
    """
    Handles Speech-to-Text using the Deepgram Flux API.

    Flux is a real-time conversational STT model that uses WebSockets.
    We always convert input audio to mono 16kHz linear16 PCM for consistency.
    """
    cfg = run_prompt_instance.run_prompt_config or {}

    # Load and convert audio to linear16 PCM
    audio_bytes = _load_and_convert_audio(run_prompt_instance)

    # Build WebSocket URL with parameters
    deepgram_url = _build_flux_websocket_url(cfg)

    # Connect and transcribe
    final_transcript = _transcribe_via_websocket(deepgram_url, audio_bytes, api_key)

    # Format response with cost calculation
    end_time = time.time()
    response_time_ms = (end_time - start_time) * 1000

    # Calculate cost based on audio duration
    # Deepgram Flux Pay As You Go: $0.0077/min
    audio_duration_seconds = len(audio_bytes) / (16000 * 2)  # 16kHz, 16-bit
    audio_duration_minutes = audio_duration_seconds / 60
    cost_per_minute = 0.0077
    total_cost = audio_duration_minutes * cost_per_minute

    value_info = {
        "data": {"response": final_transcript},
        "runtime": response_time_ms,
        "model": "flux-general-en",
        "metadata": {
            "usage": {
                "total_cost": round(total_cost, 6),
                "audio_duration_seconds": round(audio_duration_seconds, 2),
                "cost_per_minute": cost_per_minute,
            },
            "cost": {
                "total_cost": round(total_cost, 6),
            },
        },
    }

    return final_transcript, value_info


def _load_and_convert_audio(run_prompt_instance):
    """
    Loads audio from messages and converts to mono 16kHz linear16 PCM.

    Deepgram Flux works best with this format, and converting ensures
    compatibility regardless of input format (MP3, WAV, OGG, etc.).
    """
    raw_audio = run_prompt_instance._get_input_audio_from_messages()

    try:
        audio_bytes = audio_bytes_from_url_or_base64(raw_audio)
        logger.info(f"Audio loaded: {len(audio_bytes)} bytes")
    except Exception as e:
        logger.error(f"Failed to load audio: {e}")
        audio_bytes = raw_audio if isinstance(raw_audio, bytes) else raw_audio.encode()

    logger.info("Converting audio to linear16 PCM")
    try:
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes))

        # Convert to mono, 16kHz, 16-bit PCM
        audio = audio.set_channels(1)
        audio = audio.set_frame_rate(16000)
        audio = audio.set_sample_width(2)

        audio_bytes = audio.raw_data
        duration_seconds = len(audio_bytes) / (16000 * 2)

        logger.info(
            f"Converted to PCM: {len(audio_bytes)} bytes ({duration_seconds:.2f}s)"
        )

        if duration_seconds < 0.5:
            logger.warning(f"Audio is very short ({duration_seconds:.2f}s)")

        return audio_bytes

    except Exception as e:
        logger.error(f"Audio conversion failed: {e}")
        raise Exception(f"Failed to convert audio for Deepgram Flux: {e}")


def _build_flux_websocket_url(cfg):
    """Builds the Deepgram Flux WebSocket URL with query parameters."""
    params = {
        "model": "flux-general-en",
        "encoding": "linear16",
        "sample_rate": 16000,
    }

    # Add optional parameters if provided
    if cfg.get("eager_eot_threshold"):
        params["eager_eot_threshold"] = cfg.get("eager_eot_threshold")
    if cfg.get("eot_threshold"):
        params["eot_threshold"] = cfg.get("eot_threshold")
    if cfg.get("eot_timeout_ms"):
        params["eot_timeout_ms"] = int(cfg.get("eot_timeout_ms"))
    if cfg.get("keyterm"):
        params["keyterm"] = cfg.get("keyterm")
    if cfg.get("mip_opt_out"):
        params["mip_opt_out"] = cfg.get("mip_opt_out")

    query_string = urlencode(params)
    return f"wss://api.deepgram.com/v2/listen?{query_string}"


def _transcribe_via_websocket(deepgram_url, audio_bytes, api_key):
    """
    Connects to Deepgram Flux via WebSocket and transcribes audio.

    Uses threading to send audio chunks concurrently while listening
    for transcript updates from the server.
    """
    try:
        ws = websocket.create_connection(
            deepgram_url,
            header=[f"Authorization: Token {api_key}"],
            sslopt={"cert_reqs": ssl.CERT_NONE},
        )
        logger.info("Connected to Deepgram Flux")

        # Start audio sender thread
        sender_thread = threading.Thread(
            target=_send_audio_chunks, args=(ws, audio_bytes)
        )
        sender_thread.start()

        # Listen for transcripts on main thread
        final_transcript_parts = _receive_transcripts(ws, sender_thread)

        # Cleanup
        sender_thread.join()
        ws.close()

        # Assemble final transcript
        sorted_parts = [
            final_transcript_parts[i] for i in sorted(final_transcript_parts.keys())
        ]
        return " ".join(sorted_parts).strip()

    except Exception as e:
        logger.error(f"Deepgram Flux error: {e}")
        raise


def _send_audio_chunks(ws, audio_bytes):
    """Sends audio data in 80ms chunks (recommended by Deepgram)."""
    chunk_size = 2560  # 80ms at 16kHz, 16-bit
    total_chunks = (len(audio_bytes) + chunk_size - 1) // chunk_size

    logger.info(f"Sending {len(audio_bytes)} bytes in {total_chunks} chunks")

    for i in range(0, len(audio_bytes), chunk_size):
        ws.send(audio_bytes[i : i + chunk_size], opcode=websocket.ABNF.OPCODE_BINARY)

    ws.send(json.dumps({"type": "CloseStream"}))
    logger.info("Finished sending audio")


def _receive_transcripts(ws, sender_thread):
    """
    Receives and processes transcript messages from Deepgram.

    Returns a dict mapping turn_index to transcript text.
    """
    final_transcript_parts = {}
    logger.info("Listening for transcripts...")

    while sender_thread.is_alive() or ws.connected:
        try:
            msg = ws.recv()
            if not msg:
                break

            res = json.loads(msg)
            msg_type = res.get("type")

            if msg_type == "TurnInfo":
                transcript = res.get("transcript", "")
                turn_index = res.get("turn_index")
                if transcript:
                    final_transcript_parts[turn_index] = transcript

            elif msg_type == "FatalError":
                error_msg = res.get("description", "Unknown error")
                logger.error(f"Deepgram error: {error_msg}")
                raise Exception(f"Deepgram error: {error_msg}")

        except websocket.WebSocketConnectionClosedException:
            logger.info("Connection closed by server")
            break
        except Exception as e:
            logger.error(f"Error receiving message: {e}")
            break

    return final_transcript_parts
