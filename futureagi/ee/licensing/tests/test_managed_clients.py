from __future__ import annotations

from unittest.mock import patch

import pytest

from ee.falcon_ai.llm_client import FalconLLMClient
from ee.turing.client import TuringClient


class TestTuringManagedClient:
    @patch("ee.licensing.managed_ai.chat_completion")
    def test_chat_completion_uses_managed_gateway(self, mock_call):
        mock_call.return_value = {
            "choices": [{"message": {"content": "managed response"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        }

        client = TuringClient()
        result = client.chat_completion(
            model="turing_small",
            messages=[{"role": "user", "content": "hello"}],
        )

        assert result == "managed response"
        mock_call.assert_called_once()
        payload = mock_call.call_args.args[0]
        assert payload["model"] == "turing_small"
        assert payload["messages"] == [{"role": "user", "content": "hello"}]
        assert client.token_usage["total_tokens"] == 5

    @patch("ee.licensing.managed_ai.chat_completion")
    def test_chat_completion_with_tools_uses_managed_gateway(self, mock_call):
        response = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [],
                    },
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        mock_call.return_value = response

        client = TuringClient()
        result = client.chat_completion_with_tools(
            model="turing_flash",
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "function": {"name": "lookup"}}],
        )

        assert result == response
        payload = mock_call.call_args.args[0]
        assert payload["model"] == "turing_flash"
        assert payload["tools"] == [
            {"type": "function", "function": {"name": "lookup"}}
        ]
        assert client.token_usage["total_tokens"] == 2


class TestFalconManagedClient:
    def test_default_uses_managed_gateway_transport(self):
        client = FalconLLMClient()
        assert client.use_managed_gateway is True
        assert client.model == "falcon_ai"

    @pytest.mark.asyncio
    async def test_stream_completion_uses_managed_gateway(self):
        payloads = []

        async def managed_stream(payload):
            payloads.append(payload)
            yield {
                "choices": [
                    {
                        "delta": {"content": "falcon managed"},
                        "finish_reason": None,
                    }
                ]
            }
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

        with patch(
            "ee.licensing.managed_ai.stream_chat_completion",
            new=managed_stream,
        ):
            client = FalconLLMClient()
            chunks = [
                chunk
                async for chunk in client.stream_completion(
                    [{"role": "user", "content": "analyze"}],
                    tools=[{"type": "function", "function": {"name": "create"}}],
                )
            ]

        assert chunks[0]["choices"][0]["delta"]["content"] == "falcon managed"
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
        payload = payloads[0]
        assert payload["model"] == "falcon_ai"
        assert payload["messages"] == [{"role": "user", "content": "analyze"}]
        assert payload["tools"] == [
            {"type": "function", "function": {"name": "create"}}
        ]
        assert payload["stream"] is True
