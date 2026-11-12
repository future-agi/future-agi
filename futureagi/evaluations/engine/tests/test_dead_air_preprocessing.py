"""
Tests for the dead_air_detection preprocessor.

The preprocessor uses ``audio_bytes_from_url_or_base64`` (the canonical
audio loader in tfc/utils/storage.py) to resolve the input, then decodes
with librosa and injects ``_dead_air_*`` kwargs for the sandbox body.
The catalog sandbox body is executed against the preprocessed kwargs to
verify that real audio coverage determines the inclusive budget verdict.
"""

from __future__ import annotations

import io
import math
import wave
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

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


def _silent_wav_bytes(sample_count, sr=8000):
    """Encode exactly the requested number of silent mono PCM samples."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(b"\x00\x00" * sample_count)
    return buf.getvalue()


@pytest.fixture(
    params=[
        pytest.param(8000, id="one-second"),
        pytest.param(16000, id="two-seconds"),
        pytest.param(199, id="shorter-than-hop"),
        pytest.param(801, id="partial-hop"),
        pytest.param(8399, id="tail-past-midpoint"),
    ]
)
def all_silent_audio(request):
    sample_count = request.param
    with patch(
        "tfc.utils.storage.audio_bytes_from_url_or_base64",
        return_value=_silent_wav_bytes(sample_count),
    ):
        out = preprocess_inputs(
            "dead_air_detection",
            {
                "input_audio": "https://example.com/all-silent.wav",
                "silence_threshold": 0.01,
            },
        )
    assert "_dead_air_error" not in out
    return out, sample_count / 8000


@pytest.fixture(scope="module")
def catalog_evaluate():
    template_path = (
        Path(__file__).resolve().parents[3]
        / "model_hub/system_evals/function/dead_air_detection.yaml"
    )
    template = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    namespace = {}
    exec(compile(template["config"]["code"], str(template_path), "exec"), namespace)
    return namespace["evaluate"]


def test_all_silent_percentage_matches_real_coverage(all_silent_audio):
    out, duration = all_silent_audio
    assert out["_dead_air_duration_sec"] == pytest.approx(duration)
    assert out["_dead_air_percentage"] == pytest.approx(100.0)
    assert 0.0 <= out["_dead_air_percentage"] <= 100.0


def test_all_silent_gap_matches_real_coverage(all_silent_audio):
    out, duration = all_silent_audio
    assert out["_dead_air_max_gap_ms"] == pytest.approx(duration * 1000.0)
    assert 0.0 <= out["_dead_air_max_gap_ms"] <= duration * 1000.0


def test_catalog_accepts_inclusive_silence_budgets(all_silent_audio, catalog_evaluate):
    out, duration = all_silent_audio
    result = catalog_evaluate(
        None,
        None,
        None,
        None,
        **out,
        dead_air_threshold=100,
        gap_threshold_ms=duration * 1000.0,
    )
    assert result["score"] == 1.0
    assert result["reason"] == (
        "Dead air: 100.0% (pass, threshold 100.0%). "
        f"Max silence gap: {duration * 1000.0:.0f}ms "
        f"(pass, threshold {duration * 1000.0:.0f}ms)."
    )


@pytest.mark.parametrize(
    "sample_count,silent_indices,silent_samples,gap_samples",
    [
        pytest.param(8000, [0, 1], 600, 600, id="leading-gap"),
        pytest.param(8000, [19, 20], 600, 600, id="trailing-gap"),
        pytest.param(8000, [10, 11], 800, 800, id="interior-gap"),
        pytest.param(8000, [0, 1, 10, 19, 20], 1600, 600, id="separate-gaps"),
        pytest.param(8399, [20], 599, 599, id="partial-tail-gap"),
    ],
)
def test_silent_frame_runs_use_real_centered_coverage(
    sample_count, silent_indices, silent_samples, gap_samples
):
    import numpy as np

    # Control only classification: the real WAV decoder supplies sample coverage.
    # At 8000 Hz the 100 ms RMS window has a 400-sample hop and is centered.
    rms = np.full((1, sample_count // 400 + 1), 0.5, dtype="float32")
    rms[0, silent_indices] = 0.0
    with patch(
        "tfc.utils.storage.audio_bytes_from_url_or_base64",
        return_value=_silent_wav_bytes(sample_count),
    ), patch("librosa.feature.rms", return_value=rms):
        out = preprocess_inputs(
            "dead_air_detection",
            {"input_audio": "https://example.com/frame-coverage.wav"},
        )
    assert "_dead_air_error" not in out
    assert out["_dead_air_percentage"] == pytest.approx(
        silent_samples / sample_count * 100.0
    )
    assert out["_dead_air_max_gap_ms"] == pytest.approx(gap_samples / 8000 * 1000.0)
