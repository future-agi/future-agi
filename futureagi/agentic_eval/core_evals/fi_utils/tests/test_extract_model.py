from unittest.mock import patch

from agentic_eval.core_evals.fi_utils.extract_model import _extract_model_name


def _azure_serialized(last_id: str) -> dict:
    return {
        "type": "not_implemented",
        "id": ["langchain_community", "chat_models", last_id],
        "repr": "",
        "kwargs": {},
    }


class TestExtractModelName:
    """Regression tests for _extract_model_name with None invocation_params (issue #644)."""

    def test_azure_chat_openai_none_invocation_params(self):
        serialized = _azure_serialized("AzureChatOpenAI")
        with (
            patch(
                "agentic_eval.core_evals.fi_utils.extract_model._extract_model_by_key",
                return_value=None,
            ),
            patch(
                "agentic_eval.core_evals.fi_utils.extract_model._extract_model_by_pattern",
                return_value=None,
            ),
        ):
            result = _extract_model_name(serialized, invocation_params=None)
        assert result is None

    def test_azure_openai_returns_model_name_from_invocation_params(self):
        serialized = _azure_serialized("AzureOpenAI")
        with (
            patch(
                "agentic_eval.core_evals.fi_utils.extract_model._extract_model_by_key",
                return_value=None,
            ),
            patch(
                "agentic_eval.core_evals.fi_utils.extract_model._extract_model_by_pattern",
                return_value=None,
            ),
        ):
            result = _extract_model_name(
                serialized, invocation_params={"model_name": "gpt-4-turbo"}
            )
        assert result == "gpt-4-turbo"
