"""
Audio Metrics Extractor

Provider-independent helper that measures two acoustic properties of a
single-speaker audio track, to complement the timing/transcript metrics in
``conversation_metrics``:

- ``average_pitch_hz``: arithmetic mean of the voiced fundamental-frequency
  (F0) frames, estimated with ``librosa.pyin``.
- ``estimated_snr_db``: an *estimate* of speech-to-background-noise power,
  derived from frame energies. Without a clean reference recording it cannot
  be ground truth, so treat it as a relative quality signal.

The module is a pure function of its input: no network, no database, no model
loading, and no randomness, so identical bytes always give identical metrics.
Nothing here is wired into call ingestion.

SNR estimation
--------------
Frame powers are computed on 25 ms windows with a 10 ms hop. The noise floor
is taken from the quietest frames (10th percentile of frame power); every
frame more than ``_ACTIVITY_MARGIN_DB`` above that floor counts as speech
activity. SNR is the mean power of the active frames, minus the noise power,
over the mean power of the remaining frames. This needs both speech and
pauses in the track. A stationary signal with no quiet frames (a pure tone,
constant noise) has no separable noise floor, so its SNR is ``None``.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np

# Pitch search range. C2-C6 covers adult speech F0 with headroom, and keeping
# fmax modest limits octave-up errors on speech.
_PITCH_FMIN_HZ = 65.406  # C2
_PITCH_FMAX_HZ = 1046.502  # C6
_PITCH_MIN_FRAME_LENGTH = 2048  # librosa.pyin default

# Frame-energy SNR parameters.
_FRAME_SECONDS = 0.025
_HOP_SECONDS = 0.010
_NOISE_FLOOR_PERCENTILE = 10.0
_ACTIVITY_MARGIN_DB = 6.0

# A track whose loudest frame is below this mean-square power (RMS 1e-4, about
# -80 dBFS) is treated as silence.
_SILENCE_POWER = 1e-8
# Guards log10 against digitally silent (all-zero) noise frames.
_POWER_EPSILON = 1e-12
_MAX_SNR_DB = 100.0


@dataclass(frozen=True)
class AudioMetrics:
    """Acoustic measurements for one audio track.

    Either field is ``None`` when it cannot be measured, e.g. no voiced
    frames for pitch, or no separable noise floor for SNR.
    """

    average_pitch_hz: float | None
    estimated_snr_db: float | None


def analyze_audio(audio_bytes: bytes) -> AudioMetrics:
    """Measure average pitch and estimated SNR of an encoded audio track.

    Args:
        audio_bytes: A complete audio file (WAV, FLAC, OGG, ...) as bytes.
            Multi-channel audio is downmixed to mono.

    Returns:
        ``AudioMetrics``. Both fields are ``None`` for silent or empty
        waveforms. All returned numbers are finite.

    Raises:
        ValueError: If ``audio_bytes`` is empty or cannot be decoded, which
            includes float audio holding NaN/inf samples (rejected by
            ``librosa.load``).
        TypeError: If ``audio_bytes`` is not a bytes-like object.
    """
    # librosa pulls in numba, which is slow to import. Import it on first use
    # so that importing this module stays cheap.
    import librosa

    if not isinstance(audio_bytes, (bytes, bytearray, memoryview)):
        raise TypeError(
            f"audio_bytes must be bytes-like, got {type(audio_bytes).__name__}"
        )
    if len(audio_bytes) == 0:
        raise ValueError("audio_bytes is empty")

    try:
        y, sr = librosa.load(io.BytesIO(audio_bytes), sr=None, mono=True)
    except Exception as exc:  # decoder errors vary by backend
        raise ValueError(f"Could not decode audio bytes: {exc}") from exc

    y = np.asarray(y, dtype=np.float64)
    if y.size == 0 or sr <= 0:
        return AudioMetrics(average_pitch_hz=None, estimated_snr_db=None)

    frame_power = _frame_power(y, sr)
    if frame_power.max() < _SILENCE_POWER:
        return AudioMetrics(average_pitch_hz=None, estimated_snr_db=None)

    return AudioMetrics(
        average_pitch_hz=_average_pitch_hz(librosa, y, sr),
        estimated_snr_db=_estimated_snr_db(frame_power),
    )


def _frame_power(y: np.ndarray, sr: int) -> np.ndarray:
    """Mean-square power per 25 ms frame (10 ms hop), without edge padding.

    Padding would add half-empty edge frames that look like pauses and pull
    the noise floor down, so only full frames are used. Audio shorter than
    one frame is treated as a single frame.
    """
    frame_length = max(1, round(_FRAME_SECONDS * sr))
    hop_length = max(1, round(_HOP_SECONDS * sr))
    if y.size <= frame_length:
        return np.array([float(np.mean(y**2))])

    # Cumulative sum of squares gives every frame's energy in O(n).
    cumulative = np.concatenate(([0.0], np.cumsum(y**2)))
    starts = np.arange(0, y.size - frame_length + 1, hop_length)
    return (cumulative[starts + frame_length] - cumulative[starts]) / frame_length


def _estimated_snr_db(frame_power: np.ndarray) -> float | None:
    """Energy-based SNR estimate in dB, or ``None`` if no noise floor exists."""
    noise_floor = max(
        float(np.percentile(frame_power, _NOISE_FLOOR_PERCENTILE)), _POWER_EPSILON
    )
    threshold = noise_floor * 10.0 ** (_ACTIVITY_MARGIN_DB / 10.0)
    active = frame_power > threshold
    if not active.any():
        return None

    noise_power = max(float(frame_power[~active].mean()), _POWER_EPSILON)
    speech_power = float(frame_power[active].mean()) - noise_power
    if speech_power <= 0.0:
        return None

    snr_db = 10.0 * math.log10(speech_power / noise_power)
    return float(min(max(snr_db, 0.0), _MAX_SNR_DB))


def _average_pitch_hz(librosa, y: np.ndarray, sr: int) -> float | None:
    """Mean of the voiced ``librosa.pyin`` F0 frames, or ``None`` if none."""
    fmax = min(_PITCH_FMAX_HZ, 0.45 * sr)
    if fmax <= _PITCH_FMIN_HZ:
        return None  # sample rate too low to resolve any pitch in range

    # pyin needs a window covering at least two periods of the lowest pitch;
    # grow the default frame for high sample rates.
    frame_length = max(
        _PITCH_MIN_FRAME_LENGTH,
        1 << math.ceil(math.log2(2.0 * sr / _PITCH_FMIN_HZ)),
    )
    f0, voiced_flag, _ = librosa.pyin(
        y.astype(np.float32),
        fmin=_PITCH_FMIN_HZ,
        fmax=fmax,
        sr=sr,
        frame_length=frame_length,
    )
    voiced = f0[voiced_flag & np.isfinite(f0)]
    if voiced.size == 0:
        return None
    return float(np.mean(voiced))
