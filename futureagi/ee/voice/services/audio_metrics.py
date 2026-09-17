"""Provider-independent acoustic measurements for a single voice track."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import librosa
import numpy as np

_FRAME_LENGTH = 2048
_HOP_LENGTH = 512
_ENERGY_QUANTILE = 0.2


@dataclass(frozen=True)
class AudioMetrics:
    """Acoustic measurements derived from one decoded audio track."""

    average_pitch_hz: float | None
    estimated_snr_db: float | None


def analyze_audio(audio_bytes: bytes) -> AudioMetrics:
    """Analyze a single-speaker track without external services or state.

    Pitch is the arithmetic mean of finite fundamental-frequency values from
    voiced ``librosa.pyin`` frames.  ``estimated_snr_db`` is a deterministic
    frame-energy estimate: it treats the mean power of the lowest 20 percent
    of 2048-sample, 512-hop frames as background-noise power and the mean
    power of the highest 20 percent as speech-plus-noise power.  The estimate
    is ``10 * log10((high_power - noise_power) / noise_power)``.  It assumes
    the quietest frames are noise-dominated and the loudest are
    speech-dominated; without a clean reference it is not ground-truth SNR.

    ``None`` is returned for a measurement when the waveform is silent or
    does not provide enough finite frame-energy contrast to estimate it.
    Malformed or undecodable input raises ``ValueError``.
    """
    if not isinstance(audio_bytes, bytes):
        raise ValueError("audio_bytes must be decodable audio bytes")

    try:
        waveform, sample_rate = librosa.load(
            io.BytesIO(audio_bytes), sr=None, mono=True
        )
    except Exception as exc:
        raise ValueError("Could not decode audio bytes") from exc

    waveform = np.asarray(waveform)
    if waveform.size == 0 or not np.any(np.isfinite(waveform)):
        return AudioMetrics(None, None)

    waveform = waveform[np.isfinite(waveform)]
    if waveform.size == 0 or np.max(np.abs(waveform)) <= np.finfo(float).eps:
        return AudioMetrics(None, None)

    average_pitch_hz = _average_pitch_hz(waveform, sample_rate)
    estimated_snr_db = _estimated_snr_db(waveform)
    return AudioMetrics(average_pitch_hz, estimated_snr_db)


def _average_pitch_hz(waveform: np.ndarray, sample_rate: int) -> float | None:
    if waveform.size < _FRAME_LENGTH:
        return None

    pitches, voiced, _ = librosa.pyin(
        waveform,
        fmin=librosa.note_to_hz("C2"),
        fmax=librosa.note_to_hz("C7"),
        sr=sample_rate,
        frame_length=_FRAME_LENGTH,
        hop_length=_HOP_LENGTH,
    )
    voiced_pitches = pitches[np.asarray(voiced, dtype=bool) & np.isfinite(pitches)]
    if voiced_pitches.size == 0:
        return None

    average = float(np.mean(voiced_pitches))
    return average if math.isfinite(average) else None


def _estimated_snr_db(waveform: np.ndarray) -> float | None:
    if waveform.size < _FRAME_LENGTH:
        return None

    rms = librosa.feature.rms(
        y=waveform,
        frame_length=_FRAME_LENGTH,
        hop_length=_HOP_LENGTH,
        center=False,
    )[0]
    powers = np.square(rms[np.isfinite(rms)])
    frame_count = powers.size
    quantile_count = max(1, math.ceil(frame_count * _ENERGY_QUANTILE))
    if frame_count < 5 or quantile_count == frame_count:
        return None

    ordered_powers = np.sort(powers)
    noise_power = float(np.mean(ordered_powers[:quantile_count]))
    speech_plus_noise_power = float(np.mean(ordered_powers[-quantile_count:]))
    signal_power = speech_plus_noise_power - noise_power
    if noise_power <= np.finfo(float).eps or signal_power <= 0:
        return None

    estimated_snr_db = 10.0 * math.log10(signal_power / noise_power)
    return estimated_snr_db if math.isfinite(estimated_snr_db) else None
