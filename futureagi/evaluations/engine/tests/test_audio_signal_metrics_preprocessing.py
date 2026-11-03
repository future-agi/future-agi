from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from evaluations.engine.preprocessing import PREPROCESSORS, preprocess_inputs


def _audio_modules():
    import numpy as np
    import soundfile as sf

    return np, sf


def _wav_bytes():
    np, sf = _audio_modules()
    sample_rate = 16_000
    t = np.arange(sample_rate) / sample_rate
    rng = np.random.default_rng(219)
    signal = 0.3 * np.sin(2 * np.pi * 220 * t) + rng.normal(0, 0.005, t.shape)
    signal[: sample_rate // 5] = rng.normal(0, 0.005, sample_rate // 5)
    buffer = io.BytesIO()
    sf.write(buffer, signal, sample_rate, format="WAV")
    return buffer.getvalue()


def test_audio_signal_metrics_preprocessor_is_registered():
    assert "audio_signal_metrics" in PREPROCESSORS


def test_missing_audio_returns_error():
    result = preprocess_inputs("audio_signal_metrics", {})
    assert result["_audio_metrics_error"] == "Missing input_audio"


def test_loader_failure_returns_error():
    with patch(
        "tfc.utils.storage.audio_bytes_from_url_or_base64",
        side_effect=ValueError("not a valid audio source"),
    ):
        result = preprocess_inputs(
            "audio_signal_metrics",
            {"input_audio": "https://example.com/audio.wav"},
        )

    assert "not a valid audio source" in result["_audio_metrics_error"]


def test_real_audio_injects_finite_metrics_and_disables_padding():
    np, _ = _audio_modules()
    with patch(
        "tfc.utils.storage.audio_bytes_from_url_or_base64",
        return_value=_wav_bytes(),
    ) as loader:
        result = preprocess_inputs(
            "audio_signal_metrics",
            {"input_audio": "https://example.com/audio.wav"},
        )

    assert "_audio_metrics_error" not in result
    assert result["_audio_average_pitch_hz"] == pytest.approx(220, rel=0.05)
    assert result["_audio_estimated_snr_db"] is not None
    assert np.isfinite(result["_audio_estimated_snr_db"])
    _, kwargs = loader.call_args
    assert kwargs == {"min_duration_seconds": None, "pad_silence": False}


def test_analysis_failure_is_reported_without_crashing():
    with (
        patch(
            "tfc.utils.storage.audio_bytes_from_url_or_base64",
            return_value=_wav_bytes(),
        ),
        patch(
            "evaluations.engine.audio_metrics.analyze_audio",
            side_effect=RuntimeError("audio dependency unavailable"),
        ),
    ):
        result = preprocess_inputs(
            "audio_signal_metrics",
            {"input_audio": "https://example.com/audio.wav"},
        )

    assert "audio dependency unavailable" in result["_audio_metrics_error"]
