from __future__ import annotations

import io
import math

import numpy as np
import pytest
import soundfile as sf

from ee.voice.services.audio_metrics import analyze_audio


def _synth_wav_bytes(
    *,
    noise_amplitude: float = 0.0,
    include_noise_only_prefix: bool = False,
    stereo: bool = False,
) -> bytes:
    sample_rate = 16_000
    duration_seconds = 2.0
    sample_count = int(sample_rate * duration_seconds)
    times = np.arange(sample_count) / sample_rate
    signal = 0.5 * np.sin(2 * math.pi * 220 * times)
    noise = noise_amplitude * np.random.default_rng(42).standard_normal(sample_count)
    waveform = signal + noise
    if include_noise_only_prefix:
        waveform[: sample_rate // 2] = noise[: sample_rate // 2]
    if stereo:
        waveform = np.column_stack((waveform, waveform))

    buffer = io.BytesIO()
    sf.write(buffer, waveform.astype("float32"), sample_rate, format="WAV")
    return buffer.getvalue()


def _assert_finite_metrics(metrics) -> None:
    for value in (metrics.average_pitch_hz, metrics.estimated_snr_db):
        if value is not None:
            assert math.isfinite(value)


def test_reports_pitch_for_a_220_hz_sine_wave():
    metrics = analyze_audio(_synth_wav_bytes(noise_amplitude=0.005))

    assert metrics.average_pitch_hz == pytest.approx(220, rel=0.05)
    _assert_finite_metrics(metrics)


def test_stronger_noise_lowers_estimated_snr():
    low_noise = analyze_audio(
        _synth_wav_bytes(noise_amplitude=0.005, include_noise_only_prefix=True)
    )
    high_noise = analyze_audio(
        _synth_wav_bytes(noise_amplitude=0.15, include_noise_only_prefix=True)
    )

    assert low_noise.estimated_snr_db is not None
    assert high_noise.estimated_snr_db is not None
    assert high_noise.estimated_snr_db < low_noise.estimated_snr_db
    _assert_finite_metrics(low_noise)
    _assert_finite_metrics(high_noise)


def test_silence_has_no_measurements():
    sample_rate = 16_000
    buffer = io.BytesIO()
    sf.write(buffer, np.zeros(sample_rate, dtype="float32"), sample_rate, format="WAV")
    metrics = analyze_audio(buffer.getvalue())

    assert metrics.average_pitch_hz is None
    assert metrics.estimated_snr_db is None


def test_malformed_audio_raises_value_error():
    with pytest.raises(ValueError, match="Could not decode audio bytes"):
        analyze_audio(b"not audio")


def test_stereo_audio_is_downmixed_safely():
    metrics = analyze_audio(_synth_wav_bytes(noise_amplitude=0.005, stereo=True))

    assert metrics.average_pitch_hz == pytest.approx(220, rel=0.05)
    _assert_finite_metrics(metrics)


def test_analysis_is_deterministic_for_the_same_input():
    audio = _synth_wav_bytes(noise_amplitude=0.005, include_noise_only_prefix=True)

    assert analyze_audio(audio) == analyze_audio(audio)
