"""Raw provider evidence through real Falcon normalization, loop and worker."""

import asyncio
import io
import json
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
f = runpy.run_path(str(HERE / "analysis_llm_fixture.py"))
b = runpy.run_path(str(HERE / "test_analysis_completion_witness_offline.py"))
p = runpy.run_path(str(HERE / "test_analysis_provider_attempts_offline.py"))
r = runpy.run_path(str(HERE / "analysis_library_retry_fixture.py"))


class RawFalcon(b["FalconCompletion"]):
    def install(self):
        deployment = self.modules.get("ee.usage.deployment")
        if deployment is not None:
            deployment.DeploymentMode.is_cloud = lambda: getattr(self, "cloud", True)
        super().install()

    def replay_raw(self, provider, reason, *, foreground=False):
        self.guard = self.Guard("p", "u", "o", "w", 2)
        self.context.background_analysis = self.guard
        invoke, background, _ = self.configure("success")
        finding = runpy.run_path(str(HERE / "analysis_payload_fixture.py"))[
            "healthy_report"
        ]()["errors"][0]
        finding = {
            k: v for k, v in finding.items() if k not in {"error_id", "cluster_id"}
        }
        finding["trace_id"] = "00000000-0000-0000-0000-000000000001"
        attempts = []
        options = []

        def raw_response():
            attempts.append(provider)
            tool = len(attempts) == 1
            stop = "tool_use" if tool else reason
            if provider in {"openai", "managed"}:
                message = {"content": "analysis"}
                if tool:
                    message["tool_calls"] = [
                        {
                            "id": "call",
                            "type": "function",
                            "index": 0,
                            "function": {
                                "name": "submit_trace_finding",
                                "arguments": json.dumps(finding),
                            },
                        }
                    ]
                choice = {"message": message}
                if reason != "MISSING" or tool:
                    choice["finish_reason"] = "tool_calls" if tool else reason
                return {"choices": [choice], "usage": {}}
            content = (
                [
                    {
                        "type": "tool_use",
                        "id": "call",
                        "name": "submit_trace_finding",
                        "input": finding,
                    }
                ]
                if tool
                else [{"type": "text", "text": "analysis"}]
            )
            result = {"content": content, "usage": {}, "model": "offline"}
            if stop != "MISSING":
                result["stop_reason"] = stop
            return result

        def events(response):
            if provider in {"openai", "managed"}:
                choice = response["choices"][0]
                yield {
                    "choices": [
                        {
                            "delta": choice["message"],
                            "finish_reason": choice.get("finish_reason"),
                        }
                    ]
                }
                return
            block = response["content"][0]
            if block["type"] == "tool_use":
                yield {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": block,
                }
                yield {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": "{}"},
                }
            else:
                yield {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "analysis"},
                }
            delta = (
                {"stop_reason": response["stop_reason"]}
                if "stop_reason" in response
                else {}
            )
            yield {"type": "message_delta", "delta": delta}
            yield {"type": "message_stop"}

        class Response:
            status_code = 200

            def __init__(self):
                self.value = raw_response()
                self.text = json.dumps(self.value)
                self.content = self.text.encode()

            def close(self):
                pass

            def raise_for_status(self):
                pass

            def json(self):
                return self.value

            async def aiter_lines(self):
                for event in events(self.value):
                    yield "data: " + json.dumps(event)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class HTTP:
            def __init__(self, **kwargs):
                options.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def stream(self, *args, **kwargs):
                return Response()

            def post(self, *args, **kwargs):
                return Response()

        def boto_client(*args, **kwargs):
            options.append(kwargs)
            return f["bedrock_double"](
                invoke_model_with_response_stream=lambda **kw: {
                    "body": [
                        {"chunk": {"bytes": json.dumps(event).encode()}}
                        for event in events(raw_response())
                    ]
                },
                invoke_model=lambda **kw: {
                    "body": io.BytesIO(json.dumps(raw_response()).encode())
                },
            )

        httpx = SimpleNamespace(AsyncClient=HTTP, Client=HTTP)
        ns = f["compile_definitions"](
            ROOT / "ee/falcon_ai/llm_client.py",
            {
                "asyncio": asyncio,
                "json": json,
                "logger": Mock(),
                "httpx": httpx,
            },
            names={"FalconLLMClient", "EmptyStreamError", "_inline_refs"},
        )
        cls = ns["FalconLLMClient"]
        client = cls.__new__(cls)
        client.__dict__.update(
            provider=provider,
            use_managed_gateway=provider == "managed",
            model="falcon_ai" if provider == "managed" else "offline",
            max_tokens=10,
            temperature=0,
            bedrock_region="offline",
            api_key="synthetic",
            api_url="https://offline.invalid",
            response_format=None,
            _gateway_cost=0,
        )
        invoke.agent.llm_client = client

        async def managed_stream(payload):
            for event in events(raw_response()):
                yield event

        self._raw_managed_response = lambda payload: raw_response()
        gateway = runpy.run_path(str(HERE / "analysis_gateway_fixture.py"))
        managed = gateway["managed_modules"](lambda *a, **kw: Response())
        managed["ee.licensing.managed_ai"].stream_chat_completion = managed_stream
        managed["httpx"].Client = HTTP
        managed["httpx"].AsyncClient = HTTP
        self.modules.update(managed)
        self.modules["boto3"] = SimpleNamespace(client=boto_client)
        self.modules["botocore.config"] = SimpleNamespace(
            Config=lambda **kw: SimpleNamespace(**kw)
        )
        self.modules["ee.falcon_ai.llm_client"] = b["boundaries"]["module"](
            "llm_client", FalconLLMClient=cls
        )
        # Load the actual adapter modules, if present, with only I/O substituted.
        completion = runpy.run_path(
            str(ROOT / "tracer/services/analysis_result_completion.py")
        )
        for name in ("background_provider", "background_falcon"):
            path = ROOT / f"ee/agenthub/traceerroragent/{name}.py"
            if path.exists():
                with patch.dict(
                    "sys.modules",
                    {
                        **self.modules,
                        "tracer.services.analysis_result_completion": b["boundaries"][
                            "module"
                        ]("completion", **completion),
                    },
                ):
                    self.modules[f"ee.agenthub.traceerroragent.{name}"] = b[
                        "boundaries"
                    ]["module"](name, **runpy.run_path(str(path)))
        if foreground:
            invoke.agent.tool_context = SimpleNamespace()
            with patch.dict(
                "sys.modules", {**self.modules, "ai_tools.background": background}
            ):
                result = asyncio.run(
                    invoke(
                        user_message="analyze",
                        history_messages=[],
                        send_callback=AsyncMock(),
                    )
                )
            return result, [], None, attempts, options
        result, effects, marker = self.worker(None)
        return result, effects, marker, attempts, options

    def check_raw_failure(self, provider, reason):
        result, effects, marker, attempts, _ = self.replay_raw(provider, reason)
        self.assertFalse(result["success"], result)
        self.assertEqual(result.get("code"), "analysis_failed")
        self.assertEqual(
            effects, [], "no embedding, successful log, clustering or completion"
        )
        self.assertEqual(self.saved, [])
        marker.complete.assert_not_called()
        marker.abort.assert_called_once()
        witness = self.agent_runner.agent.llm_client._witness
        self.assertTrue(witness.failed)
        self.assertFalse(witness.satisfied)
        self.assertEqual(len(witness.calls), len(attempts))

    def test_bedrock_raw_max_tokens_aborts(self):
        self.check_raw_failure("bedrock", "max_tokens")

    def test_bedrock_missing_reason_aborts(self):
        self.check_raw_failure("bedrock", "MISSING")

    def test_anthropic_unknown_reason_aborts(self):
        self.check_raw_failure("anthropic", "unknown")

    def test_all_other_bad_raw_reasons_abort(self):
        for provider in ("bedrock", "anthropic"):
            for reason in (None, "", "length", "content_filter", "error"):
                with self.subTest(provider=provider, reason=reason):
                    self.check_raw_failure(provider, reason)

    def test_normal_raw_reasons_complete(self):
        for provider in ("bedrock", "anthropic"):
            for reason in ("end_turn", "stop_sequence"):
                with self.subTest(provider=provider, reason=reason):
                    result, effects, marker, attempts, options = self.replay_raw(
                        provider, reason
                    )
                    self.assertTrue(result["success"], result)
                    self.assertEqual(
                        effects, ["completed", "embedding", "success-log", "cluster"]
                    )
                    marker.complete.assert_called_once()
                    self.assertEqual(len(attempts), 2)
                    if provider == "bedrock" and "config" in options[0]:
                        self.assertEqual(
                            options[0]["config"].retries,
                            {"total_max_attempts": 1, "mode": "standard"},
                        )

    def test_openai_and_managed_raw_terminal_policies(self):
        for provider in ("openai", "managed"):
            for reason in (
                "MISSING",
                None,
                "unknown",
                "end_turn",
                "length",
                "max_tokens",
                "content_filter",
                "error",
            ):
                with self.subTest(provider=provider, reason=reason):
                    self.check_raw_failure(provider, reason)
            result, effects, marker, attempts, _ = self.replay_raw(provider, "stop")
            self.assertTrue(result["success"], result)
            self.assertEqual(
                effects, ["completed", "embedding", "success-log", "cluster"]
            )
            marker.complete.assert_called_once()
            self.assertEqual(len(attempts), 2)

    def test_foreground_normalizer_behavior_unchanged(self):
        for provider, reason in (("bedrock", "max_tokens"), ("anthropic", "unknown")):
            result, _, _, attempts, _ = self.replay_raw(
                provider, reason, foreground=True
            )
            self.assertNotIn("success", result)
            self.assertEqual(len(attempts), 2)


class LibraryPolicy(p["ProviderAttempts"]):
    def test_nonzero_library_policy_cannot_hide_failed_judge_attempt(self):
        original = p["fixture"]["clients"]

        def clients(send):
            base, ns, evidence = original(send)
            transport = ns["litellm"]
            transport.completion = r["real_retry_wrapper"](
                transport.completion, transport, 2
            )
            return base, ns, evidence

        with patch.dict(p["fixture"], {"clients": clients}):
            self.assert_failed_attempt("judge")

    def test_installed_library_really_retries_under_nonzero_policy(self):
        attempts = []

        def send(**kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise RuntimeError("offline first failure")
            return f["response"]()

        wrapped = r["real_retry_wrapper"](send, SimpleNamespace(), 2)
        wrapped(model="offline", messages=[], num_retries=0, max_retries=0)
        self.assertEqual(len(attempts), 2)


if __name__ == "__main__":
    unittest.main()
