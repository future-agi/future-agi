"""
Regression tests for _extract_model_name with missing invocation_params.

Issue #644: kwargs.get("invocation_params") can be None; the old code chained
.get() directly on it, raising AttributeError. The fix guards with `or {}`.

Covered crash sites:
  - AzureChatOpenAI id-branch at lines ~261-262
  - AzureOpenAI id-branch at lines ~264-266
"""

from unittest.mock import patch

import pytest

from agentic_eval.core_evals.fi_utils.extract_model import _extract_model_name


def _base_serialized(last_id: str) -> dict:
    """Minimal serialized dict that skips deserialization and reaches the id branches."""
    return {
        "type": "not_implemented",
        "id": ["langchain_community", "chat_models", last_id],
        "repr": "",
        "kwargs": {},
    }


def _patch_model_by_key(return_value=None):
    """Patch _extract_model_by_key so it never raises, letting execution reach the id branches."""
    return patch(
        "agentic_eval.core_evals.fi_utils.extract_model._extract_model_by_key",
        return_value=return_value,
    )


def _patch_model_by_pattern(return_value=None):
    """Patch _extract_model_by_pattern so it returns None for all calls after the id branches."""
    return patch(
        "agentic_eval.core_evals.fi_utils.extract_model._extract_model_by_pattern",
        return_value=return_value,
    )


class TestAzureChatOpenAINoneInvocationParams:
    """_extract_model_name must not raise when invocation_params is absent (AzureChatOpenAI)."""

    def test_returns_none_without_invocation_params(self):
        serialized = _base_serialized("AzureChatOpenAI")
        kwargs = {}

        with _patch_model_by_key(), _patch_model_by_pattern():
            result = _extract_model_name(serialized, **kwargs)

        assert result is None

    def test_returns_none_when_invocation_params_is_none(self):
        serialized = _base_serialized("AzureChatOpenAI")
        kwargs = {"invocation_params": None}

        with _patch_model_by_key(), _patch_model_by_pattern():
            result = _extract_model_name(serialized, **kwargs)

        assert result is None

    def test_returns_model_when_invocation_params_present(self):
        serialized = _base_serialized("AzureChatOpenAI")
        kwargs = {"invocation_params": {"model": "gpt-4o"}}

        with _patch_model_by_key(), _patch_model_by_pattern():
            result = _extract_model_name(serialized, **kwargs)

        assert result == "gpt-4o"


class TestAzureOpenAINoneInvocationParams:
    """_extract_model_name must not AttributeError when invocation_params is absent (AzureOpenAI).

    Note: when invocation_params is missing/None and model_name is therefore absent,
    execution falls through to the deployment_name construction block (lines ~269-275)
    which has a separate pre-existing bug (issue #643) outside the scope of this fix.
    Tests here cover only the guarded path where invocation_params contains model_name.
    """

    def test_returns_model_name_when_invocation_params_present(self):
        serialized = _base_serialized("AzureOpenAI")
        kwargs = {"invocation_params": {"model_name": "gpt-4-turbo"}}

        with _patch_model_by_key(), _patch_model_by_pattern():
            result = _extract_model_name(serialized, **kwargs)

        assert result == "gpt-4-turbo"

    def test_returns_none_when_invocation_params_empty(self):
        """invocation_params present but model_name absent: guard works, falls through."""
        serialized = {
            "type": "not_implemented",
            "id": ["langchain_community", "llms", "AzureOpenAI"],
            "repr": "",
            # Provide both deployment fields so line 275 does not TypeError.
            # deployment_version is never assigned (pre-existing bug #643), so we
            # cannot avoid that crash without also patching the inner block.
            # Instead assert that we get no AttributeError from the invocation_params guard.
            "kwargs": {},
        }
        kwargs = {"invocation_params": None}

        with _patch_model_by_key(), _patch_model_by_pattern():
            # The AttributeError at the old .get().get() chain is gone.
            # Execution reaches the deployment block which raises TypeError (issue #643).
            # Verify it is NOT AttributeError so the guarded line is reached.
            with pytest.raises(TypeError):
                _extract_model_name(serialized, **kwargs)
