"""R05/R06: bounded decode, input failure precedence and target selection."""

import io
from unittest.mock import Mock

import numpy as np
import pytest

from ee.voice.services.audio_analysis import decode
from ee.voice.services.audio_analysis.errors import AudioDeterministicError


def wav(y, sr=16000, **kwargs):
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, y, sr, format="WAV", subtype="FLOAT", **kwargs)
    return buf.getvalue()


@pytest.mark.parametrize(
    "data,reason",
    [
        (b"", "invalid_audio"),
        (b"RIFFbad", "invalid_audio"),
        (b"ID3\0\0truncated", "invalid_audio"),
    ],
)
def test_invalid_container(data, reason):
    with pytest.raises(AudioDeterministicError, match=reason):
        decode.decode_audio(data)


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_nonfinite_sample_invalid_audio_not_dropped(value):
    y = np.ones(16000, dtype=np.float32) * 0.1
    y[8000] = value
    with pytest.raises(AudioDeterministicError, match="invalid_audio"):
        decode.decode_audio(wav(y))


@pytest.mark.parametrize(
    "samples,reason",
    [(0, "empty_audio"), (16000, "no_voice"), (14400, "insufficient_audio")],
)
def test_empty_silence_short_distinct(samples, reason):
    with pytest.raises(AudioDeterministicError, match=reason):
        decode.decode_audio(wav(np.zeros(samples)))


@pytest.mark.parametrize(
    "sr,allowed", [(7999, False), (8000, True), (48000, True), (48001, False)]
)
def test_sample_rate_endpoints(sr, allowed):
    data = wav(np.full(sr, 0.1), sr)
    if allowed:
        assert decode.decode_audio(data).sample_rate_hz == sr
    else:
        with pytest.raises(AudioDeterministicError, match="unsupported_sample_rate"):
            decode.decode_audio(data)


def test_three_channels_unsupported_format():
    with pytest.raises(AudioDeterministicError, match="unsupported_format"):
        decode.decode_audio(wav(np.ones((16000, 3))))


def test_opposite_phase_stereo_never_reaches_downmix():
    y = np.sin(2 * np.pi * 220 * np.arange(16000) / 16000).astype(np.float32)
    data = wav(np.column_stack((y, -y)))
    result = decode.decode_audio(data, channel_index=1, expected_channels=2)
    assert result.waveform.ndim == 1
    np.testing.assert_array_equal(result.waveform, -y)
    with pytest.raises(AudioDeterministicError, match="unknown_agent_track"):
        decode.decode_audio(data)


@pytest.mark.parametrize(
    "frames,sr,reason",
    [
        (28_800_000, 48000, None),
        (28_800_001, 48000, "limit_exceeded"),
        (4_800_000, 8000, None),
        (4_800_001, 8000, "limit_exceeded"),
    ],
)
def test_decoded_and_duration_limits_header_only(frames, sr, reason):
    header = Mock(
        samplerate=sr, channels=1, frames=frames, format="WAV", subtype="PCM_16"
    )
    if reason:
        with pytest.raises(AudioDeterministicError, match=reason):
            decode.validate_header(header)
    else:
        decode.validate_header(header)
    header.read.assert_not_called()


def test_encoded_limit_exact_and_one_over():
    decode.validate_encoded_size(decode.MAX_ENCODED_BYTES)
    with pytest.raises(AudioDeterministicError, match="limit_exceeded"):
        decode.validate_encoded_size(decode.MAX_ENCODED_BYTES + 1)


def test_libsndfile_supports_mp3():
    import soundfile as sf

    if tuple(map(int, sf.__libsndfile_version__.split("."))) < (1, 1, 0):
        pytest.skip("libsndfile < 1.1.0 has no MP3 decoder")
    assert "MP3" in sf.available_formats()
    buf = io.BytesIO()
    sf.write(buf, np.full(32000, 0.1), 16000, format="MP3")
    assert decode.decode_audio(buf.getvalue()).sample_rate_hz == 16000


def test_fetch_rejects_wrong_key_before_head():
    s3 = Mock()
    with pytest.raises(AudioDeterministicError, match="analysis_error"):
        decode.fetch_audio(
            s3, bucket="recordings", key="https://foreign/a", call_id="call"
        )
    s3.head_object.assert_not_called()


