"""Tests for the audio metrics extractor (average pitch and estimated SNR).

All audio is synthesised in-memory and encoded to WAV bytes, so the tests need
no fixtures, network, or database. Noise uses a seeded generator, so runs are
reproducible.
"""

from __future__ import annotations

import dataclasses
import io
import math

import numpy as np
import pytest
import soundfile as sf

from ee.voice.services.audio_metrics import AudioMetrics, analyze_audio

SAMPLE_RATE = 16000


def _wav_bytes(samples: np.ndarray, sr: int = SAMPLE_RATE, subtype: str = "FLOAT"):
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV", subtype=subtype)
    return buf.getvalue()


def _tone(freq_hz: float, seconds: float, sr: int = SAMPLE_RATE, amp: float = 0.5):
    t = np.arange(int(seconds * sr)) / sr
    return amp * np.sin(2.0 * np.pi * freq_hz * t)


def _gated_tone(
    freq_hz: float = 220.0,
    noise_sigma: float = 0.0,
    bursts: int = 4,
    burst_seconds: float = 0.4,
    pause_seconds: float = 0.3,
    sr: int = SAMPLE_RATE,
    amp: float = 0.5,
    seed: int = 0,
):
    """Tone bursts separated by pauses, over stationary white noise.

    Stands in for speech with pauses, the shape the SNR estimator expects.
    """
    burst = _tone(freq_hz, burst_seconds, sr, amp)
    pause = np.zeros(int(pause_seconds * sr))
    parts = [pause]
    for _ in range(bursts):
        parts += [burst, pause]
    signal = np.concatenate(parts)
    rng = np.random.default_rng(seed)
    return signal + rng.normal(0.0, noise_sigma, signal.size)


def _snr_db(sigma: float) -> float:
    metrics = analyze_audio(_wav_bytes(_gated_tone(noise_sigma=sigma)))
    assert metrics.estimated_snr_db is not None
    return metrics.estimated_snr_db


def _is_finite_or_none(value) -> bool:
    return value is None or math.isfinite(value)


# --- pitch -----------------------------------------------------------------


def test_220hz_sine_pitch_within_5_percent():
    metrics = analyze_audio(_wav_bytes(_tone(220.0, 1.5)))

    assert metrics.average_pitch_hz is not None
    assert metrics.average_pitch_hz == pytest.approx(220.0, rel=0.05)


@pytest.mark.parametrize("sr", [8000, 16000, 44100])
def test_pitch_is_stable_across_sample_rates(sr):
    metrics = analyze_audio(_wav_bytes(_tone(220.0, 1.5, sr=sr), sr=sr))

    assert metrics.average_pitch_hz == pytest.approx(220.0, rel=0.05)


def test_pitch_survives_16bit_pcm_encoding():
    metrics = analyze_audio(_wav_bytes(_tone(220.0, 1.5), subtype="PCM_16"))

    assert metrics.average_pitch_hz == pytest.approx(220.0, rel=0.05)


def test_pitch_averages_voiced_frames_only():
    # Pauses are digital silence (unvoiced). Averaging them in would drag the
    # mean far below 220 Hz.
    metrics = analyze_audio(_wav_bytes(_gated_tone(220.0)))

    assert metrics.average_pitch_hz == pytest.approx(220.0, rel=0.05)


# --- SNR -------------------------------------------------------------------


def test_snr_decreases_as_noise_increases():
    snrs = [_snr_db(sigma) for sigma in (0.001, 0.01, 0.05, 0.15)]

    assert snrs == sorted(snrs, reverse=True)
    assert len(set(snrs)) == len(snrs)


def test_snr_is_close_to_the_true_ratio():
    # Tone power is 0.5**2 / 2 = 0.125; noise power is sigma**2.
    for sigma in (0.01, 0.05):
        expected_db = 10.0 * math.log10(0.125 / sigma**2)
        assert _snr_db(sigma) == pytest.approx(expected_db, abs=3.0)


def test_snr_of_clean_signal_with_digital_silence_is_finite_and_high():
    # Noise-free pauses would divide by zero without the power floor.
    snr = _snr_db(0.0)

    assert math.isfinite(snr)
    assert snr > 60.0


def test_snr_is_none_for_stationary_signal_without_a_noise_floor():
    # A continuous tone has no quiet frames to estimate noise from.
    metrics = analyze_audio(_wav_bytes(_tone(220.0, 1.5)))

    assert metrics.estimated_snr_db is None
    assert metrics.average_pitch_hz == pytest.approx(220.0, rel=0.05)


# --- silence and degenerate input -----------------------------------------


@pytest.mark.parametrize("seconds", [0.02, 1.0])
def test_silence_returns_none_metrics(seconds):
    metrics = analyze_audio(_wav_bytes(np.zeros(int(seconds * SAMPLE_RATE))))

    assert metrics == AudioMetrics(average_pitch_hz=None, estimated_snr_db=None)


