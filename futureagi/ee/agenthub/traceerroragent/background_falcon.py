"""Falcon's generation-local raw transport, never its lossy stream normalizers.

Hidden background conversations do not need token streaming. Use one nonstream
request and present its proven response as one AgentLoop chunk. This avoids
making EOF, [DONE], or a normalized 'stop' stand in for provider evidence.
Foreground/interactive Falcon instances retain all original streaming behavior.
"""

import asyncio

from ee.agenthub.traceerroragent.background_provider import (
    direct_completion,
    normalize_response,
)
from ee.falcon_ai.llm_client import FalconLLMClient
from tracer.services.analysis_result_completion import IncompleteAnalysis


class BackgroundFalconClient(FalconLLMClient):
    def __init__(self, client, witness, call):
        # Copy configuration, never replace a shared class/method/global policy.
        self.__dict__.update(client.__dict__)
        self._witness = witness
        self._call = call

    async def _raw_completion(self, messages, tools, provider):
        if self._witness.failed or self._witness.fallback_used:
            raise IncompleteAnalysis()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            from ee.agenthub.traceerroragent.background_payload_wire import (
                background_tool_definitions,
            )

            payload["tools"] = background_tool_definitions(tools, self._witness)
        if self.response_format:
            payload["response_format"] = self.response_format
        if provider == "managed":
            from ee.agenthub.traceerroragent.background_gateway import (
                managed_completion,
            )

            raw = await asyncio.to_thread(managed_completion, payload)
            dialect = "openai"
        else:
            dialect = provider
            raw = await asyncio.to_thread(
                direct_completion,
                provider=provider,
                payload=payload,
                timeout=300.0,
                api_url=self.api_url,
                api_key=self.api_key,
                region=getattr(self, "bedrock_region", None),
            )
        self._witness.observe_raw(self._call, dialect, raw)
        from ee.agenthub.traceerroragent.background_payload_wire import (
            validate_falcon_wire,
        )

        validate_falcon_wire(raw, dialect, self._witness)
        response = normalize_response(raw, dialect)
        choice = response["choices"][0]
        message = dict(choice["message"])
        # AgentLoop consumes indexed streaming tool calls even for a single chunk.
        if message.get("tool_calls"):
            message["tool_calls"] = [
                dict(tool, index=index)
                for index, tool in enumerate(message["tool_calls"])
            ]
        return {
            **response,
            "choices": [{"delta": message, "finish_reason": choice["finish_reason"]}],
        }

    async def _stream_managed(self, messages, tools=None):
        yield await self._raw_completion(messages, tools, "managed")

    async def _stream_bedrock(self, messages, tools=None):
        yield await self._raw_completion(messages, tools, "bedrock")

    async def _stream_anthropic(self, messages, tools=None):
        yield await self._raw_completion(messages, tools, "anthropic")

    async def _stream_openai(self, messages, tools=None):
        # Vertex/turing in Falcon already use an explicit OpenAI gateway URL.
        yield await self._raw_completion(messages, tools, "openai")

    async def stream_with_retry(self, messages, tools=None, max_retries=0):
        async for chunk in self.stream_completion(messages, tools):
            yield chunk

    async def _call_llm_simple(self, *args, **kwargs):
        self._witness.failed = True
        raise IncompleteAnalysis()
