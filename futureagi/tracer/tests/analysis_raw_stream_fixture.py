"""Present protocol-only loop fixtures at the raw managed HTTP response seam.

The worker still constructs the real raw adapter and enforces its provider type.
Only the wire response is synthetic. Raw-provider tests supply their own real
Falcon class and native responses and do not use this protocol conversion.
"""

import asyncio
import json
import runpy
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def module(name, values):
    result = ModuleType(name)
    result.__dict__.update(values)
    return result


def install(agent, modules):
    client = agent.llm_client
    original_execute = getattr(agent, "_execute_tool", None)
    if original_execute is None:

        async def no_tools(*a):
            raise AssertionError("no tool dispatch expected")

        original_execute = no_tools

    async def execute(tool, args):
        from ee.agenthub.traceerroragent.background_payload_wire import (
            consume_tool_payload,
        )

        # Execute-only fixtures model an already validated tool.run boundary.
        # Actual raw-input fixtures mark their real canonical run explicitly.
        if not hasattr(tool, "_offline_wire") or getattr(
            tool, "_offline_foreground", False
        ):
            consume_tool_payload(tool, args, agent.tool_context.background_analysis)
        return await original_execute(tool, args)

    agent._execute_tool = execute
    if hasattr(client, "provider"):
        return
    legacy = getattr(client, "stream_completion", client.stream_with_retry)

    async def response(messages, tools):
        message, reason, usage, seen = {}, None, {}, False
        async for chunk in legacy(messages, tools):
            usage.update(chunk.get("usage") or {})
            choices = chunk.get("choices") or []
            if choices:
                seen = True
                choice = choices[0]
                message.update(choice.get("delta") or {})
                if choice.get("finish_reason") is not None:
                    reason = choice["finish_reason"]
        for call in message.get("tool_calls", []) or []:
            if call["function"]["arguments"] == "{}":
                # Old execute-only tests deliberately have no model input fixture.
                # Supply a real valid wire payload, never an adapter bypass.
                name = call["function"]["name"]
                data = {"trace_id": "00000000-0000-0000-0000-000000000001"}
                if name == "read_trace_span":
                    data["span_id"] = "span"
                elif name == "submit_trace_finding":
                    data.update(
                        runpy.run_path(
                            str(ROOT / "tracer/tests/analysis_payload_fixture.py")
                        )["healthy_report"]()["errors"][0]
                    )
                    data.pop("error_id")
                    data.pop("cluster_id")
                elif name == "submit_trace_scores":
                    report = runpy.run_path(
                        str(ROOT / "tracer/tests/analysis_payload_fixture.py")
                    )["healthy_report"]()
                    data.update(
                        {
                            k: v
                            for k, v in report["summary"].items()
                            if k != "error_count"
                        }
                    )
                    for dim, value in report["scores"].items():
                        data[dim + "_score"] = value["score"]
                        data[dim + "_reason"] = value["reason"]
                call["function"]["arguments"] = json.dumps(data)
        return {
            "choices": [{"message": message, "finish_reason": reason}] if seen else [],
            "usage": usage,
        }

    def completion(payload):
        return asyncio.run(response(payload["messages"], payload.get("tools")))

    compile_definitions = runpy.run_path(
        str(ROOT / "tracer/tests/analysis_llm_fixture.py")
    )["compile_definitions"]
    falcon = compile_definitions(
        ROOT / "ee/falcon_ai/llm_client.py",
        {"asyncio": asyncio, "json": json},
        names={"FalconLLMClient"},
    )
    cls = falcon["FalconLLMClient"]
    raw_client = cls.__new__(cls)
    raw_client.__dict__.update(
        provider="openai",
        use_managed_gateway=True,
        model="falcon_ai",
        max_tokens=100,
        temperature=0,
        response_format=None,
    )
    agent.llm_client = raw_client
    modules["ee.falcon_ai.llm_client"] = module("falcon_client", falcon)
    gateway = runpy.run_path(str(ROOT / "tracer/tests/analysis_gateway_fixture.py"))
    httpx = gateway["httpx"]

    def post(url, **kwargs):
        return httpx.Response(
            200,
            text=json.dumps(completion(kwargs["json"])),
            request=httpx.Request("POST", url),
        )

    managed = gateway["managed_modules"](post)
    # Preserve the worker's deployment fixture and add the cloud routing bit.
    if "ee.usage.deployment" in modules:
        modules["ee.usage.deployment"].DeploymentMode.is_cloud = lambda: True
        managed.pop("ee.usage.deployment")
    modules.update(managed)
    for name in ("background_provider", "background_falcon"):
        with patch.dict("sys.modules", modules):
            ns = runpy.run_path(str(ROOT / f"ee/agenthub/traceerroragent/{name}.py"))
        modules[f"ee.agenthub.traceerroragent.{name}"] = module(name, ns)
