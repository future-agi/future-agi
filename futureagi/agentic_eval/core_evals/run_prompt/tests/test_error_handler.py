import pytest


class DummyLogger:
    def error(self, *args, **kwargs):
        pass


class CapturingLogger:
    def __init__(self):
        self.fields = None

    def error(self, *args, **kwargs):
        self.fields = kwargs


def _raise_masked_api_key_error():
    try:
        raise Exception(
            "Incorrect API key provided: sk-invalid. You can find your API key at "
            "https://platform.openai.com/account/api-keys"
        )
    except Exception:
        raise TypeError("instance exception may not have a separate value")


def _raise_generic_wrapper_api_key_error():
    try:
        raise Exception("Authentication failed because the configured API key is invalid")
    except Exception as exc:
        raise RuntimeError("runprompt provider call failed") from exc


@pytest.mark.unit
def test_handle_api_error_unwraps_masked_api_key_error():
    from agentic_eval.core_evals.run_prompt.error_handler import handle_api_error

    try:
        _raise_masked_api_key_error()
    except Exception as exc:
        message = handle_api_error(exc, DummyLogger())

    assert "Incorrect API key provided" in message
    assert "instance exception may not have a separate value" not in message


@pytest.mark.unit
def test_handle_api_error_uses_api_signal_not_exact_wrapper_text():
    from agentic_eval.core_evals.run_prompt.error_handler import handle_api_error

    try:
        _raise_generic_wrapper_api_key_error()
    except Exception as exc:
        message = handle_api_error(exc, DummyLogger())

    assert message == "Authentication failed because the configured API key is invalid"


@pytest.mark.unit
def test_handle_api_error_uses_litellm_body_message():
    import litellm

    from agentic_eval.core_evals.run_prompt.error_handler import handle_api_error

    exc = litellm.BadRequestError(
        message='BedrockException - {"message":"The provided model identifier is invalid."}',
        model="bad-model",
        llm_provider="bedrock",
        body={"message": "The provided model identifier is invalid."},
    )

    message = handle_api_error(exc, DummyLogger())

    assert message == "The provided model identifier is invalid."


@pytest.mark.unit
def test_handle_api_error_extracts_embedded_provider_json_message():
    from agentic_eval.core_evals.run_prompt.error_handler import handle_api_error

    exc = Exception(
        'litellm.BadRequestError: BedrockException - {"message":"The provided model identifier is invalid."}'
    )

    message = handle_api_error(exc, DummyLogger())

    assert message == "The provided model identifier is invalid."


@pytest.mark.unit
def test_handle_api_error_formats_provider_model_not_found():
    import litellm

    from agentic_eval.core_evals.run_prompt.error_handler import handle_api_error

    exc = litellm.NotFoundError(
        message=(
            "AnthropicException - "
            'b\'{"type":"error","error":{"type":"not_found_error",'
            '"message":"model: missing-model-id"},'
            '"request_id":"req_123"}\''
        ),
        model="missing-model-id",
        llm_provider="anthropic",
    )

    message = handle_api_error(exc, DummyLogger())

    assert message == (
        "Model not found: missing-model-id. Select a model available for the "
        "configured provider/account, or update the model/provider configuration."
    )


@pytest.mark.unit
def test_handle_api_error_accepts_error_context_dataclass():
    from agentic_eval.core_evals.run_prompt.error_handler import (
        ErrorContext,
        handle_api_error,
    )

    logger = CapturingLogger()
    context = ErrorContext(
        model="gpt-4o",
        temperature=0.7,
        message_count=2,
        organization_id="org-id",
    )

    handle_api_error(Exception("boom"), logger, context)

    # Dataclass is coerced to a logging dict, and unset (None) fields are
    # dropped so the output matches the legacy dict behaviour.
    assert logger.fields["model"] == "gpt-4o"
    assert logger.fields["temperature"] == 0.7
    assert logger.fields["organization_id"] == "org-id"
    assert "max_tokens" not in logger.fields
    assert "template_id" not in logger.fields


@pytest.mark.unit
def test_handle_api_error_still_accepts_plain_dict_context():
    from agentic_eval.core_evals.run_prompt.error_handler import handle_api_error

    logger = CapturingLogger()

    handle_api_error(Exception("boom"), logger, {"model": "gpt-4o", "max_tokens": 512})

    assert logger.fields["model"] == "gpt-4o"
    assert logger.fields["max_tokens"] == 512


@pytest.mark.unit
def test_litellm_try_except_unwraps_masked_api_key_error():
    from agentic_eval.core_evals.run_prompt.error_handler import litellm_try_except

    with pytest.raises(Exception) as exc_info:
        with litellm_try_except():
            _raise_masked_api_key_error()

    assert "Incorrect API key provided" in str(exc_info.value)
    assert "instance exception may not have a separate value" not in str(exc_info.value)


@pytest.mark.unit
def test_new_runprompt_path_formats_masked_api_key_error(simple_messages):
    from unittest.mock import patch

    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

    class FailingHandler:
        def execute_sync(self, streaming=False):
            _raise_masked_api_key_error()

    run_prompt = RunPrompt(
        model="gpt-4o",
        messages=simple_messages,
        organization_id="org-id",
        output_format="text",
        temperature=0.7,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        max_tokens=1000,
        top_p=1.0,
        response_format=None,
        tool_choice=None,
        tools=None,
    )

    with (
        patch(
            "agentic_eval.core_evals.run_prompt.litellm_response.LiteLLMModelManager"
        ) as mock_model_manager,
        patch(
            "agentic_eval.core_evals.run_prompt.litellm_response.ModelHandlerFactory.create_handler",
            return_value=FailingHandler(),
        ),
    ):
        manager = mock_model_manager.return_value
        manager.get_provider.return_value = "openai"
        manager.get_api_key.return_value = "sk-invalid"

        with pytest.raises(Exception) as exc_info:
            run_prompt._litellm_response_new()

    assert "Incorrect API key provided" in str(exc_info.value)
    assert "instance exception may not have a separate value" not in str(exc_info.value)
