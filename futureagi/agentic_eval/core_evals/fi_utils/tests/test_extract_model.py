from agentic_eval.core_evals.fi_utils.extract_model import _extract_model_name


def _azure_serialized(last_id: str) -> dict:
    return {
        "type": "not_implemented",
        "id": ["langchain", "chat_models", last_id],
        "kwargs": {},
        "repr": f"{last_id}()",
    }


def test_azure_chat_openai_without_invocation_params_does_not_crash():
    assert _extract_model_name(_azure_serialized("AzureChatOpenAI")) is None


def test_azure_chat_openai_reads_model_from_invocation_params():
    assert (
        _extract_model_name(
            _azure_serialized("AzureChatOpenAI"),
            invocation_params={"model": "gpt-4o"},
        )
        == "gpt-4o"
    )


def test_azure_openai_reads_model_name_from_invocation_params():
    assert (
        _extract_model_name(
            _azure_serialized("AzureOpenAI"),
            invocation_params={"model_name": "gpt-4-turbo"},
        )
        == "gpt-4-turbo"
    )
