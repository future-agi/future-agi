"""Deterministic acoustic measurements for decoded single-speaker audio."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioMetrics:
    average_pitch_hz: float | None
    estimated_snr_db: float | None


@dataclass(frozen=True)
class AudioFrames:
    """Frame measurements on the 16 kHz analysis timeline, in seconds.

    Power frames are uncentered RMS squared, normally 2048 samples with a
    512-sample hop (short inputs use their full length and a quarter-length hop).
    Nonfinite powers retain their positions here, but are excluded from SNR.
    pYIN uses centered 2048-sample frames with a 512-sample hop. A centered
    frame i spans i * hop - frame / 2 through i * hop + frame / 2; its padded
    edges are outside the input timeline. Missing/skipped pitch has None arrays;
    missing/skipped power has an empty array and zero frame/hop durations.
    """

    sample_rate_hz: int
    frame_power: np.ndarray
    frame_seconds: float
    hop_seconds: float
    frame_centered: bool
    f0: np.ndarray | None
    voiced_flag: np.ndarray | None
    pitch_frame_seconds: float
    pitch_hop_seconds: float
    pitch_centered: bool


def _analysis_dependencies():
    try:
        import librosa
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Audio analysis dependencies are unavailable") from exc
    return librosa, np


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

    librosa, _ = _analysis_dependencies()
    try:
        waveform, sample_rate = librosa.load(BytesIO(audio_bytes), sr=None, mono=True)
    except (ValueError, RuntimeError, EOFError, OSError) as exc:
        raise ValueError("Unable to decode audio bytes") from exc
    return analyze_waveform(waveform, sample_rate)[0]


def analyze_waveform(
    y: np.ndarray,
    sr: int,
    *,
    compute_pitch: bool = True,
    compute_snr: bool = True,
) -> tuple[AudioMetrics, AudioFrames]:
    """Analyze decoded mono samples with the same estimator as analyze_audio.

    Conversion to float64 and resampling to 16 kHz do not mutate the input.
    A disabled computation returns None for its scalar and no measured frames.
    """
    librosa, np = _analysis_dependencies()
    samples = np.asarray(y, dtype=np.float64)
    if samples.ndim != 1:
        raise ValueError("waveform must be mono")
    empty_frames = AudioFrames(
        sample_rate_hz=16000,
        frame_power=np.array([], dtype=float),
        frame_seconds=0.0,
        hop_seconds=0.0,
        frame_centered=False,
        f0=None,
        voiced_flag=None,
        pitch_frame_seconds=2048 / 16000,
        pitch_hop_seconds=512 / 16000,
        pitch_centered=True,
    )
    if samples.size == 0 or not np.all(np.isfinite(samples)):
        return AudioMetrics(None, None), empty_frames
    if np.max(np.abs(samples)) < 1e-8:
        return AudioMetrics(None, None), empty_frames

    # Fixed-duration windows keep pitch and energy measurements comparable.
    samples = librosa.resample(samples, orig_sr=sr, target_sr=16000)
    sample_rate = 16000

    pitch = None
    f0 = voiced = None
    if compute_pitch:
        try:
            frame_length = 2048
            if samples.size >= frame_length and sample_rate > 2 * librosa.note_to_hz(
                "C2"
            ):
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

    frame_length = hop_length = 0
    power = empty_frames.frame_power
    snr = None
    if compute_snr:
        frame_length = min(2048, samples.size)
        hop_length = max(1, frame_length // 4)
        rms = librosa.feature.rms(
            y=samples, frame_length=frame_length, hop_length=hop_length, center=False
        )[0]
        power = np.square(rms)
        finite_power = power[np.isfinite(power)]
        if finite_power.size >= 5:
            noise = float(np.quantile(finite_power, 0.1))
            speech = float(np.quantile(finite_power, 0.9))
            if noise > 1e-12 and speech > noise * 1.05:
                ratio = (speech - noise) / noise
                candidate = 10.0 * np.log10(ratio)
                if np.isfinite(candidate):
                    snr = float(candidate)

    frames = AudioFrames(
        sample_rate_hz=sample_rate,
        frame_power=power,
        frame_seconds=frame_length / sample_rate,
        hop_seconds=hop_length / sample_rate,
        frame_centered=False,
        f0=f0,
        voiced_flag=voiced,
        pitch_frame_seconds=2048 / sample_rate,
        pitch_hop_seconds=512 / sample_rate,
        pitch_centered=True,
    )
    return AudioMetrics(pitch, snr), frames
