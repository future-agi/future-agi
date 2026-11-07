"""Additive waveform API: preserve the shipped byte API and analysis geometry."""

import builtins
import dataclasses
import importlib.util
import io
import sys
from unittest.mock import Mock

import librosa
import numpy as np
import pytest
import soundfile as sf

from evaluations.engine import audio_metrics as helper


def _fixture(sr, kind):
    t = np.arange(sr) / sr
    y = 0.3 * np.sin(2 * np.pi * 220 * t)
    if kind == "gated":
        y *= t > 0.5
        y += np.random.default_rng(2094).normal(0, 0.002, len(t))
    elif kind == "stereo":
        y = np.column_stack((y, 0.1 * np.sin(2 * np.pi * 440 * t)))
    elif kind == "short":
        y = np.array([0.1, -0.1, 0.1])
    elif kind == "silence":
        y = np.zeros(sr)
    elif kind == "noise":
        y = np.random.default_rng(2094).normal(0, 0.1, sr)
    buffer = io.BytesIO()
    sf.write(buffer, y, sr, format="WAV", subtype="FLOAT")
    return buffer.getvalue()


@pytest.mark.parametrize("sr", [8000, 16000, 44100, 48000])
@pytest.mark.parametrize(
    "kind", ["tone", "gated", "stereo", "short", "silence", "noise"]
)
def test_encoded_and_decoded_results_are_exactly_equal(sr, kind):
    data = _fixture(sr, kind)
    y, decoded_sr = librosa.load(io.BytesIO(data), sr=None, mono=True)
    before = y.tobytes()
    result, _ = helper.analyze_waveform(y, decoded_sr)
    assert helper.analyze_audio(data) == result
    assert y.tobytes() == before


def test_byte_api_delegates_decoded_samples(monkeypatch):
    y = np.ones(100)
    result = helper.AudioMetrics(123.0, -4.5)
    analyze = Mock(return_value=(result, None))
    load = Mock(return_value=(y, 44100))
    monkeypatch.setattr(librosa, "load", load)
    monkeypatch.setattr(helper, "analyze_waveform", analyze)
    assert helper.analyze_audio(b"encoded") is result
    analyze.assert_called_once_with(y, 44100)
    assert load.call_args.kwargs == {"sr": None, "mono": True}


@pytest.mark.parametrize("sr", [8000, 44100, 48000])
def test_returned_frames_match_actual_analysis_geometry(sr):
    data = _fixture(sr, "gated")
    y, _ = librosa.load(io.BytesIO(data), sr=None, mono=True)
    result, frames = helper.analyze_waveform(y, sr)
    samples = librosa.resample(y.astype(np.float64), orig_sr=sr, target_sr=16000)
    expected = librosa.feature.rms(
        y=samples, frame_length=2048, hop_length=512, center=False
    )[0]
    np.testing.assert_array_equal(frames.frame_power, np.square(expected))
    assert frames.sample_rate_hz == 16000
    assert frames.frame_seconds == frames.pitch_frame_seconds == 2048 / 16000
    assert frames.hop_seconds == frames.pitch_hop_seconds == 512 / 16000
    assert frames.frame_centered is False
    assert frames.pitch_centered is True
    assert len(frames.f0) == len(samples) // 512 + 1
    valid = frames.f0[frames.voiced_flag & np.isfinite(frames.f0)]
    assert result.average_pitch_hz == float(np.mean(valid))
    with pytest.raises(dataclasses.FrozenInstanceError):
        frames.sample_rate_hz = 8000
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.average_pitch_hz = 1


def test_snr_only_never_calls_pyin(monkeypatch):
    pyin = Mock(side_effect=AssertionError("pYIN must not run"))
    monkeypatch.setattr(librosa, "pyin", pyin)
    result, frames = helper.analyze_waveform(
        np.linspace(0.01, 0.5, 16000), 16000, compute_pitch=False
    )
    pyin.assert_not_called()
    assert result.average_pitch_hz is None
    assert result.estimated_snr_db is not None
    assert frames.f0 is frames.voiced_flag is None
    assert len(frames.frame_power) > 0


def test_pitch_only_never_calls_rms(monkeypatch):
    rms = Mock(side_effect=AssertionError("RMS must not run"))
    monkeypatch.setattr(librosa.feature, "rms", rms)
    y = 0.3 * np.sin(2 * np.pi * 220 * np.arange(16000) / 16000)
    result, frames = helper.analyze_waveform(y, 16000, compute_snr=False)
    rms.assert_not_called()
    assert result.average_pitch_hz == pytest.approx(220, rel=0.05)
    assert result.estimated_snr_db is None
    assert frames.frame_power.size == 0
    assert frames.frame_seconds == frames.hop_seconds == 0


@pytest.mark.parametrize("y", [[], [np.nan], [np.inf], [0.0], [1e-9]])
def test_empty_nonfinite_and_silent_samples_keep_missing_results(y):
    result, frames = helper.analyze_waveform(np.array(y), 16000)
    assert result == helper.AudioMetrics(None, None)
    assert frames.frame_power.size == 0
    assert frames.f0 is frames.voiced_flag is None


