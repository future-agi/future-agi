import base64
import io
import wave
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydub import AudioSegment

_SAMPLE_RATE = 16_000
_BYTES_PER_SECOND = _SAMPLE_RATE * 2  # mono, 16-bit


def _wav_base64(seconds: float) -> str:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(b"\x01\x00" * int(_SAMPLE_RATE * seconds))
    return base64.b64encode(output.getvalue()).decode()


def _segment_from_wav_bytes(wav_bytes: bytes) -> AudioSegment:
    """Build an AudioSegment from WAV bytes without needing ffmpeg."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        return AudioSegment(
            data=frames,
            sample_width=wav.getsampwidth(),
            frame_rate=wav.getframerate(),
            channels=wav.getnchannels(),
        )


def _convert(seconds: float) -> bytes:
    from agentic_eval.core_evals.run_prompt.other_services.deepgram_response import (
        _load_and_convert_audio,
    )

    run_prompt = SimpleNamespace(
        _get_input_audio_from_messages=lambda: _wav_base64(seconds)
    )

    def _from_file(buffer, *args, **kwargs):
        return _segment_from_wav_bytes(buffer.getvalue())

    with (
        patch(
            "agentic_eval.core_evals.run_prompt.other_services.deepgram_response."
            "audio_bytes_from_url_or_base64",
            wraps=__import__(
                "tfc.utils.storage", fromlist=["audio_bytes_from_url_or_base64"]
            ).audio_bytes_from_url_or_base64,
        ) as load_audio,
        # Simulate a slim image: any attempt to load an extra (librosa via
        # ``_ensure_min_duration``) must never be reached by this base feature.
        patch(
            "tfc.utils.lazy_extras.load_extra",
            side_effect=ImportError("audio extra not installed"),
        ) as load_extra,
        patch("pydub.AudioSegment.from_file", side_effect=_from_file),
    ):
        pcm = _load_and_convert_audio(run_prompt)

    load_audio.assert_called_once_with(
        run_prompt._get_input_audio_from_messages(), pad_silence=False
    )
    load_extra.assert_not_called()
    return pcm


def test_deepgram_audio_conversion_does_not_require_audio_extra():
    pcm = _convert(seconds=0.1)
    assert isinstance(pcm, bytes)
    assert len(pcm) % 2 == 0


def test_deepgram_short_audio_is_padded_to_minimum_duration_with_pydub():
    """Short clips keep the historical >=1s minimum without librosa.

    Before the slim split the shared loader padded to 1s via librosa; the
    Deepgram path now does the same with pydub so behaviour is unchanged on
    slim *and* full/EE images.
    """
    pcm = _convert(seconds=0.1)
    assert len(pcm) == _BYTES_PER_SECOND
    # Original 0.1s of signal is preserved, the remainder is silence.
    signal_bytes = int(_SAMPLE_RATE * 0.1) * 2
    assert pcm[:signal_bytes] == b"\x01\x00" * (signal_bytes // 2)
    assert pcm[signal_bytes:] == b"\x00" * (len(pcm) - signal_bytes)


@pytest.mark.parametrize("seconds", [1.0, 1.5])
def test_deepgram_audio_at_or_above_minimum_is_not_padded(seconds):
    pcm = _convert(seconds=seconds)
    assert len(pcm) == int(_BYTES_PER_SECOND * seconds)
