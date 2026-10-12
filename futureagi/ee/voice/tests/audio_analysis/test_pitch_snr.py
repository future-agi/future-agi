"""R05/R06, AC03–AC05: native waveform and eligibility contracts."""

import dataclasses

import numpy as np
import pytest

from ee.voice.services import audio_metrics as helper
from ee.voice.services.audio_analysis import pitch_snr


def tone(sr=16000, seconds=1.5):
    return (0.5 * np.sin(2 * np.pi * 220 * np.arange(round(sr * seconds)) / sr)).astype(
        np.float32
    )


@pytest.mark.parametrize("sr", [8000, 16000, 44100])
def test_220hz_within_5pct_via_wrapper(sr):
    y = tone(sr)
    before = y.tobytes()
    pitch, snr = pitch_snr.analyze_pitch_snr(y, sr)
    assert pitch["value"] == pytest.approx(220, rel=0.05)
    assert pitch["algorithm_version"] == "pyin_c2c6_v1"
    assert snr["reason"] == "no_noise_floor"
    assert y.tobytes() == before


def test_waveform_result_and_frames():
    result, frames = helper.analyze_waveform(tone(), 16000)
    assert result.average_pitch_hz == pytest.approx(220, rel=0.05)
    assert frames.voiced_flag.any()
    assert frames.pitch_hop_seconds == 512 / 16000
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.average_pitch_hz = 1


@pytest.mark.parametrize(
    "seconds,reason",
    [(0, "no_voiced_frames"), (0.249, "insufficient_voice"), (0.25, None)],
)
def test_pitch_coverage_threshold_0_25s(monkeypatch, seconds, reason):
    frames = helper.AudioFrames(
        np.array([0.0, 1.0]),
        0.025,
        0.01,
        np.array([np.nan, np.nan, 220.0]),
        np.array([False, False, seconds > 0]),
        seconds / 4,
    )
    monkeypatch.setattr(
        pitch_snr,
        "analyze_waveform",
        lambda y, sr: (helper.AudioMetrics(220, None), frames),
    )
    pitch, _ = pitch_snr.analyze_pitch_snr(tone(), 16000)
    assert pitch["reason"] == reason


def test_one_invalid_metric_keeps_sibling(monkeypatch):
    frames = helper.AudioFrames(
        np.r_[np.zeros(50), np.ones(50)],
        0.025,
        0.01,
        np.full(50, 220),
        np.ones(50, dtype=bool),
        0.032,
    )
    monkeypatch.setattr(
        pitch_snr,
        "analyze_waveform",
        lambda y, sr: (helper.AudioMetrics(float("nan"), 100), frames),
    )
    pitch, snr = pitch_snr.analyze_pitch_snr(tone(), 16000)
    assert pitch["reason"] == "nonfinite_output"
    assert snr["state"] == "available"
    assert snr["flags"] == {"noise_floor_floored": True, "value_capped": True}


def test_snr_coverage_0_25s_active_and_inactive(monkeypatch):
    frames = helper.AudioFrames(
        np.r_[np.zeros(2), np.ones(70)], 0.025, 0.01, None, None, None
    )
    monkeypatch.setattr(
        pitch_snr,
        "analyze_waveform",
        lambda y, sr: (helper.AudioMetrics(None, 100), frames),
    )
    _, snr = pitch_snr.analyze_pitch_snr(tone(), 16000)
    assert snr["reason"] == "insufficient_voice"


def test_gated_noise_sweep_monotone():
    y = np.tile(np.r_[np.zeros(8000), tone(seconds=0.5)], 4)
    values = []
    for sigma in (0.001, 0.01, 0.05, 0.15):
        power = helper._frame_power(
            y + np.random.default_rng(2094).normal(0, sigma, y.size), 16000
        )
        values.append(helper._estimated_snr_db_detail(power)[0])
    assert all(a > b for a, b in zip(values, values[1:], strict=False))


@pytest.mark.parametrize("db", [0, 10, 20, 30])
def test_known_ratio_within_3db(db):
    # At 0 dB, the unchanged 6 dB activity gate cannot separate stationary
    # signal+noise from pauses. That case must remain no_noise_floor.
    power = np.r_[np.ones(100), np.full(100, 1 + 10 ** (db / 10))]
    value, _, _, _ = helper._estimated_snr_db_detail(power)
    if db == 0:
        assert value is None
    else:
        assert value == pytest.approx(db, abs=3)
