"""Speech-to-text paths without ffmpeg, in a directory backend CI collects.

``futureagi/pytest.ini`` skips ``agentic_eval`` (it has its own pytest.ini and
no CI job), so these call sites are pinned here.
"""

import base64
import io
import wave
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tfc.utils.media_tools import MediaToolUnavailable

pytestmark = pytest.mark.unit

NAMED = "ffmpeg is not installed in this backend image"


def _wav_bytes(seconds: float = 0.2) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x01\x00" * int(8000 * seconds))
    return out.getvalue()


@pytest.fixture
def no_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))


def _logged(warning) -> list[tuple]:
    return [(c.args[0], c.kwargs.get("reason", "")) for c in warning.call_args_list]


def test_stt_normalize_sends_audio_unconverted_and_says_why(no_ffmpeg):
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils import (
        audio_processor,
    )

    wav = _wav_bytes()
    with (
        # Loading pads short clips with librosa (the `audio` extra); the
        # conversion step under test starts from the loaded bytes.
        patch.object(audio_processor, "audio_bytes_from_url_or_base64", lambda a: wav),
        patch.object(audio_processor.logger, "warning") as warning,
    ):
        audio_bytes, ext = audio_processor.AudioProcessor.normalize_audio_for_stt(
            base64.b64encode(wav).decode()
        )
    assert audio_bytes == wav
    assert ext == "wav"
    assert any(
        event == "stt_audio_not_converted" and reason.startswith(NAMED)
        for event, reason in _logged(warning)
    )


def test_litellm_transcription_sends_audio_unconverted_and_says_why(no_ffmpeg):
    from agentic_eval.core_evals.run_prompt import litellm_response

    wav = _wav_bytes()
    run_prompt = SimpleNamespace(
        model="whisper-1",
        _get_input_audio_from_messages=lambda: base64.b64encode(wav).decode(),
    )
    sent = {}

    class Stop(Exception):
        pass

    def fake_transcription(*, model, file, api_key, drop_params):
        sent["name"] = file.name
        sent["bytes"] = file.read()
        raise Stop  # the provider call is all this test needs

    with (
        patch.object(litellm_response, "audio_bytes_from_url_or_base64", lambda a: wav),
        patch.object(litellm_response.logger, "warning") as warning,
        patch.object(litellm_response.litellm, "transcription", fake_transcription),
        pytest.raises(Stop),
    ):
        litellm_response.RunPrompt._transcription_response(run_prompt, 0.0, "fake")
    assert sent == {"name": "audio.wav", "bytes": wav}
    assert any(
        event == "stt_audio_not_converted" and reason.startswith(NAMED)
        for event, reason in _logged(warning)
    )


def test_deepgram_without_ffmpeg_names_the_missing_tool(no_ffmpeg):
    """Before: "Failed to convert audio for Deepgram Flux" around pydub's
    FileNotFoundError."""
    from agentic_eval.core_evals.run_prompt.other_services.deepgram_response import (
        _load_and_convert_audio,
    )

    run_prompt = SimpleNamespace(
        _get_input_audio_from_messages=lambda: base64.b64encode(_wav_bytes()).decode()
    )
    with (
        patch("pydub.AudioSegment.from_file") as from_file,
        pytest.raises(MediaToolUnavailable) as caught,
    ):
        _load_and_convert_audio(run_prompt)
    assert str(caught.value).startswith(NAMED)
    assert "Deepgram speech-to-text" in str(caught.value)
    from_file.assert_not_called()
