"""
Tests for the dead_air_detection preprocessor.

The preprocessor uses ``audio_bytes_from_url_or_base64`` (the canonical
audio loader in tfc/utils/storage.py) to resolve the input, then decodes
with librosa and injects ``_dead_air_*`` kwargs for the sandbox body.
The sandbox body itself is trivial threshold logic and is verified by
inspection.
"""

from __future__ import annotations

import base64
import io
import math
import shutil
from pathlib import Path
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
    for s, e in silence_segments or []:
        y[int(s * sr) : int(e * sr)] = 0.0
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


def _recording(name):
    # Pre-encoded 440 Hz, 8 kHz mono clips. Audio-enabled slim workers can
    # decode these formats but don't ship an AAC encoder or an M4A muxer.
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe required")
    return (Path(__file__).parent / "fixtures" / "dead_air" / name).read_bytes()


def test_m4a_audio_is_decoded():
    """The loader supplies the original bucket object's bytes."""
    body = _recording("silent-tail.m4a")
    with patch(
        "tfc.utils.storage.audio_bytes_from_url_or_base64",
        return_value=body,
    ):
        out = preprocess_inputs(
            "dead_air_detection",
            {"input_audio": "https://example.com/silent.m4a"},
        )
    assert "_dead_air_error" not in out
    assert out["_dead_air_percentage"] > 50.0
    assert out["_dead_air_max_gap_ms"] > 1000.0


@pytest.mark.parametrize("as_base64", [False, True], ids=["uploaded-bytes", "base64"])
def test_short_m4a_matches_original_waveform(as_base64):
    # 1600 samples do not end on an AAC frame boundary (1024 samples).
    duration = 0.2
    wav = _synth_wav_bytes(duration_sec=duration)
    m4a = _recording("tone.m4a")
    value = base64.b64encode(m4a).decode("ascii") if as_base64 else {"bytes": m4a}
    reference = preprocess_inputs("dead_air_detection", {"input_audio": {"bytes": wav}})
    actual = preprocess_inputs("dead_air_detection", {"input_audio": value})
    assert "_dead_air_error" not in actual
    assert actual["_dead_air_duration_sec"] == pytest.approx(
        reference["_dead_air_duration_sec"], abs=1 / 8000
    )
    assert actual["_dead_air_percentage"] == reference["_dead_air_percentage"]
    assert actual["_dead_air_max_gap_ms"] == reference["_dead_air_max_gap_ms"]


def test_decodable_wav_does_not_require_ffmpeg():
    wav = _synth_wav_bytes(duration_sec=0.2)
    with patch("tfc.utils.media_tools.shutil.which", return_value=None) as which:
        actual = preprocess_inputs(
            "dead_air_detection", {"input_audio": {"bytes": wav}}
        )
    assert "_dead_air_error" not in actual
    which.assert_not_called()


def test_missing_ffmpeg_keeps_original_decode_error(monkeypatch):
    librosa = pytest.importorskip("librosa")
    monkeypatch.setenv("PATH", "")
    with patch.object(
        librosa, "load", side_effect=RuntimeError("unsupported recording")
    ):
        actual = preprocess_inputs(
            "dead_air_detection", {"input_audio": {"bytes": b"recording"}}
        )
    assert actual["_dead_air_error"] == "Could not decode audio: unsupported recording"
    assert "_dead_air_percentage" not in actual


def test_raw_aac_decodes_all_frames():
    # Same tone as the M4A, but ADTS has three full AAC frames and no edit list.
    audio = _recording("tone.aac")
    actual = preprocess_inputs("dead_air_detection", {"input_audio": {"bytes": audio}})
    assert actual["_dead_air_duration_sec"] == pytest.approx(3 * 1024 / 8000)


def test_incomplete_m4a_is_not_scored():
    # The intact front index still declares 2 s; dropping the final 12 AAC
    # packets leaves 512 ms of decodable PCM, losing almost all the silence.
    audio = _recording("silent-tail.m4a")[:-48]
    actual = preprocess_inputs("dead_air_detection", {"input_audio": {"bytes": audio}})
    assert "_dead_air_percentage" not in actual
    assert "_dead_air_error" in actual


def test_missing_ffprobe_names_tool(tmp_path, monkeypatch):
    audio = _recording("tone.m4a")
    (tmp_path / "ffmpeg").symlink_to(shutil.which("ffmpeg"))
    monkeypatch.setenv("PATH", str(tmp_path))
    actual = preprocess_inputs("dead_air_detection", {"input_audio": {"bytes": audio}})
    assert "ffprobe is not installed" in actual["_dead_air_error"]
    assert "_dead_air_percentage" not in actual


def test_oversize_pcm_is_not_scored(monkeypatch):
    audio = _recording("silent-tail.m4a")
    # The compressed recording fits, but the 32 kB PCM recording does not.
    monkeypatch.setattr("tfc.utils.storage.MAX_AUDIO_FILE_SIZE", len(audio) + 1024)
    actual = preprocess_inputs("dead_air_detection", {"input_audio": {"bytes": audio}})
    assert (
        actual["_dead_air_error"]
        == "Could not decode audio: Decoded audio recording is too large"
    )
    assert "_dead_air_percentage" not in actual


def test_undecodable_bytes_return_error():
    with patch(
        "tfc.utils.storage.audio_bytes_from_url_or_base64",
        return_value=b"not audio at all",
    ):
        out = preprocess_inputs(
            "dead_air_detection",
            {"input_audio": "https://example.com/broken.m4a"},
        )
    assert out["_dead_air_error"].startswith("Could not decode audio")
    assert "_dead_air_percentage" not in out


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
