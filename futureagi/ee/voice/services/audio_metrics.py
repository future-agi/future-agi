from __future__ import annotations

import io
from dataclasses import dataclass

import librosa
import numpy as np


@dataclass(frozen=True)
class AudioMetrics:
    average_pitch_hz: float | None
    estimated_snr_db: float | None


def analyze_audio(audio_bytes: bytes) -> AudioMetrics:
    """Analyze a single-speaker audio track for pitch and estimated SNR.

    Average pitch is the arithmetic mean of finite fundamental-frequency
    estimates returned by ``librosa.pyin`` for voiced frames only.

    Estimated SNR is a deterministic frame-energy estimate. RMS energy is
    calculated over fixed-size frames, the lowest-energy frames are used as a
    background-noise estimate, and the highest-energy frames are used as a
    signal estimate. The ratio of the two mean powers is returned in decibels.

    This is only an estimate: without a separate clean reference recording,
    the returned value is not a ground-truth signal-to-noise ratio.

    Args:
        audio_bytes: Encoded audio bytes supported by librosa.

    Returns:
        AudioMetrics containing average pitch and estimated SNR. Either value
        may be None when the audio does not contain enough information for a
        meaningful measurement.

    Raises:
        ValueError: If the supplied bytes cannot be decoded as audio.
    """
    try:
        y, sr = librosa.load(
            io.BytesIO(audio_bytes),
            sr=None,
            mono=True,
        )
    except Exception as exc:
        raise ValueError("Could not decode audio bytes") from exc

    y = np.asarray(y, dtype=float)

    if y.size == 0 or not np.any(np.isfinite(y)):
        return AudioMetrics(
            average_pitch_hz=None,
            estimated_snr_db=None,
        )

    finite_samples = y[np.isfinite(y)]
    if finite_samples.size == 0 or np.allclose(finite_samples, 0.0):
        return AudioMetrics(
            average_pitch_hz=None,
            estimated_snr_db=None,
        )

    average_pitch_hz = _average_pitch_hz(y, sr)
    estimated_snr_db = _estimate_snr_db(y, sr)

    return AudioMetrics(
        average_pitch_hz=average_pitch_hz,
        estimated_snr_db=estimated_snr_db,
    )


def _average_pitch_hz(
    y: np.ndarray,
    sr: int,
) -> float | None:
    """Return the mean fundamental frequency across finite voiced frames."""
    try:
        f0, voiced_flag, _ = librosa.pyin(
            y,
            fmin=librosa.note_to_hz("C2"),
            fmax=librosa.note_to_hz("C7"),
            sr=sr,
        )
    except Exception:
        return None

    if f0 is None or voiced_flag is None:
        return None

    voiced_frequencies = f0[np.asarray(voiced_flag, dtype=bool) & np.isfinite(f0)]

    if voiced_frequencies.size == 0:
        return None

    average = float(np.mean(voiced_frequencies))

    return average if np.isfinite(average) else None


def _estimate_snr_db(
    y: np.ndarray,
    sr: int,
) -> float | None:
    """Estimate SNR from deterministic frame RMS variation.

    The waveform is split into fixed overlapping frames and RMS energy is
    calculated for each frame. The median RMS is used as a robust estimate of
    the typical signal-plus-noise level. The median absolute deviation of RMS
    values estimates background-noise variation.

    Signal power is estimated as the squared median RMS. Noise power is
    estimated as the squared RMS median absolute deviation. The returned value
    is ``10 * log10(signal_power / noise_power)``.

    This is an energy-based estimate rather than a ground-truth SNR because no
    separate clean reference recording is available.
    """
    frame_length = max(1, min(len(y), int(0.05 * sr)))
    hop_length = max(1, frame_length // 2)

    rms = librosa.feature.rms(
        y=y,
        frame_length=frame_length,
        hop_length=hop_length,
    )[0]

    rms = rms[np.isfinite(rms)]

    if rms.size < 4:
        return None

    median_rms = float(np.median(rms))

    if not np.isfinite(median_rms) or median_rms <= 0.0:
        return None

    median_absolute_deviation = float(np.median(np.abs(rms - median_rms)))

    if not np.isfinite(median_absolute_deviation) or median_absolute_deviation <= 0.0:
        return None

    signal_power = median_rms**2
    noise_power = median_absolute_deviation**2

    if (
        not np.isfinite(signal_power)
        or not np.isfinite(noise_power)
        or noise_power <= 0.0
    ):
        return None

    estimated_snr_db = float(10.0 * np.log10(signal_power / noise_power))

    return estimated_snr_db if np.isfinite(estimated_snr_db) else None
