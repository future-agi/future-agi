"""
Tests for the dead_air_detection preprocessor.

The preprocessor uses ``audio_bytes_from_url_or_base64`` (the canonical
audio loader in tfc/utils/storage.py) to resolve the input, then decodes
with librosa and injects ``_dead_air_*`` kwargs for the sandbox body.
The sandbox body itself is trivial threshold logic and is verified by
inspection.
"""

from __future__ import annotations

import io
import math
from unittest.mock import patch

import pytest

from evaluations.engine.preprocessing import PREPROCESSORS, preprocess_inputs


def test_dead_air_preprocessor_registered():
    assert "dead_air_detection" in PREPROCESSORS


def test_missing_audio_returns_error():
    out = preprocess_inputs("dead_air_detection", {})
    assert out["_dead_air_error"] == "Missing input_audio"


def test_loader_failure_returns_error():
    with patch(
        "tfc.utils.storage.audio_bytes_from_url_or_base64",
        side_effect=ValueError("not a valid audio source"),
    ):
        out = preprocess_inputs(
            "dead_air_detection",
            {"input_audio": "not-a-url"},
        )
    assert "_dead_air_error" in out
    assert "not a valid audio source" in out["_dead_air_error"]


def _synth_wav_bytes(duration_sec=2.0, sr=8000, silence_segments=None):
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        pytest.skip("numpy/soundfile not installed")
    n = int(duration_sec * sr)
    t = np.linspace(0, duration_sec, n, endpoint=False)
    y = 0.5 * np.sin(2 * math.pi * 440 * t).astype("float32")
    for (s, e) in silence_segments or []:
        y[int(s * sr):int(e * sr)] = 0.0
    buf = io.BytesIO()
    sf.write(buf, y, sr, format="WAV")
    return buf.getvalue()


def test_clean_audio_has_low_dead_air():
    body = _synth_wav_bytes(duration_sec=1.0, silence_segments=None)
    with patch(
        "tfc.utils.storage.audio_bytes_from_url_or_base64",
        return_value=body,
    ):
        out = preprocess_inputs(
            "dead_air_detection",
            {"input_audio": "https://example.com/clean.wav"},
        )
    assert "_dead_air_error" not in out
    assert out["_dead_air_percentage"] < 5.0
    assert out["_dead_air_max_gap_ms"] < 200.0


def test_silent_audio_is_mostly_dead_air():
    body = _synth_wav_bytes(
        duration_sec=2.0,
        silence_segments=[(0.0, 1.6)],
    )
    with patch(
        "tfc.utils.storage.audio_bytes_from_url_or_base64",
        return_value=body,
    ):
        out = preprocess_inputs(
            "dead_air_detection",
            {"input_audio": "https://example.com/silent.wav"},
        )
    assert "_dead_air_error" not in out
    assert out["_dead_air_percentage"] > 50.0
    assert out["_dead_air_max_gap_ms"] > 1000.0


def test_loader_called_with_no_silence_padding():
    """Padding short audio with synthetic silence would inflate the metric."""
    body = _synth_wav_bytes(duration_sec=0.5)
    with patch(
        "tfc.utils.storage.audio_bytes_from_url_or_base64",
        return_value=body,
    ) as mock_loader:
        preprocess_inputs(
            "dead_air_detection",
            {"input_audio": "https://example.com/short.wav"},
        )
    _, kwargs = mock_loader.call_args
    assert kwargs.get("pad_silence") is False
    assert kwargs.get("min_duration_seconds") is None
