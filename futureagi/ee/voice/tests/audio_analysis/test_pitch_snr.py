"""R05/R06 reconciliation: canonical values, EE coverage and eligibility only."""

import dataclasses

import numpy as np
import pytest

from ee.voice.services.audio_analysis import pitch_snr
from evaluations.engine import audio_metrics as helper


def tone(sr=16000, seconds=1.5):
    return (0.5 * np.sin(2 * np.pi * 220 * np.arange(round(sr * seconds)) / sr)).astype(
        np.float32
    )


def _frames(**kwargs):
    frames = helper.AudioFrames(
        sample_rate_hz=16000,
        frame_power=np.r_[np.ones(50), np.full(50, 2.0)],
        frame_seconds=2048 / 16000,
        hop_seconds=512 / 16000,
        frame_centered=False,
        f0=np.full(50, 220.0),
        voiced_flag=np.ones(50, dtype=bool),
        pitch_frame_seconds=2048 / 16000,
        pitch_hop_seconds=512 / 16000,
        pitch_centered=True,
    )
    return dataclasses.replace(frames, **kwargs)


def _stub(monkeypatch, metrics, frames):
    monkeypatch.setattr(pitch_snr, "analyze_waveform", lambda y, sr: (metrics, frames))


@pytest.mark.parametrize("sr", [8000, 16000, 44100, 48000])
def test_220hz_within_5pct_via_wrapper(sr):
    y = tone(sr)
    before = y.tobytes()
    pitch, snr = pitch_snr.analyze_pitch_snr(y, sr)
    assert pitch["value"] == pytest.approx(220, rel=0.05)
    assert pitch["algorithm_version"] == "pyin_c2c7_16k_v1"
    assert snr["algorithm_version"] == "power_quantiles_q90_q10_16k_v1"
    assert snr["reason"] == "no_noise_floor"
    assert y.tobytes() == before


