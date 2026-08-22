from unittest.mock import patch

import pytest

from ee.falcon_ai.llm_client import FalconLLMClient


class EmptyStreamClient(FalconLLMClient):
    def __init__(self):
        super().__init__(provider="openai", model="gpt-4o")
        self.calls = 0

    async def stream_completion(self, messages, tools=None):
        self.calls += 1
        if False:
            yield {}


@pytest.mark.asyncio
async def test_stream_with_retry_retries_empty_stream_before_failing(monkeypatch):
    client = EmptyStreamClient()

    async def no_sleep(delay):
        return None

    monkeypatch.setattr("ee.falcon_ai.llm_client.asyncio.sleep", no_sleep)

    with pytest.raises(RuntimeError, match="empty stream"):
        async for _chunk in client.stream_with_retry(
            [{"role": "user", "content": "hello"}],
            max_retries=2,
        ):
            pass

    assert client.calls == 2


@pytest.mark.asyncio
async def test_managed_stream_forwards_incremental_tool_calls_usage_and_metadata():
    chunks = [
        {
            "choices": [
                {
                    "delta": {"content": "Working"},
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {
                                    "name": "create_agent",
                                    "arguments": '{"name":',
                                },
                            },
                            {
                                "index": 1,
                                "id": "call_2",
                                "function": {
                                    "name": "lookup",
                                    "arguments": '{"id":',
                                },
                            },
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"arguments": '"agent"}'},
                            },
                            {
                                "index": 1,
                                "function": {"arguments": "1}"},
                            },
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {
            "choices": [],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            "agentcc_metadata": {"cost": 0.25},
        },
    ]

    async def managed_stream(payload):
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
        for chunk in chunks:
            yield chunk

    with patch(
        "ee.licensing.managed_ai.stream_chat_completion",
        new=managed_stream,
    ):
        client = FalconLLMClient()
        result = [
            chunk
            async for chunk in client.stream_completion(
                [{"role": "user", "content": "build"}],
                tools=[{"type": "function", "function": {"name": "create_agent"}}],
            )
        ]

    assert result == chunks
    assert client._gateway_cost == 0.25


@pytest.mark.asyncio
async def test_managed_stream_inlines_ref_tool_schemas():
    import json

    captured = {}

    async def managed_stream(payload):
        captured["payload"] = payload
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    tools = [
        {
            "type": "function",
            "function": {
                "name": "make_agent",
                "parameters": {
                    "type": "object",
                    "properties": {"cfg": {"$ref": "#/$defs/Cfg"}},
                    "$defs": {"Cfg": {"type": "object"}},
                },
            },
        }
    ]

    with patch(
        "ee.licensing.managed_ai.stream_chat_completion",
        new=managed_stream,
    ):
        async for _ in FalconLLMClient().stream_completion(
            [{"role": "user", "content": "go"}], tools=tools
        ):
            pass

    serialized = json.dumps(captured["payload"]["tools"])
    assert "$ref" not in serialized
    assert "$defs" not in serialized


@pytest.mark.asyncio
async def test_managed_stream_normalizes_length_finish_reason():
    async def managed_stream(payload):
        yield {
            "choices": [
                {
                    "delta": {},
                    "finish_reason": "length",
                }
            ]
        }

    with patch(
        "ee.licensing.managed_ai.stream_chat_completion",
        new=managed_stream,
    ):
        chunks = [
            chunk
            async for chunk in FalconLLMClient().stream_completion(
                [{"role": "user", "content": "hello"}]
            )
        ]

    assert chunks[0]["choices"][0]["finish_reason"] == "max_tokens"
