"""Tests for LLM.call_llm() response_format parameter.

Verifies that response_format is passed through to the gateway client
when provided, and omitted when None.

To run:
    docker exec backend bash -c "export DJANGO_SETTINGS_MODULE=tfc.settings.test && python -m pytest ee/tests/agentic_eval/tests/test_call_llm_response_format.py -v -s -n 0"
"""

import inspect

import pytest
from unittest.mock import MagicMock, patch

from agentic_eval.core.llm.llm import LLM


SAMPLE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "eval_result",
        "schema": {
            "type": "object",
            "properties": {"result": {"type": "number"}},
            "required": ["result"],
        },
    },
}


@pytest.fixture
def llm_instance():
    """Create an LLM instance with mocked internals."""
    with patch.object(LLM, "__init__", lambda self, **kw: None):
        instance = LLM.__new__(LLM)
        instance.model_name = "test-model"
        instance.temperature = 0.2
        instance.max_tokens = 4096
        instance.api_key = None
        instance.token_usage = {}
        instance.cost = {}
        instance._update_token_usage = lambda r: None
        instance._update_cost = lambda r: None
        return instance


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = '{"result": 0.8}'
    gw.chat.completions.create.return_value = resp
    return gw


class TestCallLlmResponseFormat:
    def test_default_response_format_is_none(self):
        sig = inspect.signature(LLM.call_llm)
        assert sig.parameters["response_format"].default is None

    def test_gateway_receives_response_format_when_set(
        self, llm_instance, mock_gateway
    ):
        with patch(
            "ee.usage.services.gateway_llm_client.get_gateway_client",
            return_value=mock_gateway,
        ):
            llm_instance.call_llm(
                [{"role": "user", "content": "test"}],
                "turing",
                response_format=SAMPLE_FORMAT,
            )

        kwargs = mock_gateway.chat.completions.create.call_args.kwargs
        assert "response_format" in kwargs
        assert kwargs["response_format"] == SAMPLE_FORMAT

    def test_gateway_excludes_response_format_when_none(
        self, llm_instance, mock_gateway
    ):
        with patch(
            "ee.usage.services.gateway_llm_client.get_gateway_client",
            return_value=mock_gateway,
        ):
            llm_instance.call_llm(
                [{"role": "user", "content": "test"}],
                "turing",
            )

        kwargs = mock_gateway.chat.completions.create.call_args.kwargs
        assert "response_format" not in kwargs

    def test_ee_managed_model_ignores_provider_api_key(
        self,
        llm_instance,
        mock_gateway,
    ):
        llm_instance.model_name = "turing_large"
        llm_instance.api_key = "customer-key"
        with (
            patch(
                "ee.usage.deployment.DeploymentMode.is_ee",
                return_value=True,
            ),
            patch(
                "ee.usage.services.gateway_llm_client.get_gateway_client",
                return_value=mock_gateway,
            ),
            patch("agentic_eval.core.llm.llm.litellm.completion") as fallback,
        ):
            llm_instance.call_llm(
                [{"role": "user", "content": "test"}],
                "turing",
            )

        mock_gateway.chat.completions.create.assert_called_once()
        fallback.assert_not_called()

    def test_ee_managed_gateway_failure_does_not_fall_back(self, llm_instance):
        from ee.licensing.activation_client import ManagedServiceError

        llm_instance.model_name = "turing_large"
        with (
            patch(
                "ee.usage.deployment.DeploymentMode.is_ee",
                return_value=True,
            ),
            patch(
                "ee.usage.services.gateway_llm_client.get_gateway_client",
                side_effect=ManagedServiceError("ACTIVATION_FAILED", "no token"),
            ),
            patch("agentic_eval.core.llm.llm.litellm.completion") as fallback,
        ):
            with pytest.raises(ManagedServiceError):
                llm_instance.call_llm(
                    [{"role": "user", "content": "test"}],
                    "turing",
                )

        fallback.assert_not_called()

    def test_ee_protect_uses_managed_transport(self, llm_instance):
        from ee.licensing.activation_client import ManagedServiceError

        llm_instance.model_name = "protect"
        llm_instance.provider = "protect"
        llm_instance._prepare_completion_payload = MagicMock(
            return_value={
                "model": "protect",
                "messages": [{"role": "user", "content": "test"}],
            }
        )
        llm_instance._try_gateway_completion = MagicMock(
            side_effect=ManagedServiceError("ACTIVATION_FAILED", "no token")
        )
        llm_instance._handle_protect_completion = MagicMock()
        llm_instance._handle_final_fallback = MagicMock()

        with patch(
            "ee.usage.deployment.DeploymentMode.is_ee",
            return_value=True,
        ):
            with pytest.raises(ManagedServiceError):
                llm_instance._get_completion_content(
                    [{"role": "user", "content": "test"}]
                )

        llm_instance._handle_protect_completion.assert_not_called()
        llm_instance._handle_final_fallback.assert_not_called()