def test_short_waveform_uses_one_full_uncentered_rms_frame(monkeypatch):
    pyin = Mock(side_effect=AssertionError("short input must skip pYIN"))
    monkeypatch.setattr(librosa, "pyin", pyin)
    result, frames = helper.analyze_waveform(np.array([0.1, -0.1, 0.1]), 16000)
    assert result == helper.AudioMetrics(None, None)
    assert frames.frame_seconds == 3 / 16000
    assert frames.hop_seconds == 1 / 16000
    assert len(frames.frame_power) == 1
    assert frames.frame_centered is False
    pyin.assert_not_called()


@pytest.mark.parametrize("db", [-10, 0, 10, 30, 110])
def test_quantile_snr_is_not_floored_or_capped(monkeypatch, db):
    power = np.r_[np.ones(50), np.full(50, 1 + 10 ** (db / 10))]
    monkeypatch.setattr(librosa.feature, "rms", lambda **kwargs: np.sqrt(power)[None])
    result, _ = helper.analyze_waveform(np.ones(16000), 16000, compute_pitch=False)
    assert result.estimated_snr_db == pytest.approx(db, abs=1e-9)


@pytest.mark.parametrize(
    "power",
    [np.zeros(10), np.ones(10), np.r_[np.zeros(5), np.ones(5)], np.arange(1, 5)],
)
def test_insufficient_quantile_contrast_or_noise_returns_none(monkeypatch, power):
    monkeypatch.setattr(librosa.feature, "rms", lambda **kwargs: np.sqrt(power)[None])
    result, _ = helper.analyze_waveform(np.ones(16000), 16000, compute_pitch=False)
    assert result.estimated_snr_db is None


def test_nonfinite_power_positions_are_preserved_but_excluded_from_snr(monkeypatch):
    rms = np.array([1, np.nan, 1, 1, 1, 2, np.inf, 2, 2, 2], dtype=float)
    monkeypatch.setattr(librosa.feature, "rms", lambda **kwargs: rms[None])
    result, frames = helper.analyze_waveform(np.ones(16000), 16000, compute_pitch=False)
    np.testing.assert_array_equal(frames.frame_power, rms**2)
    assert result.estimated_snr_db == pytest.approx(10 * np.log10(3))


def test_pitch_error_keeps_snr_and_error_mapping(monkeypatch, caplog):
    monkeypatch.setattr(librosa, "pyin", Mock(side_effect=ValueError("pitch")))
    result, frames = helper.analyze_waveform(np.linspace(0.01, 0.5, 16000), 16000)
    assert result.average_pitch_hz is None
    assert result.estimated_snr_db is not None
    assert frames.f0 is frames.voiced_flag is None
    assert "audio_metrics_pitch_unavailable" in caplog.text


def test_pyin_parameters_and_voiced_only_mean_are_unchanged(monkeypatch):
    f0 = np.array([100.0, 200.0, np.nan, 400.0])
    voiced = np.array([True, True, True, False])
    pyin = Mock(return_value=(f0, voiced, np.ones(4)))
    monkeypatch.setattr(librosa, "pyin", pyin)
    y = np.linspace(0.01, 0.5, 8000, dtype=np.float32)
    result, frames = helper.analyze_waveform(y, 8000, compute_snr=False)
    assert result.average_pitch_hz == 150.0
    assert frames.f0 is f0
    assert frames.voiced_flag is voiced
    assert pyin.call_args.kwargs == {
        "sr": 16000,
        "fmin": librosa.note_to_hz("C2"),
        "fmax": librosa.note_to_hz("C7"),
        "frame_length": 2048,
    }
    np.testing.assert_array_equal(
        pyin.call_args.args[0],
        librosa.resample(y.astype(np.float64), orig_sr=8000, target_sr=16000),
    )


def test_waveform_requires_mono_samples():
    with pytest.raises(ValueError, match="waveform must be mono"):
        helper.analyze_waveform(np.ones((16000, 2)), 16000)


def test_module_import_keeps_heavy_dependencies_lazy_and_has_no_ee_import(monkeypatch):
    real_import = builtins.__import__

    def importing(name, *args, **kwargs):
        assert name.split(".")[0] not in {"librosa", "numpy", "ee"}
        return real_import(name, *args, **kwargs)

    spec = importlib.util.spec_from_file_location(
        "audio_metrics_probe", helper.__file__
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    monkeypatch.setattr(builtins, "__import__", importing)
    spec.loader.exec_module(module)
    assert dataclasses.is_dataclass(module.AudioFrames)


@pytest.mark.parametrize("dependency", ["librosa", "numpy"])
def test_dependency_error_mapping_stays_runtime_error(monkeypatch, dependency):
    real_import = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == dependency:
            raise ImportError(f"missing {dependency}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    for analyze, value in (
        (helper.analyze_audio, b"encoded"),
        (lambda y: helper.analyze_waveform(y, 16000), np.ones(16000)),
    ):
        with pytest.raises(RuntimeError, match="Audio analysis dependencies"):
            analyze(value)
