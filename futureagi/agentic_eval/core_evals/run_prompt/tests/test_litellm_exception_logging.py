"""Regression tests for LiteLLM exception logging behavior."""

from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_format_audio_output_logs_token_counting_failure():
    """The real audio formatting path should preserve output when token counting fails."""
    from agentic_eval.core_evals.run_prompt import litellm_response

    run_prompt = litellm_response.RunPrompt.__new__(litellm_response.RunPrompt)
    run_prompt.model = "gpt-4o"

    with (
        patch.object(litellm_response, "upload_audio_to_s3", return_value="https://example.com/audio.mp3"),
        patch.object(litellm_response, "count_tiktoken_tokens", side_effect=RuntimeError("counter unavailable")),
        patch.object(litellm_response.logger, "warning") as warning,
    ):
        result = run_prompt._format_audio_output(b"audio", 0, "hello")

    assert result[0] == "https://example.com/audio.mp3"
    assert result[1]["metadata"]["usage"]["prompt_tokens"] == 0
    warning.assert_any_call(
        "Failed to count prompt tokens for audio TTS",
        model="gpt-4o",
        error="counter unavailable",
        input_length=5,
    )
