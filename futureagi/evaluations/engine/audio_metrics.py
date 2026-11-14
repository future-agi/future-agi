"""Deterministic acoustic measurements for decoded single-speaker audio."""

import logging
from dataclasses import dataclass
from io import BytesIO

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioMetrics:
    average_pitch_hz: float | None
    estimated_snr_db: float | None


def analyze_audio(audio_bytes: bytes) -> AudioMetrics:
    """Return voiced pitch and a finite, heuristic energy-based SNR estimate.

    The SNR uses the 10th and 90th frame-power quantiles as noise and speech
    proxies. It is not calibrated ground-truth SNR and returns ``None`` when
    the decoded clip does not contain enough signal/noise information.
    """
    if not isinstance(audio_bytes, (bytes, bytearray, memoryview)):
        raise TypeError(
            f"audio_bytes must be bytes-like, got {type(audio_bytes).__name__}"
        )
    if not audio_bytes:
        raise ValueError("audio_bytes is empty")

    try:
        import librosa
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Audio analysis dependencies are unavailable") from exc

    try:
        waveform, sample_rate = librosa.load(BytesIO(audio_bytes), sr=None, mono=True)
    except (ValueError, RuntimeError, EOFError, OSError) as exc:
        raise ValueError("Unable to decode audio bytes") from exc

    samples = np.asarray(waveform, dtype=np.float64)
    if samples.size == 0 or not np.all(np.isfinite(samples)):
        return AudioMetrics(None, None)
    if np.max(np.abs(samples)) < 1e-8:
        return AudioMetrics(None, None)

    # Fixed-duration windows keep pitch and energy measurements comparable.
    samples = librosa.resample(samples, orig_sr=sample_rate, target_sr=16000)
    sample_rate = 16000

    pitch = None
    try:
        frame_length = 2048
        if samples.size >= frame_length and sample_rate > 2 * librosa.note_to_hz("C2"):
            f0, voiced, _ = librosa.pyin(
                samples,
                sr=sample_rate,
                fmin=librosa.note_to_hz("C2"),
                fmax=min(librosa.note_to_hz("C7"), sample_rate * 0.45),
                frame_length=frame_length,
            )
            valid = f0[np.asarray(voiced, dtype=bool) & np.isfinite(f0)]
            if valid.size:
                pitch = float(np.mean(valid))
    except (ValueError, librosa.ParameterError):
        _LOGGER.warning("audio_metrics_pitch_unavailable", exc_info=True)

    frame_length = min(2048, samples.size)
    hop_length = max(1, frame_length // 4)
    rms = librosa.feature.rms(
        y=samples, frame_length=frame_length, hop_length=hop_length, center=False
    )[0]
    power = np.square(rms)
    power = power[np.isfinite(power)]
    snr = None
    if power.size >= 5:
        noise = float(np.quantile(power, 0.1))
        speech = float(np.quantile(power, 0.9))
        if noise > 1e-12 and speech > noise * 1.05:
            ratio = (speech - noise) / noise
            candidate = 10.0 * np.log10(ratio)
            if np.isfinite(candidate):
                snr = float(candidate)

    return AudioMetrics(pitch, snr)
