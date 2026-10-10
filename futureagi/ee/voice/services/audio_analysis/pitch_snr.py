"""Eligibility and interval coverage over the unchanged helper's own frames."""

import math

import numpy as np

from ee.voice.services.audio_metrics import (
    _ACTIVITY_MARGIN_DB,
    _NOISE_FLOOR_PERCENTILE,
    _POWER_EPSILON,
    _estimated_snr_db_detail,
    analyze_waveform,
)

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
    metrics, frames = analyze_waveform(y, sr)
    duration = len(y) / sr
    voiced = frames.voiced_flag
    if voiced is None or frames.f0 is None:
        voiced = np.array([], dtype=bool)
    else:
        voiced = voiced & np.isfinite(frames.f0)
    hop = frames.pitch_hop_seconds or 0
    seconds = frame_coverage(voiced, 4 * hop, hop, duration, centered=True)
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
    if len(power):
        floor = max(
            float(np.percentile(power, _NOISE_FLOOR_PERCENTILE)), _POWER_EPSILON
        )
        active = power > floor * 10 ** (_ACTIVITY_MARGIN_DB / 10)
        _, _, floored, capped = _estimated_snr_db_detail(power)
    else:
        active = np.array([], dtype=bool)
        floored = capped = False
    active_s = frame_coverage(
        active, frames.frame_seconds, frames.hop_seconds, duration
    )
    inactive_s = frame_coverage(
        ~active, frames.frame_seconds, frames.hop_seconds, duration
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
        flags={"noise_floor_floored": floored, "value_capped": capped},
    )
    return pitch, snr
