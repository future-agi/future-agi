"""EE eligibility and interval coverage over the canonical OSS measurements."""

import math

import numpy as np

from evaluations.engine.audio_metrics import analyze_waveform

from .constants import PITCH_ALGORITHM_VERSION, SNR_ALGORITHM_VERSION
from .envelope import metric_entry


def interval_union(intervals):
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def interval_seconds(intervals):
    return sum(end - start for start, end in interval_union(intervals))


def frame_coverage(mask, frame_seconds, hop_seconds, duration, *, centered=False):
    offset = frame_seconds / 2 if centered else 0
    return interval_seconds(
        (
            max(0, i * hop_seconds - offset),
            min(duration, i * hop_seconds - offset + frame_seconds),
        )
        for i in np.flatnonzero(mask)
    )


def _result(name, value, reason, **kwargs):
    state = "unavailable" if reason else "available"
    if value is not None and not math.isfinite(value):
        state, reason = "failed", "nonfinite_output"
    return metric_entry(
        name,
        value=value if state == "available" else None,
        state=state,
        reason=reason,
        **kwargs,
    )


def analyze_pitch_snr(y, sr):
    """Apply coverage gates without changing the canonical scalar estimates.

    For quantile SNR, active_seconds covers the union of frames at/above the
    speech (90th) quantile; inactive_seconds covers frames at/below the noise
    (10th) quantile. These are power proxies, not a speech/pause classifier, and
    their interval unions may overlap. Nonfinite powers never contribute.
    """
    metrics, frames = analyze_waveform(y, sr)
    duration = len(y) / sr
    voiced = frames.voiced_flag
    if voiced is None or frames.f0 is None:
        voiced = np.array([], dtype=bool)
    else:
        voiced = voiced & np.isfinite(frames.f0)
    seconds = frame_coverage(
        voiced,
        frames.pitch_frame_seconds,
        frames.pitch_hop_seconds,
        duration,
        centered=frames.pitch_centered,
    )
    pitch_reason = (
        "no_voiced_frames"
        if seconds == 0
        else "insufficient_voice"
        if seconds < 0.25
        else None
    )
    pitch = _result(
        "average_pitch_hz",
        metrics.average_pitch_hz,
        pitch_reason,
        algorithm_version=PITCH_ALGORITHM_VERSION,
        coverage={
            "analyzed_seconds": duration,
            "voiced_seconds": seconds,
            "frame_count": len(voiced),
            "voiced_frame_count": int(voiced.sum()),
        },
    )
    power = frames.frame_power
    finite = np.isfinite(power)
    active = inactive = np.zeros(len(power), dtype=bool)
    if finite.any():
        noise, speech = np.quantile(power[finite], [0.1, 0.9])
        active = finite & (power >= speech)
        inactive = finite & (power <= noise)
    active_s = frame_coverage(
        active,
        frames.frame_seconds,
        frames.hop_seconds,
        duration,
        centered=frames.frame_centered,
    )
    inactive_s = frame_coverage(
        inactive,
        frames.frame_seconds,
        frames.hop_seconds,
        duration,
        centered=frames.frame_centered,
    )
    snr_reason = (
        "no_noise_floor"
        if metrics.estimated_snr_db is None
        else "insufficient_voice"
        if min(active_s, inactive_s) < 0.25
        else None
    )
    snr = _result(
        "estimated_snr_db",
        metrics.estimated_snr_db,
        snr_reason,
        algorithm_version=SNR_ALGORITHM_VERSION,
        coverage={
            "analyzed_seconds": duration,
            "active_seconds": active_s,
            "inactive_seconds": inactive_s,
            "frame_count": len(power),
        },
        # The canonical estimator neither floors nor caps; retain the schema keys.
        flags={"noise_floor_floored": False, "value_capped": False},
    )
    return pitch, snr
