"""LiveKit call pricing — plug-and-play rate configuration.

All pricing rates live in ``PricingRates``.  To update pricing:
set the corresponding env var → all cost calculations update on next restart.

    LIVEKIT_STT_RATE_PER_SECOND   (default: 0.000128)
    LIVEKIT_TTS_RATE_PER_CHAR     (default: 0.000011)
    LIVEKIT_STORAGE_RATE_PER_MB   (default: 0.000023)

LLM pricing is handled by ``litellm.cost_per_token`` (auto-updated model registry).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Pricing rates — override via env vars, or change defaults here
# ---------------------------------------------------------------------------

# Defaults (used when env var is not set)
_DEFAULT_STT_RATE = "0.000128"  # $0.0077/min → per second (Deepgram Nova-3)
_DEFAULT_TTS_RATE = "0.000011"  # $0.011/1K chars → per char (Cartesia Sonic-2)
_DEFAULT_STORAGE_RATE = "0.000023"  # $0.023/GB → per MB (S3)


@dataclass(frozen=True)
class PricingRates:
    """All manually-configured pricing rates for LiveKit calls.

    LLM pricing is NOT here — it uses litellm's auto-updated model registry.
    Only STT, TTS, and storage rates need manual configuration.

    Rates are in **USD**.  Override via env vars:
      LIVEKIT_STT_RATE_PER_SECOND, LIVEKIT_TTS_RATE_PER_CHAR,
      LIVEKIT_STORAGE_RATE_PER_MB.
    """

    # STT — Deepgram (per second of audio)
    # Source: https://deepgram.com/pricing
    stt_cost_per_second: Decimal = Decimal(
        os.getenv("LIVEKIT_STT_RATE_PER_SECOND", _DEFAULT_STT_RATE)
    )

    # TTS — Cartesia (per character)
    # Source: https://cartesia.ai/pricing
    tts_cost_per_character: Decimal = Decimal(
        os.getenv("LIVEKIT_TTS_RATE_PER_CHAR", _DEFAULT_TTS_RATE)
    )

    # Storage — S3 recording (per MB stored)
    # Estimated MP3 bitrate: ~190 kbps VBR. 4 tracks per call.
    storage_cost_per_mb: Decimal = Decimal(
        os.getenv("LIVEKIT_STORAGE_RATE_PER_MB", _DEFAULT_STORAGE_RATE)
    )


# Active pricing instance — all cost functions use this by default.
RATES = PricingRates()

# Estimated MP3 bitrate for storage cost calculation (bits per second).
_MP3_BITRATE_BPS = 190_000
_NUM_RECORDING_TRACKS = 4


# ---------------------------------------------------------------------------
# Individual cost calculators
# ---------------------------------------------------------------------------


def calculate_stt_cost(
    duration_seconds: float,
    model: str = "",  # reserved for per-model rates (e.g. nova-2 vs nova-3)
    rates: PricingRates = RATES,
) -> Decimal:
    """STT cost = audio duration (seconds) × rate per second."""
    return Decimal(str(duration_seconds)) * rates.stt_cost_per_second


def calculate_llm_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model: str = "",
) -> Decimal:
    """LLM cost via litellm — auto-updated per-model pricing.

    Falls back to 0 (with warning) if litellm doesn't recognise the model.
    """
    from litellm import cost_per_token, model_cost

    if not model or (prompt_tokens == 0 and completion_tokens == 0):
        return Decimal("0")

    model_info = model_cost.get(model)
    if not model_info:
        logger.warning("livekit_pricing_unknown_llm_model", model=model)
        return Decimal("0")

    prompt_cost, completion_cost = cost_per_token(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    return Decimal(str(prompt_cost)) + Decimal(str(completion_cost))


def calculate_tts_cost(
    characters: int,
    model: str = "",  # reserved for per-model rates (e.g. sonic vs sonic-2)
    rates: PricingRates = RATES,
) -> Decimal:
    """TTS cost = character count × rate per character."""
    return Decimal(str(characters)) * rates.tts_cost_per_character


def calculate_storage_cost(
    duration_seconds: float,
    rates: PricingRates = RATES,
) -> Decimal:
    """Estimated S3 storage cost from call duration.

    Approximation: MP3 at ~190 kbps VBR × 4 tracks.
    """
    if duration_seconds <= 0:
        return Decimal("0")
    bytes_per_track = (duration_seconds * _MP3_BITRATE_BPS) / 8
    total_bytes = bytes_per_track * _NUM_RECORDING_TRACKS
    total_mb = Decimal(str(total_bytes)) / Decimal("1048576")  # 1024 * 1024
    return total_mb * rates.storage_cost_per_mb


# ---------------------------------------------------------------------------
# Aggregate calculator
# ---------------------------------------------------------------------------


def calculate_call_costs(
    usage: dict[str, Any],
    duration_seconds: float = 0.0,
    rates: PricingRates = RATES,
) -> dict[str, Decimal]:
    """Calculate all cost components from a usage dict.

    Args:
        usage: ``provider_call_data["livekit"]["usage"]`` dict containing
               ``stt``, ``llm``, ``tts``, and ``models`` sub-dicts.
        duration_seconds: Call duration for storage cost estimation.
            Falls back to ``usage["stt"]["duration"]`` if not provided.
        rates: Pricing rates to use (defaults to module-level ``RATES``).

    Returns:
        ``{"stt": ..., "llm": ..., "tts": ..., "storage": ..., "total": ...}``
        with all values as ``Decimal`` in USD.
    """
    models = usage.get("models", {})

    stt_cost = calculate_stt_cost(
        duration_seconds=usage.get("stt", {}).get("duration", 0.0),
        model=models.get("stt", ""),
        rates=rates,
    )
    llm_cost = calculate_llm_cost(
        prompt_tokens=usage.get("llm", {}).get("prompt_tokens", 0),
        completion_tokens=usage.get("llm", {}).get("completion_tokens", 0),
        model=models.get("llm", ""),
    )
    tts_cost = calculate_tts_cost(
        characters=usage.get("tts", {}).get("characters", 0),
        model=models.get("tts", ""),
        rates=rates,
    )

    # Storage cost: use explicit duration or fall back to STT audio duration
    storage_duration = duration_seconds or usage.get("stt", {}).get("duration", 0.0)
    storage_cost = calculate_storage_cost(
        duration_seconds=storage_duration,
        rates=rates,
    )

    total = stt_cost + llm_cost + tts_cost + storage_cost

    logger.debug(
        "livekit_cost_calculated",
        stt=float(stt_cost),
        llm=float(llm_cost),
        tts=float(tts_cost),
        storage=float(storage_cost),
        total=float(total),
        models=models,
    )

    return {
        "stt": stt_cost,
        "llm": llm_cost,
        "tts": tts_cost,
        "storage": storage_cost,
        "total": total,
    }
