from __future__ import annotations

import io
import math

import numpy as np
import pytest
import soundfile as sf

from ee.voice.services.audio_metrics import analyze_audio


def _synth_wav_bytes(
    *,
    frequency_hz: float = 220.0,
    duration_sec: float = 2.0,
    sample_rate: int = 22050,
    noise_amplitude: float = 0.0,
    stereo: bool = False,
) -> bytes:
    n_samples = int(duration_sec * sample_rate)

    t = np.linspace(
        0,
        duration_sec,
        n_samples,
        endpoint=False,
    )

    signal = 0.5 * np.sin(2 * math.pi * frequency_hz * t)

    rng = np.random.default_rng(12345)

    if noise_amplitude > 0:
        signal = signal + (noise_amplitude * rng.normal(size=n_samples))

    signal = signal.astype(np.float32)

    if stereo:
        signal = np.column_stack(
            (
                signal,
                signal,
            )
        )

    buffer = io.BytesIO()

    sf.write(
        buffer,
        signal,
        sample_rate,
        format="WAV",
    )

    return buffer.getvalue()


def test_220_hz_sine_wave_reports_expected_pitch():
    audio_bytes = _synth_wav_bytes(
        frequency_hz=220.0,
        noise_amplitude=0.01,
    )

    metrics = analyze_audio(audio_bytes)

    assert metrics.average_pitch_hz is not None
    assert metrics.average_pitch_hz == pytest.approx(
        220.0,
        rel=0.05,
    )


def test_stronger_noise_produces_lower_estimated_snr():
    low_noise_audio = _synth_wav_bytes(
        noise_amplitude=0.01,
    )

    high_noise_audio = _synth_wav_bytes(
        noise_amplitude=0.20,
    )

    low_noise_metrics = analyze_audio(low_noise_audio)
    high_noise_metrics = analyze_audio(high_noise_audio)

    assert low_noise_metrics.estimated_snr_db is not None
    assert high_noise_metrics.estimated_snr_db is not None

    assert high_noise_metrics.estimated_snr_db < low_noise_metrics.estimated_snr_db


def test_silence_returns_none_for_all_metrics():
    sample_rate = 22050
    silence = np.zeros(
        sample_rate,
        dtype=np.float32,
    )

    buffer = io.BytesIO()

    sf.write(
        buffer,
        silence,
        sample_rate,
        format="WAV",
    )

    metrics = analyze_audio(buffer.getvalue())

    assert metrics.average_pitch_hz is None
    assert metrics.estimated_snr_db is None


def test_malformed_audio_raises_value_error():
    with pytest.raises(
        ValueError,
        match="Could not decode audio bytes",
    ):
        analyze_audio(b"this is not valid audio data")


def test_numeric_results_are_always_finite():
    audio_bytes = _synth_wav_bytes(
        frequency_hz=220.0,
        noise_amplitude=0.05,
    )

    metrics = analyze_audio(audio_bytes)

    for value in (
        metrics.average_pitch_hz,
        metrics.estimated_snr_db,
    ):
        if value is not None:
            assert math.isfinite(value)


def test_stereo_audio_is_safely_downmixed():
    audio_bytes = _synth_wav_bytes(
        frequency_hz=220.0,
        noise_amplitude=0.01,
        stereo=True,
    )

    metrics = analyze_audio(audio_bytes)

    assert metrics.average_pitch_hz is not None
    assert math.isfinite(metrics.average_pitch_hz)


def test_analysis_is_deterministic():
    audio_bytes = _synth_wav_bytes(
        frequency_hz=220.0,
        noise_amplitude=0.05,
    )

    first = analyze_audio(audio_bytes)
    second = analyze_audio(audio_bytes)

    assert first == second
