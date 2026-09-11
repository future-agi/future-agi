"""Tests for LLM gateway-first routing."""

import sys
import types
from unittest.mock import MagicMock, patch


class TestLLMOrgId:
    def test_org_id_defaults_to_none(self):
        with patch("agentic_eval.core.llm.llm.LLM._init_client"):
            from agentic_eval.core.llm.llm import LLM

            llm = LLM.__new__(LLM)
            LLM.__init__(llm, provider="openai", model_name="gpt-4o")
            assert llm.org_id is None

    def test_org_id_stored_on_instance(self):
        with patch("agentic_eval.core.llm.llm.LLM._init_client"):
            from agentic_eval.core.llm.llm import LLM

            llm = LLM.__new__(LLM)
            LLM.__init__(llm, provider="openai", model_name="gpt-4o", org_id="org-123")
            assert llm.org_id == "org-123"


class TestLLMGatewayFirstRouting:
    def _make_llm(self, org_id=None, gateway_client=None):
        with patch("agentic_eval.core.llm.llm.LLM._init_client"):
            from agentic_eval.core.llm.llm import LLM

            llm = LLM.__new__(LLM)
            llm.provider = "openai"
            llm.model_name = "gpt-4o"
            llm.temperature = 0.7
            llm.max_tokens = 8100
            llm.history = []
            llm.config = {}
            llm.optimizer = False
            llm.api_key = None
            llm.org_id = org_id
            llm.cost = {"total_cost": 0, "prompt_cost": 0, "completion_cost": 0}
            llm.token_usage = {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }
            llm._gateway_client = gateway_client
            llm.provider_api_keys = {"openai": "test-key"}
            return llm

    def _mock_gateway_response(self, content="gateway response"):
        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = content
        mock_response.usage.total_tokens = 10
        mock_response.usage.prompt_tokens = 5
        mock_response.usage.completion_tokens = 5
        mock_gateway.chat.completions.create.return_value = mock_response
        return mock_gateway

    @patch("agentic_eval.core.llm.llm.log_to_clickhouse")
    def test_gateway_success_skips_litellm(self, mock_ch):
        mock_gateway = self._mock_gateway_response("gateway response")
        llm = self._make_llm(org_id="org-123", gateway_client=mock_gateway)

        with patch("agentic_eval.core.llm.llm.litellm") as mock_litellm:
            result = llm._get_completion_content(
                messages=[{"role": "user", "content": "hello"}]
            )
            assert result == "gateway response"
            mock_litellm.completion.assert_not_called()

    @patch("agentic_eval.core.llm.llm.log_to_clickhouse")
    def test_gateway_failure_falls_back_to_litellm(self, mock_ch):
        mock_gateway = MagicMock()
        mock_gateway.chat.completions.create.side_effect = Exception("gateway down")

        mock_litellm_response = MagicMock()
        mock_litellm_response.choices = [MagicMock()]
        mock_litellm_response.choices[0].message.content = "litellm response"
        mock_litellm_response.usage.total_tokens = 10
        mock_litellm_response.usage.prompt_tokens = 5
        mock_litellm_response.usage.completion_tokens = 5

        llm = self._make_llm(org_id=None, gateway_client=mock_gateway)

        with patch("agentic_eval.core.llm.llm.litellm") as mock_litellm:
            mock_litellm.completion.return_value = mock_litellm_response
            result = llm._get_completion_content(
                messages=[{"role": "user", "content": "hello"}]
            )
            assert result == "litellm response"

    @patch("agentic_eval.core.llm.llm.log_to_clickhouse")
    def test_gateway_sends_org_id_header(self, mock_ch):
        mock_gateway = self._mock_gateway_response("ok")
        llm = self._make_llm(org_id="org-456", gateway_client=mock_gateway)

        llm._get_completion_content(messages=[{"role": "user", "content": "hello"}])

        call_kwargs = mock_gateway.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("extra_headers", {}).get("X-Org-Id") == "org-456"

    @patch("agentic_eval.core.llm.llm.log_to_clickhouse")
    def test_gateway_forwards_response_format(self, mock_ch):
        mock_gateway = self._mock_gateway_response("ok")
        llm = self._make_llm(org_id=None, gateway_client=mock_gateway)

        llm._get_completion_content(
            messages=[{"role": "user", "content": "hello"}],
            response_format={"type": "json_object"},
        )

        call_kwargs = mock_gateway.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("response_format") == {"type": "json_object"}

    @patch("agentic_eval.core.llm.llm.log_to_clickhouse")
    def test_gateway_forwards_safe_openai_metadata(self, mock_ch):
        mock_gateway = self._mock_gateway_response("ok")
        llm = self._make_llm(org_id="org-789", gateway_client=mock_gateway)

        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.1,
            "max_tokens": 42,
            "response_format": {"type": "json_object"},
            "stream": True,
            "stream_options": {"include_usage": True},
            "unknown_internal_field": "skip-me",
        }

        llm._try_gateway_completion(payload)

        call_kwargs = mock_gateway.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("response_format") == {"type": "json_object"}
        assert call_kwargs.kwargs.get("stream") is True
        assert call_kwargs.kwargs.get("stream_options") == {"include_usage": True}
        assert "unknown_internal_field" not in call_kwargs.kwargs

    @patch("agentic_eval.core.llm.llm.log_to_clickhouse")
    def test_no_gateway_when_client_is_none(self, mock_ch):
        mock_litellm_response = MagicMock()
        mock_litellm_response.choices = [MagicMock()]
        mock_litellm_response.choices[0].message.content = "litellm only"
        mock_litellm_response.usage.total_tokens = 10
        mock_litellm_response.usage.prompt_tokens = 5
        mock_litellm_response.usage.completion_tokens = 5

        llm = self._make_llm(org_id=None, gateway_client=None)

        with patch("agentic_eval.core.llm.llm.litellm") as mock_litellm:
            mock_litellm.completion.return_value = mock_litellm_response
            result = llm._get_completion_content(
                messages=[{"role": "user", "content": "hello"}]
            )
            assert result == "litellm only"

    @patch("agentic_eval.core.llm.llm.log_to_clickhouse")
    def test_futureagi_managed_model_uses_managed_ai_service(self, mock_ch, monkeypatch):
        managed = types.ModuleType("ee.licensing.managed_ai")

        def is_managed_model(model):
            return str(model).startswith("turing_")

        calls = []

        def chat_completion(payload):
            calls.append(payload)
            return {
                "choices": [{"message": {"content": "managed ai response"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            }

        managed.is_managed_model = is_managed_model
        managed.chat_completion = chat_completion

        ee_module = types.ModuleType("ee")
        licensing_module = types.ModuleType("ee.licensing")
        monkeypatch.setitem(sys.modules, "ee", ee_module)
        monkeypatch.setitem(sys.modules, "ee.licensing", licensing_module)
        monkeypatch.setitem(sys.modules, "ee.licensing.managed_ai", managed)

        mock_gateway = self._mock_gateway_response("old gateway")
        llm = self._make_llm(org_id="org-123", gateway_client=mock_gateway)
        llm.model_name = "turing_small"

        with patch("agentic_eval.core.llm.llm.litellm") as mock_litellm:
            result = llm._get_completion_content(
                messages=[{"role": "user", "content": "hello"}]
            )

        assert result == "managed ai response"
        assert calls[0]["model"] == "turing_small"
        mock_gateway.chat.completions.create.assert_not_called()
        mock_litellm.completion.assert_not_called()
        assert llm.token_usage["total_tokens"] == 5
