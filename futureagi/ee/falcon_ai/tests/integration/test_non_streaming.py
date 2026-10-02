import asyncio
import json
import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
logging.disable(logging.CRITICAL)
import django

django.setup()

from ee.falcon_ai.llm_client import FalconLLMClient

client = FalconLLMClient()
print(f"Provider: {client.provider}")
print(f"Model: {client.model[:60]}")


async def test_simple():
    print("\n--- Test 1: Simple text ---")
    async for chunk in client.non_streaming_completion(
        [{"role": "user", "content": "Say exactly: hello world"}]
    ):
        delta = chunk["choices"][0]["delta"]
        text = delta.get("content", "")
        fr = chunk["choices"][0].get("finish_reason")
        usage = chunk.get("usage")
        print(f"  Content: {text[:100]}")
        print(f"  Finish: {fr}")
        print(f"  Usage: {usage}")


async def test_tool_call():
    print("\n--- Test 2: Tool call ---")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]
    async for chunk in client.non_streaming_completion(
        [{"role": "user", "content": "Weather in London?"}],
        tools=tools,
    ):
        delta = chunk["choices"][0]["delta"]
        fr = chunk["choices"][0].get("finish_reason")
        print(f"  Has content: {bool(delta.get('content'))}")
        print(f"  Has tool_calls: {bool(delta.get('tool_calls'))}")
        print(f"  Finish: {fr}")
        if delta.get("tool_calls"):
            for tc in delta["tool_calls"]:
                print(
                    f"  Tool: {tc['function']['name']}({tc['function']['arguments']})"
                )


async def test_real():
    print("\n--- Test 3: Real tools (explore_trace) ---")
    from ee.falcon_ai.modes import load_tools_for_mode

    tools = load_tools_for_mode("tracing")
    openai_tools = []
    for t in tools:
        s = t.input_schema
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": (
                        s
                        if s.get("properties")
                        else {"type": "object", "properties": {}}
                    ),
                },
            }
        )
    print(f"  Tools: {len(openai_tools)}")

    async for chunk in client.non_streaming_completion(
        [
            {
                "role": "system",
                "content": "Use explore_trace to analyze traces for errors.",
            },
            {
                "role": "user",
                "content": "Analyze trace af1931b7-78d7-a82c-a6a1-f7dfdb9aa8bb for errors.",
            },
        ],
        tools=openai_tools,
    ):
        delta = chunk["choices"][0]["delta"]
        fr = chunk["choices"][0].get("finish_reason")
        print(f"  Has content: {bool(delta.get('content'))}")
        print(f"  Has tool_calls: {bool(delta.get('tool_calls'))}")
        print(f"  Finish: {fr}")
        if delta.get("tool_calls"):
            for tc in delta["tool_calls"]:
                args = tc["function"]["arguments"][:80]
                print(f"  Tool: {tc['function']['name']}({args})")


loop = asyncio.new_event_loop()
loop.run_until_complete(test_simple())
loop.run_until_complete(test_tool_call())
loop.run_until_complete(test_real())
loop.close()
print("\n=== ALL PASSED ===")
