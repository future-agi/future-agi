"""Error localization on audio names a missing ffmpeg instead of failing as
"could not be chunked" or "invalid request"."""

import asyncio
import wave
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from ee.evals.localizer.agentcc_transport import GatewayConfig, MediaRegistry
from ee.evals.localizer.claude_harness import ClaudeAgentLocalizer, LocalizationCase
from ee.evals.localizer.error_localizer import ErrorLocalizer, _create_audio_segments
from tfc.utils.media_tools import MediaToolUnavailable

pytestmark = pytest.mark.unit


def _wav() -> bytes:
    out = BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8000)
    return out.getvalue()


@pytest.fixture
def no_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))


def _kwargs():
    return {
        "eval_name": "Tone",
        "rule_prompt": "The {{output}} must be calm.",
        "input": {"output": _wav()},
        "input_type": {"output": "audio"},
        "evaluation_result": "Failed",
        "evaluation_explanation": "The caller sounds upset.",
        "choices": [],
    }


def test_audio_segments_raise_the_named_error(no_ffmpeg):
    with pytest.raises(MediaToolUnavailable) as caught:
        _create_audio_segments(_wav())
    assert str(caught.value).startswith("ffprobe is not installed") or str(
        caught.value
    ).startswith("ffmpeg is not installed")


@patch("ee.evals.localizer.error_localizer.asyncio.run")
def test_legacy_localizer_skips_with_the_named_reason(mock_run, no_ffmpeg):
    el = ErrorLocalizer(**_kwargs())
    el._llm = MagicMock()
    result = el._localize(_wav(), "output", "audio")
    assert result.analysis == {}
    assert result.selected_key == "output"
    assert result.skip_reason.startswith(
        "ffmpeg is not installed in this backend image"
    )
    mock_run.assert_not_called()


async def test_agent_tool_reports_the_named_error(no_ffmpeg):
    case = LocalizationCase(ClaudeAgentLocalizer(**_kwargs()), MediaRegistry())
    inspect = next(t for t in case.tools() if t.name == "inspect_input")
    reply = await inspect.handler({"key": "output"})
    assert reply["is_error"] is True
    assert "ffmpeg is not installed in this backend image" in str(reply["content"])
    assert isinstance(case.media_tool_error, MediaToolUnavailable)


def test_no_utterances_is_reported_before_the_missing_tool(no_ffmpeg):
    """Rebuilding the image cannot fix a call with no stored utterances."""
    from ee.evals.localizer.conversation_audio import create_utterance_segments

    with pytest.raises(ValueError, match="^No conversation utterances available$"):
        create_utterance_segments(_wav(), [])


@pytest.mark.parametrize("ending", ["success", "max_turns", "timeout"])
async def test_run_without_findings_fails_with_the_named_error(monkeypatch, ending):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    claude_agent_sdk = pytest.importorskip("claude_agent_sdk")
    from ee.evals.localizer.agentcc_transport import AgentCCTransport

    class Client:
        def __init__(self, options):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def query(self, prompt):
            pass

        async def receive_response(self):
            if ending == "timeout":
                await asyncio.sleep(10)
            yield SimpleNamespace(
                is_error=False,
                subtype="error_max_turns" if ending == "max_turns" else "success",
                terminal_reason="completed",
            )

    @asynccontextmanager
    async def no_network(self):
        yield self

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", Client)
    monkeypatch.setattr(claude_agent_sdk, "ResultMessage", SimpleNamespace)
    monkeypatch.setattr(AgentCCTransport, "serve", no_network)
    missing = MediaToolUnavailable("error localization of call audio")
    monkeypatch.setattr(LocalizationCase, "media_tool_error", missing)

    with pytest.raises(MediaToolUnavailable) as caught:
        await ClaudeAgentLocalizer(**_kwargs())._run(
            GatewayConfig(
                "http://unused.invalid",
                "fake",
                timeout=0.05 if ending == "timeout" else 5,
            )
        )
    assert caught.value is missing
