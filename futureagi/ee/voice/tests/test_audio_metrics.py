from io import BytesIO
from math import gcd

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import resample_poly

from ee.voice.services.audio_metrics import analyze_audio


def wav(noise=0.005, stereo=False):
    sample_rate = 16000
    time = np.arange(sample_rate * 2) / sample_rate
    signal = np.sin(2 * np.pi * 220 * time) * 0.3
    rng = np.random.default_rng(42)
    samples = signal + rng.normal(0, noise, signal.shape)
    samples[: sample_rate // 4] = rng.normal(0, noise, sample_rate // 4)
    if stereo:
        samples = np.column_stack((samples, samples))
    buffer = BytesIO()
    sf.write(buffer, samples, sample_rate, format="WAV")
    return buffer.getvalue()


def test_pitch_is_close_to_220_hz():
    result = analyze_audio(wav())
    assert result.average_pitch_hz == pytest.approx(220, rel=0.05)


def test_more_noise_reduces_snr():
    quiet = analyze_audio(wav(0.005))
    noisy = analyze_audio(wav(0.05))
    assert quiet.estimated_snr_db is not None
    assert noisy.estimated_snr_db is not None
    assert noisy.estimated_snr_db < quiet.estimated_snr_db


def test_silence_returns_missing_metrics():
    buffer = BytesIO()
    sf.write(buffer, np.zeros(16000), 16000, format="WAV")
    result = analyze_audio(buffer.getvalue())
    assert result.average_pitch_hz is None
    assert result.estimated_snr_db is None


def test_invalid_bytes_raise_value_error():
    with pytest.raises(ValueError, match="decode"):
        analyze_audio(b"not an audio recording")


def test_empty_bytes_raise_value_error():
    with pytest.raises(ValueError, match="empty"):
        analyze_audio(b"")


def test_non_bytes_input_raises_type_error():
    with pytest.raises(TypeError, match="bytes-like"):
        analyze_audio("not bytes")


def test_stereo_is_supported_and_results_are_repeatable():
    recording = wav(stereo=True)
    first = analyze_audio(recording)
    assert first == analyze_audio(recording)
    assert first.average_pitch_hz is not None
    for value in (first.average_pitch_hz, first.estimated_snr_db):
        assert value is None or np.isfinite(value)


def test_short_audio_returns_missing_metrics_without_raising():
    buffer = BytesIO()
    sf.write(buffer, np.array([0.2, 0.1, 0.0]), 16000, format="WAV")

    result = analyze_audio(buffer.getvalue())

    assert result.average_pitch_hz is None
    assert result.estimated_snr_db is None


@pytest.mark.parametrize("sample_rate", [8000, 16000, 48000, 192000])
def test_equivalent_recordings_keep_pitch_and_snr_verdict(sample_rate):
    source_rate = 48000
    time = np.arange(source_rate) / source_rate
    rng = np.random.default_rng(42)
    samples = 0.3 * np.sin(2 * np.pi * 220 * time)
    samples += rng.normal(0, 0.005, samples.shape)
    samples[: source_rate // 4] = rng.normal(0, 0.005, source_rate // 4)
    source_buffer = BytesIO()
    sf.write(source_buffer, samples, source_rate, format="WAV")
    reference = analyze_audio(source_buffer.getvalue())

    divisor = gcd(source_rate, sample_rate)
    converted = resample_poly(samples, sample_rate // divisor, source_rate // divisor)
    buffer = BytesIO()
    sf.write(buffer, converted, sample_rate, format="WAV")
    result = analyze_audio(buffer.getvalue())

    assert result.average_pitch_hz == pytest.approx(220, rel=0.05)
    assert result.estimated_snr_db is not None
    assert result.estimated_snr_db >= 6
    # Downsampling changes noise bandwidth, so the estimate need not be identical.
    assert result.estimated_snr_db == pytest.approx(reference.estimated_snr_db, abs=4)
