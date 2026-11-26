"""A prompt message with an audio_url item names a missing ffmpeg instead of
crashing on a None content item or sending the audio unconverted."""

import wave
from io import BytesIO
from types import SimpleNamespace

import pytest

from tfc.utils.media_tools import MediaToolUnavailable

pytestmark = pytest.mark.unit

AUDIO_URL = "https://media.example.test/clip.wav"
NAMED = "ffmpeg is not installed in this backend image"


def _wav() -> bytes:
    out = BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 800)
    return out.getvalue()


@pytest.fixture
def audio_url_without_ffmpeg(tmp_path, monkeypatch):
    """Empty PATH, an audio-capable model and a synthetic fetched WAV."""
    from model_hub.views import prompt_template

    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(prompt_template, "get_model_mode", lambda model: "audio")
    monkeypatch.setattr(
        prompt_template,
        "safe_fetch",
        lambda url, **kwargs: SimpleNamespace(
            content=_wav(), raise_for_status=lambda: None
        ),
    )


def _audio_item():
    return {"type": "audio_url", "audio_url": {"url": AUDIO_URL}}


def test_handle_media_raises_the_named_error(audio_url_without_ffmpeg):
    from model_hub.views.prompt_template import handle_media

    with pytest.raises(MediaToolUnavailable, match=f"^{NAMED}"):
        handle_media(_audio_item(), "gpt-4o-audio-preview")


def test_replace_variables_raises_the_named_error(audio_url_without_ffmpeg):
    """Prompt workbench and the async prompt runner: the run's output shows
    str(error); it used to be "'NoneType' object has no attribute 'keys'"."""
    from model_hub.views.prompt_template import replace_variables

    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "Transcribe {{name}}"}, _audio_item()],
        }
    ]
    with pytest.raises(MediaToolUnavailable, match=f"^{NAMED}"):
        replace_variables(messages, {"name": "clip"}, "gpt-4o-audio-preview")


def test_run_prompt_list_content_raises_the_named_error(audio_url_without_ffmpeg):
    """Dataset run-prompt columns: the item used to be sent unconverted."""
    from model_hub.views.run_prompt import process_list_content

    with pytest.raises(MediaToolUnavailable, match=f"^{NAMED}"):
        process_list_content([_audio_item()], {}, {}, 0, "gpt-4o-audio-preview")


def test_agent_playground_prompt_node_raises_the_named_error(
    audio_url_without_ffmpeg,
):
    """Agent playground prompt nodes: the item used to be sent unconverted."""
    from agent_playground.services.engine.runners.llm_prompt import LLMPromptRunner

    messages = [{"role": "user", "content": [_audio_item()]}]
    with pytest.raises(MediaToolUnavailable, match=f"^{NAMED}"):
        LLMPromptRunner()._render_messages(messages, {}, "gpt-4o-audio-preview")


def test_other_audio_errors_keep_the_old_fallback(monkeypatch):
    """Only the missing tool is raised; a failed download still logs and
    returns None as before."""
    from model_hub.views import prompt_template

    monkeypatch.setattr(prompt_template, "get_model_mode", lambda model: "audio")

    def failing_fetch(url, **kwargs):
        raise ValueError("blocked host")

    monkeypatch.setattr(prompt_template, "safe_fetch", failing_fetch)
    assert prompt_template.handle_media(_audio_item(), "gpt-4o-audio-preview") is None


@pytest.fixture
def stt_audio_url_without_ffmpeg(audio_url_without_ffmpeg, monkeypatch):
    """The same setup with a speech-to-text model."""
    from model_hub.views import prompt_template

    monkeypatch.setattr(prompt_template, "get_model_mode", lambda model: "stt")


def test_handle_media_passes_an_stt_audio_item_through(stt_audio_url_without_ffmpeg):
    """Speech-to-text reads the audio_url item itself and sends the audio
    unconverted without ffmpeg, so the item is kept, not failed."""
    from model_hub.views.prompt_template import handle_media

    item = _audio_item()
    assert handle_media(item, "whisper-1") == item


def test_agent_playground_stt_node_keeps_the_audio_url(stt_audio_url_without_ffmpeg):
    """An agent-playground prompt node on whisper-1 still renders the original
    audio_url item, as it did before the named error, so it transcribes."""
    from agent_playground.services.engine.runners.llm_prompt import LLMPromptRunner

    messages = [{"role": "user", "content": [_audio_item()]}]
    rendered = LLMPromptRunner()._render_messages(messages, {}, "whisper-1")
    assert rendered == [{"role": "user", "content": [_audio_item()]}]


def test_replace_variables_keeps_an_stt_audio_item(stt_audio_url_without_ffmpeg):
    """Prompt workbench on an STT model: the run gets the audio item, not a
    None that crashed it before."""
    from model_hub.views.prompt_template import replace_variables

    messages = [{"role": "user", "content": [_audio_item()]}]
    assert replace_variables(messages, {}, "whisper-1") == messages