def test_near_silent_noise_floor_returns_none_metrics():
    rng = np.random.default_rng(1)
    quiet = rng.normal(0.0, 1e-6, SAMPLE_RATE)  # about -120 dBFS

    metrics = analyze_audio(_wav_bytes(quiet))

    assert metrics == AudioMetrics(average_pitch_hz=None, estimated_snr_db=None)


def test_empty_waveform_returns_none_metrics():
    metrics = analyze_audio(_wav_bytes(np.zeros(0)))

    assert metrics == AudioMetrics(average_pitch_hz=None, estimated_snr_db=None)


def test_very_short_clip_does_not_raise():
    metrics = analyze_audio(_wav_bytes(_tone(220.0, 0.01)))

    assert _is_finite_or_none(metrics.average_pitch_hz)
    assert _is_finite_or_none(metrics.estimated_snr_db)


# --- undecodable input -----------------------------------------------------


@pytest.mark.parametrize(
    "bad_bytes",
    [
        b"",
        b"this is not audio",
        b"RIFF\x00\x00\x00\x00WAVEfmt garbage",
        _wav_bytes(_tone(220.0, 0.5))[:30],  # header cut off mid-stream
    ],
)
def test_undecodable_bytes_raise_value_error(bad_bytes):
    with pytest.raises(ValueError):
        analyze_audio(bad_bytes)


def test_non_bytes_input_raises_type_error():
    with pytest.raises(TypeError):
        analyze_audio("not bytes")  # type: ignore[arg-type]


# --- finiteness, stereo, determinism --------------------------------------


def test_results_are_always_finite():
    rng = np.random.default_rng(2)
    inputs = [
        _tone(220.0, 1.0),
        _gated_tone(noise_sigma=0.0),
        _gated_tone(noise_sigma=0.3),
        rng.normal(0.0, 0.1, SAMPLE_RATE),  # white noise, no pitch
        np.clip(_tone(220.0, 1.0, amp=5.0), -1.0, 1.0),  # heavily clipped
    ]

    for samples in inputs:
        metrics = analyze_audio(_wav_bytes(samples))
        assert _is_finite_or_none(metrics.average_pitch_hz)
        assert _is_finite_or_none(metrics.estimated_snr_db)


@pytest.mark.parametrize("bad_sample", [np.nan, np.inf, -np.inf])
def test_non_finite_samples_raise_value_error(bad_sample):
    # A float file with NaN/inf samples is corrupt. It is rejected up front
    # rather than letting non-finite values leak into the metrics.
    samples = _tone(220.0, 1.0)
    samples[100:200] = bad_sample

    with pytest.raises(ValueError):
        analyze_audio(_wav_bytes(samples))


def test_stereo_is_downmixed_to_mono():
    mono = _gated_tone(220.0, noise_sigma=0.01)
    stereo = np.column_stack([mono, mono])

    from_stereo = analyze_audio(_wav_bytes(stereo))
    from_mono = analyze_audio(_wav_bytes(mono))

    assert from_stereo.average_pitch_hz == pytest.approx(220.0, rel=0.05)
    assert from_stereo.average_pitch_hz == pytest.approx(from_mono.average_pitch_hz)
    assert from_stereo.estimated_snr_db == pytest.approx(from_mono.estimated_snr_db)


def test_stereo_with_silent_channel_keeps_pitch():
    tone = _gated_tone(220.0, noise_sigma=0.01)
    stereo = np.column_stack([tone, np.zeros_like(tone)])

    metrics = analyze_audio(_wav_bytes(stereo))

    assert metrics.average_pitch_hz == pytest.approx(220.0, rel=0.05)


def test_stereo_downmix_averages_channels():
    # Opposite-phase channels cancel when averaged to mono.
    tone = _tone(220.0, 1.0)
    stereo = np.column_stack([tone, -tone])

    metrics = analyze_audio(_wav_bytes(stereo))

    assert metrics == AudioMetrics(average_pitch_hz=None, estimated_snr_db=None)


def test_identical_input_gives_identical_output():
    data = _wav_bytes(_gated_tone(220.0, noise_sigma=0.02))

    assert analyze_audio(data) == analyze_audio(data)


def test_accepts_bytearray_and_memoryview():
    data = _wav_bytes(_tone(220.0, 1.0))
    expected = analyze_audio(data)

    assert analyze_audio(bytearray(data)) == expected
    assert analyze_audio(memoryview(data)) == expected


def test_audio_metrics_is_immutable():
    metrics = AudioMetrics(average_pitch_hz=220.0, estimated_snr_db=30.0)

    with pytest.raises(dataclasses.FrozenInstanceError):
        metrics.average_pitch_hz = 100.0  # type: ignore[misc]
