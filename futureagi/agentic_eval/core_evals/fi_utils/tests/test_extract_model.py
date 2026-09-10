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

    def test_azure_openai_none_deployment_version_no_typeerror(self):
        """Regression test for #2158: no TypeError when deployment_version is unavailable."""
        serialized = {
            "type": "not_implemented",
            "id": ["langchain_community", "llms", "AzureOpenAI"],
            "repr": "",
            "kwargs": {"deployment_name": "my-azure-gpt4"},
        }
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
            result = _extract_model_name(serialized)
        assert result == "my-azure-gpt4"

    def test_azure_openai_with_deployment_version(self):
        serialized = {
            "type": "not_implemented",
            "id": ["langchain_community", "llms", "AzureOpenAI"],
            "repr": "",
            "kwargs": {
                "deployment_name": "my-azure-gpt4",
                "deployment_version": "2024-02-15-preview",
            },
        }
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
            result = _extract_model_name(serialized)
        assert result == "my-azure-gpt4-2024-02-15-preview"

    def test_azure_openai_with_openai_api_version(self):
        serialized = {
            "type": "not_implemented",
            "id": ["langchain_community", "llms", "AzureOpenAI"],
            "repr": "",
            "kwargs": {
                "deployment_name": "gpt-4",
                "openai_api_version": "0301",
            },
        }
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
            result = _extract_model_name(serialized)
        assert result == "gpt-4-0301"

    def test_azure_openai_empty_id_and_missing_kwargs(self):
        serialized = {
            "type": "not_implemented",
            "id": [],
            "kwargs": None,
        }
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
            result = _extract_model_name(serialized)
        assert result is None
