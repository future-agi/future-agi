import base64
import io
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _short_wav_base64() -> str:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x00" * 1_600)
    return base64.b64encode(output.getvalue()).decode()


def test_deepgram_audio_conversion_does_not_require_audio_extra():
    from agentic_eval.core_evals.run_prompt.other_services.deepgram_response import (
        _load_and_convert_audio,
    )

    run_prompt = SimpleNamespace(
        _get_input_audio_from_messages=lambda: _short_wav_base64()
    )
    audio = MagicMock()
    audio.set_channels.return_value = audio
    audio.set_frame_rate.return_value = audio
    audio.set_sample_width.return_value = audio
    audio.raw_data = b"\x00\x00" * 1_600
    with patch(
        "agentic_eval.core_evals.run_prompt.other_services.deepgram_response."
        "audio_bytes_from_url_or_base64",
        wraps=__import__(
            "tfc.utils.storage", fromlist=["audio_bytes_from_url_or_base64"]
        ).audio_bytes_from_url_or_base64,
    ) as load_audio, patch("pydub.AudioSegment.from_file", return_value=audio):
        pcm = _load_and_convert_audio(run_prompt)

    load_audio.assert_called_once_with(
        run_prompt._get_input_audio_from_messages(), pad_silence=False
    )
    assert len(pcm) == 3_200