def test_fetch_checks_version_and_closes_stream():
    data = wav(np.full(16000, 0.1))
    body = io.BytesIO(data)
    s3 = Mock()
    s3.head_object.return_value = {"ContentLength": len(data), "ETag": "etag"}
    s3.get_object.return_value = {"Body": body, "ETag": "etag"}
    assert (
        decode.fetch_audio(
            s3,
            bucket="recordings",
            key="call-recordings/call/a",
            call_id="call",
            expected_version="etag",
        )
        == data
    )
    assert body.closed
    assert s3.get_object.call_args.kwargs["IfMatch"] == "etag"


def test_revoked_credentials_sanitized():
    from botocore.exceptions import ClientError

    s3 = Mock()
    s3.head_object.side_effect = ClientError(
        {
            "Error": {"Code": "AccessDenied", "Message": "secret"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        "HeadObject",
    )
    with pytest.raises(AudioDeterministicError, match="provider_access_denied") as exc:
        decode.fetch_audio(
            s3, bucket="recordings", key="call-recordings/call/a", call_id="call"
        )
    assert "secret" not in str(exc.value)


def test_limit_check_precedes_data_read(monkeypatch):
    import soundfile as sf

    header = Mock(
        samplerate=48000, channels=1, frames=28_800_001, format="WAV", subtype="PCM_16"
    )
    handle = Mock()
    handle.__enter__ = Mock(return_value=header)
    handle.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(sf, "SoundFile", lambda *args: handle)
    with pytest.raises(AudioDeterministicError, match="limit_exceeded"):
        decode.decode_audio(b"header-stub")
    header.read.assert_not_called()


def test_chunk_read_limit_cannot_be_bypassed(monkeypatch):
    import soundfile as sf

    header = Mock(
        samplerate=16000, channels=1, frames=16000, format="WAV", subtype="FLOAT"
    )
    header.read.side_effect = [
        np.ones((16000, 1), dtype=np.float32),
        np.ones((1, 1), dtype=np.float32),
    ]
    handle = Mock()
    handle.__enter__ = Mock(return_value=header)
    handle.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(sf, "SoundFile", lambda *args: handle)
    monkeypatch.setattr(decode, "MAX_TARGET_SAMPLES", 16000)
    with pytest.raises(AudioDeterministicError, match="limit_exceeded"):
        decode.decode_audio(b"compressed-header-stub")


def test_fetch_version_mismatch_reheads_once():
    s3 = Mock()
    s3.head_object.return_value = {"ContentLength": 10, "ETag": "new"}
    sleep = Mock()
    with pytest.raises(AudioDeterministicError, match="storage_unavailable"):
        decode.fetch_audio(
            s3,
            bucket="recordings",
            key="call-recordings/call/a",
            call_id="call",
            expected_version="old",
            sleep=sleep,
        )
    assert s3.head_object.call_count == 2
    sleep.assert_called_once_with(2)
    s3.get_object.assert_not_called()


def test_fetch_lying_head_closes_body():
    s3 = Mock()
    body = io.BytesIO(b"too long")
    s3.head_object.return_value = {"ContentLength": 3}
    s3.get_object.return_value = {"Body": body}
    with pytest.raises(AudioDeterministicError, match="storage_unavailable"):
        decode.fetch_audio(
            s3, bucket="recordings", key="call-recordings/call/a", call_id="call"
        )
    assert body.closed


def test_white_noise_has_no_noise_floor_and_pitch_is_spurious():
    """Unvoiced broadband noise has no pauses; pYIN still invents a pitch.

    Measured on the seed-2094 10 s / 16 kHz fixture: the unchanged
    `pyin_c2c6_v1` estimator reports ~78 Hz over ~3.3 s of voiced coverage, so
    the 0.25 s gate does not suppress it. A `no_voiced_frames` assertion is
    therefore not achievable without changing the pinned estimator. What holds
    is the SNR side: no pauses means `no_noise_floor`. Recorded deviation vs
    AC05 wording; see SLICE1-NOTES.md.
    """
    from ee.voice.services.audio_analysis.pitch_snr import analyze_pitch_snr

    decoded = decode.decode_audio(
        wav(np.random.default_rng(2094).normal(0, 0.1, 160000))
    )
    pitch, snr = analyze_pitch_snr(decoded.waveform, decoded.sample_rate_hz)
    assert pitch["state"] == "available" and 50 < pitch["value"] < 150
    assert snr["reason"] == "no_noise_floor"
