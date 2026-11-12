"""Actual HTTPX transport and raw normalization; no socket can be opened."""

import asyncio
import json
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
f = runpy.run_path(str(HERE / "analysis_llm_fixture.py"))


class RawTransport(unittest.TestCase):
    def setUp(self):
        witness_ns = runpy.run_path(
            str(ROOT / "tracer/services/analysis_result_completion.py")
        )
        falcon_ns = f["compile_definitions"](
            ROOT / "ee/falcon_ai/llm_client.py", {}, names={"FalconLLMClient"}
        )
        self.ns = f["compile_definitions"](
            ROOT / "ee/agenthub/traceerroragent/background_provider.py",
            {
                "json": json,
                "SimpleNamespace": SimpleNamespace,
                "FalconLLMClient": falcon_ns["FalconLLMClient"],
                "IncompleteAnalysis": witness_ns["IncompleteAnalysis"],
            },
        )
        from types import ModuleType

        mod = ModuleType("tracer.services.analysis_result_completion")
        vars(mod).update(witness_ns)
        modules = runpy.run_path(str(HERE / "analysis_payload_fixture.py"))["modules"](
            {"tracer.services.analysis_result_completion": mod}
        )
        isolation = patch.dict("sys.modules", modules)
        isolation.start()
        self.addCleanup(isolation.stop)
        self.witness = witness_ns["CompletionWitness"](4)
        self.key = object()
        self.payload = {
            "model": "offline",
            "max_tokens": 10,
            "temperature": 0,
            "messages": [{"role": "user", "content": "offline"}],
        }

    def transport(self, status, data):
        sends = []

        def handle(request):
            sends.append(request)
            return httpx.Response(status, json=data)

        self.ns["httpx"] = SimpleNamespace(
            Client=lambda **kw: httpx.Client(
                transport=httpx.MockTransport(handle), **kw
            )
        )
        return sends

    def call(self, provider):
        call = self.witness.start(self.key)
        try:
            raw = self.ns["direct_completion"](
                provider=provider,
                payload=self.payload,
                timeout=1,
                api_key="synthetic",
                api_url="https://offline.invalid",
            )
            self.witness.observe_raw(call, provider, raw)
            result = self.ns["normalize_response"](raw, provider)
            self.witness.end(call)
            return result
        except BaseException:
            self.witness.failed = True
            raise

    def test_http_error_is_one_send_no_retry(self):
        for provider in ("openai", "anthropic"):
            self.witness = type(self.witness)(4)  # independent generation per dialect
            sends = self.transport(500, {"error": "offline"})
            with self.assertRaises(httpx.HTTPStatusError):
                self.call(provider)
            self.assertEqual(len(sends), 1)
            self.assertTrue(self.witness.failed)
            self.assertFalse(self.witness.satisfied)

    def test_http_redirect_not_followed(self):
        sends = self.transport(307, {})
        with self.assertRaises(httpx.HTTPStatusError):
            self.call("openai")
        self.assertEqual(len(sends), 1)

    def test_raw_anthropic_reason_is_observed_before_normalizing(self):
        sends = self.transport(200, {"stop_reason": "max_tokens", "content": []})
        normalizer = Mock(wraps=self.ns["normalize_response"])
        self.ns["normalize_response"] = normalizer
        with self.assertRaises(RuntimeError):
            self.call("anthropic")
        normalizer.assert_not_called()
        self.assertEqual(len(sends), 1)
        self.assertEqual(self.witness.calls[-1]["raw_finish"], "max_tokens")

    def test_native_tool_response_roundtrips_sdk_message_interface(self):
        self.transport(
            200,
            {
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "one",
                        "name": "inspect",
                        "input": {"span": "x"},
                    },
                    {"type": "tool_use", "id": "two", "name": "submit", "input": {}},
                ],
            },
        )
        response = self.ns["response_object"](self.call("anthropic"))
        self.assertEqual(response.choices[0].message.tool_calls[1].id, "two")
        self.assertEqual(
            json.loads(response.choices[0].message.tool_calls[0].function.arguments),
            {"span": "x"},
        )
        self.assertEqual(response.choices[0].message.model_dump()["role"], "assistant")
        self.assertFalse(self.witness.satisfied, "tool-use response is not a final")
        self.transport(
            200,
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": "done"}]},
        )
        self.call("anthropic")
        self.assertTrue(self.witness.satisfied)

    def test_native_usage_matches_actual_llm_accounting_interface(self):
        self.transport(
            200,
            {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 3, "output_tokens": 4},
            },
        )
        response = self.ns["response_object"](self.call("anthropic"))
        method = f["compile_definitions"](
            f["LLM_PATH"], {}, "LLM", {"_update_token_usage"}
        )["_update_token_usage"]
        client = SimpleNamespace(
            token_usage={"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}
        )
        method(client, response)
        self.assertEqual(
            client.token_usage,
            {"total_tokens": 7, "prompt_tokens": 3, "completion_tokens": 4},
        )

    def test_normal_openai_wire_response_completes(self):
        sends = self.transport(
            200,
            {"choices": [{"finish_reason": "stop", "message": {"content": "done"}}]},
        )
        self.call("openai")
        self.assertTrue(self.witness.satisfied)
        self.assertEqual(self.witness.calls[0]["raw_finish"], "stop")
        self.assertEqual(len(sends), 1)
        self.assertNotIn("stream", json.loads(sends[0].content))

    def test_raw_required_rejects_unproven_protocol_client(self):
        completion = runpy.run_path(
            str(ROOT / "tracer/services/analysis_result_completion.py")
        )
        sends = []

        async def stream(*args):
            sends.append(1)
            yield {"choices": [{"finish_reason": "stop"}]}

        witness = completion["CompletionWitness"](4)
        client = completion["RecordingLLM"](
            SimpleNamespace(stream_completion=stream), witness, raw=True
        )
        adapter = Mock()

        async def run():
            return [chunk async for chunk in client.stream_with_retry([])]

        with patch.dict(
            "sys.modules",
            {
                "ee.agenthub.traceerroragent.background_falcon": SimpleNamespace(
                    BackgroundFalconClient=adapter
                ),
                "ee.falcon_ai.llm_client": SimpleNamespace(
                    FalconLLMClient=type("FalconLLMClient", (), {})
                ),
            },
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(run())
        self.assertTrue(witness.failed)
        self.assertFalse(witness.satisfied)
        self.assertEqual(sends, [])
        adapter.assert_not_called()

    def test_unsupported_provider_refuses_without_network(self):
        sends = self.transport(200, {})
        with self.assertRaises(RuntimeError):
            self.call("unknown")
        self.assertEqual(sends, [])
        self.assertFalse(self.witness.satisfied)


if __name__ == "__main__":
    unittest.main()
