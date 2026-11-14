"""Regression tests for empty provider choices in llm.py (issue #643).

The xAI, gateway, and call_llm paths in LLM read response.choices[0]
without checking the list first, so a provider response with no
choices (content filtering, tool-call-only replies) failed with
"list index out of range" instead of the ValueError("Empty response
from model") that the litellm paths in the same methods already raise.
These tests pin the guarded behavior at each of those sites.
"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_llm(provider="openai"):
    with patch("agentic_eval.core.llm.llm.LLM._init_client"):
        from agentic_eval.core.llm.llm import LLM

        llm = LLM.__new__(LLM)
        llm.provider = provider
        llm.model_name = "gpt-4o"
        llm.temperature = 0.7
        llm.max_tokens = 8100
        llm.history = []
        llm.config = {}
        llm.optimizer = False
        llm.api_key = None
        llm.org_id = None
        llm.cost = {"total_cost": 0, "prompt_cost": 0, "completion_cost": 0}
        llm.token_usage = {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        llm._gateway_client = None
        llm.provider_api_keys = {"openai": "test-key"}
        llm.last_finish_reason = None
        return llm


def _empty_response():
    response = MagicMock()
    response.choices = []
    response.usage.total_tokens = 10
    response.usage.prompt_tokens = 5
    response.usage.completion_tokens = 5
    return response


def _mock_gateway(response):
    mock_gateway = MagicMock()
    mock_gateway.chat.completions.create.return_value = response
    return mock_gateway


class TestGetCompletionContentEmptyChoices:
    """Sync _get_completion_content: xAI and gateway branches.

    _requires_managed_transport is patched to True because managed
    models surface provider errors immediately instead of retrying;
    that is what lets the test observe the branch's own error rather
    than the retry loop's FAILED_TO_PROCESS_EVALUATION.
    """

    @patch("agentic_eval.core.llm.llm.log_to_clickhouse")
    def test_gateway_empty_choices_raises_value_error(self, mock_ch):
        from agentic_eval.core.llm.llm import LLM

        llm = _make_llm()

        with (
            patch.object(
                LLM, "_try_gateway_completion", return_value=_empty_response()
            ),
            patch.object(LLM, "_requires_managed_transport", return_value=True),
        ):
            with pytest.raises(ValueError, match="Empty response from model"):
                llm._get_completion_content(
                    messages=[{"role": "user", "content": "hello"}]
                )

    @patch("agentic_eval.core.llm.llm.log_to_clickhouse")
    def test_xai_empty_choices_raises_value_error(self, mock_ch):
        from agentic_eval.core.llm.llm import LLM

        llm = _make_llm(provider="xai")
        llm.llm_client = MagicMock()
        llm.llm_client.chat.completions.create.return_value = _empty_response()

        with patch.object(LLM, "_requires_managed_transport", return_value=True):
            with pytest.raises(ValueError, match="Empty response from model"):
                llm._get_completion_content(
                    messages=[{"role": "user", "content": "hello"}]
                )


class TestGetCompletionContentAsyncEmptyChoices:
    @patch("agentic_eval.core.llm.llm.log_to_clickhouse")
    def test_gateway_empty_choices_raises_value_error(self, mock_ch):
        from agentic_eval.core.llm.llm import LLM

        llm = _make_llm()

        with (
            patch.object(
                LLM,
                "_try_gateway_completion_async",
                new=AsyncMock(return_value=_empty_response()),
            ),
            patch.object(LLM, "_requires_managed_transport", return_value=True),
        ):
            with pytest.raises(ValueError, match="Empty response from model"):
                asyncio.run(
                    llm._get_completion_content_async(
                        messages=[{"role": "user", "content": "hello"}]
                    )
                )


class TestCallLlmEmptyChoices:
    def test_litellm_empty_choices_reports_empty_response(self):
        llm = _make_llm()
        llm.api_key = "test-key"

        with patch("agentic_eval.core.llm.llm.litellm") as mock_litellm:
            mock_litellm.completion.return_value = _empty_response()
            with pytest.raises(ValueError) as excinfo:
                llm.call_llm(
                    prompt=[{"role": "user", "content": "hello"}],
                    provider="openai",
                )

        assert "Empty response from model" in str(excinfo.value)
        assert "list index out of range" not in str(excinfo.value)

    def test_gateway_empty_choices_raises_for_managed_model(self, monkeypatch):
        from agentic_eval.core.llm.llm import LLM

        llm = _make_llm()

        gateway_module = types.ModuleType("ee.usage.services.gateway_llm_client")
        gateway_module.get_gateway_client = lambda: _mock_gateway(_empty_response())
        for name in ("ee", "ee.usage", "ee.usage.services"):
            parent = types.ModuleType(name)
            parent.__path__ = []
            monkeypatch.setitem(sys.modules, name, parent)
        monkeypatch.setitem(
            sys.modules,
            "ee.usage.services.gateway_llm_client",
            gateway_module,
        )

        with patch.object(LLM, "_requires_managed_transport", return_value=True):
            with pytest.raises(ValueError, match="Empty response from model"):
                llm.call_llm(
                    prompt=[{"role": "user", "content": "hello"}],
                    provider="openai",
                )
