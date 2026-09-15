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


@pytest.mark.unit
def test_create_payload_logs_model_validation_failure():
    """Payload creation should continue when LiteLLM cannot validate a custom model."""
    from agentic_eval.core_evals.run_prompt import litellm_response

    run_prompt = litellm_response.RunPrompt.__new__(litellm_response.RunPrompt)
    run_prompt.model = "custom-model"
    run_prompt.output_format = None
    run_prompt.response_format = None
    run_prompt.messages = []
    run_prompt.temperature = None
    run_prompt.frequency_penalty = None
    run_prompt.presence_penalty = None
    run_prompt.max_tokens = 100
    run_prompt.top_p = None
    run_prompt.tools = None
    run_prompt.tool_choice = None
    run_prompt.reasoning_effort = None
    run_prompt.thinking_budget = None

    with (
        patch.object(litellm_response, "get_model_mode", return_value=None),
        patch.object(
            litellm_response.litellm,
            "get_max_tokens",
            side_effect=RuntimeError("unknown model"),
        ),
        patch.object(litellm_response.logger, "debug") as debug,
    ):
        payload = run_prompt._create_payload("openai", "test-key")

    assert payload["model"] == "custom-model"
    assert payload["max_tokens"] == 100
    debug.assert_called_once_with(
        "Failed to validate max tokens for model",
        model="custom-model",
        error="unknown model",
    )