@pytest.mark.parametrize("sr", [8000, 16000, 44100, 48000])
def test_ee_available_values_are_verbatim_canonical_values(sr):
    y = np.tile(np.r_[np.zeros(sr // 2), tone(sr, 0.5)], 4)
    y += np.random.default_rng(2094).normal(0, 0.002, len(y))
    canonical, frames = helper.analyze_waveform(y, sr)
    pitch, snr = pitch_snr.analyze_pitch_snr(y, sr)
    assert pitch["state"] == snr["state"] == "available"
    assert pitch["value"] == canonical.average_pitch_hz
    assert snr["value"] == canonical.estimated_snr_db
    assert pitch["coverage"]["frame_count"] == len(frames.f0)
    assert snr["coverage"]["frame_count"] == len(frames.frame_power)
    assert snr["flags"] == {"noise_floor_floored": False, "value_capped": False}
    assert set(pitch["coverage"]) == {
        "analyzed_seconds",
        "voiced_seconds",
        "frame_count",
        "voiced_frame_count",
    }
    assert set(snr["coverage"]) == {
        "analyzed_seconds",
        "active_seconds",
        "inactive_seconds",
        "frame_count",
    }


@pytest.mark.parametrize(
    "seconds,reason",
    [(0, "no_voiced_frames"), (0.249, "insufficient_voice"), (0.25, None)],
)
def test_pitch_coverage_threshold_0_25s(monkeypatch, seconds, reason):
    frames = _frames(voiced_flag=np.full(50, seconds > 0, dtype=bool))
    _stub(monkeypatch, helper.AudioMetrics(220, None), frames)
    pitch, _ = pitch_snr.analyze_pitch_snr(tone(seconds=seconds or 1), 16000)
    assert pitch["reason"] == reason
    assert pitch["coverage"]["voiced_seconds"] == pytest.approx(seconds)
    assert pitch["value"] == (220 if reason is None else None)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("invalid", ["pitch", "snr"])
def test_one_invalid_metric_keeps_sibling(monkeypatch, bad, invalid):
    metrics = (
        helper.AudioMetrics(bad, 110)
        if invalid == "pitch"
        else helper.AudioMetrics(220, bad)
    )
    _stub(monkeypatch, metrics, _frames())
    pitch, snr = pitch_snr.analyze_pitch_snr(tone(seconds=4), 16000)
    failed, available = (pitch, snr) if invalid == "pitch" else (snr, pitch)
    assert failed["state"] == "failed"
    assert failed["reason"] == "nonfinite_output"
    assert failed["value"] is None
    assert available["state"] == "available"
    assert snr["flags"] == {"noise_floor_floored": False, "value_capped": False}


@pytest.mark.parametrize(
    "seconds,reason", [(0.249, "insufficient_voice"), (0.25, None)]
)
def test_snr_coverage_threshold_and_quantile_ties(monkeypatch, seconds, reason):
    # Tied quantiles cover both masks; coverage is not a complementary VAD mask.
    _stub(monkeypatch, helper.AudioMetrics(None, 0), _frames(frame_power=np.ones(20)))
    _, snr = pitch_snr.analyze_pitch_snr(tone(seconds=seconds), 16000)
    assert snr["reason"] == reason
    assert snr["coverage"]["active_seconds"] == pytest.approx(seconds)
    assert snr["coverage"]["inactive_seconds"] == pytest.approx(seconds)


@pytest.mark.parametrize("power", [np.arange(10.0), np.arange(10.0)[::-1]])
def test_snr_requires_both_quantile_tails_to_cover_0_25s(monkeypatch, power):
    _stub(monkeypatch, helper.AudioMetrics(None, 5), _frames(frame_power=power))
    _, snr = pitch_snr.analyze_pitch_snr(tone(), 16000)
    assert snr["reason"] == "insufficient_voice"
    assert snr["coverage"]["active_seconds"] == pytest.approx(0.128)
    assert snr["coverage"]["inactive_seconds"] == pytest.approx(0.128)


def test_snr_none_takes_precedence_over_insufficient_coverage(monkeypatch):
    _stub(
        monkeypatch, helper.AudioMetrics(None, None), _frames(frame_power=np.array([]))
    )
    _, snr = pitch_snr.analyze_pitch_snr(tone(), 16000)
    assert snr["reason"] == "no_noise_floor"
    assert snr["value"] is None


@pytest.mark.parametrize("value", [-10, 0, 110])
def test_adapter_neither_floors_nor_caps_canonical_value(monkeypatch, value):
    _stub(monkeypatch, helper.AudioMetrics(220, value), _frames())
    _, snr = pitch_snr.analyze_pitch_snr(tone(seconds=4), 16000)
    assert snr["state"] == "available"
    assert snr["value"] == value
    assert snr["flags"] == {"noise_floor_floored": False, "value_capped": False}


def test_nonfinite_frames_do_not_shift_timestamps_or_count_as_coverage(monkeypatch):
    power = np.array([np.nan] * 10 + [1.0] + [np.inf] * 10 + [2.0])
    frames = _frames(
        frame_power=power,
        f0=np.array([np.nan, 220.0, np.inf]),
        voiced_flag=np.ones(3, dtype=bool),
    )
    _stub(monkeypatch, helper.AudioMetrics(220, 5), frames)
    pitch, snr = pitch_snr.analyze_pitch_snr(tone(seconds=0.5), 16000)
    assert pitch["coverage"]["voiced_frame_count"] == 1
    assert pitch["coverage"]["voiced_seconds"] == pytest.approx(0.096)
    assert snr["coverage"]["frame_count"] == 22
    assert snr["coverage"]["inactive_seconds"] == pytest.approx(0.128)
    assert snr["coverage"]["active_seconds"] == 0


def test_frame_interval_union_clips_centering_and_overlaps():
    assert pitch_snr.frame_coverage(
        [True] * 5, 0.128, 0.032, 0.15, centered=True
    ) == pytest.approx(0.15)
    assert pitch_snr.frame_coverage(
        [True, True, False, False, False, False, True], 0.128, 0.032, 1
    ) == pytest.approx(0.288)


def test_gated_noise_sweep_monotone():
    y = np.tile(np.r_[np.zeros(8000), tone(seconds=0.5)], 4)
    values = []
    for sigma in (0.001, 0.01, 0.05, 0.15):
        metrics, _ = helper.analyze_waveform(
            y + np.random.default_rng(2094).normal(0, sigma, y.size),
            16000,
            compute_pitch=False,
        )
        values.append(metrics.estimated_snr_db)
    assert all(a > b for a, b in zip(values, values[1:], strict=False))
