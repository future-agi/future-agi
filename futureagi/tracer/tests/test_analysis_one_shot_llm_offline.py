"""One-shot adapter send boundaries, terminal evidence and retry isolation."""

import asyncio
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

f = runpy.run_path(str(Path(__file__).with_name("analysis_llm_fixture.py")))


class OneShot(unittest.TestCase):
    def client(self, outcomes, *, limit=4, gateway=False):
        sends = []
        queue = iter(outcomes)

        def send(route, kwargs):
            sends.append((route, kwargs))
            value = next(queue)
            if isinstance(value, BaseException):
                raise value
            return value

        base, namespace, evidence = f["clients"](send)
        raw_gateway = runpy.run_path(
            str(Path(__file__).with_name("analysis_gateway_fixture.py"))
        )
        payloads = runpy.run_path(
            str(Path(__file__).with_name("analysis_payload_fixture.py"))
        )
        self.managed_send = lambda payload: f["response"]()

        def post(url, **kwargs):
            return raw_gateway["httpx"].Response(
                200,
                json=raw_gateway["jsonable"](self.managed_send(kwargs["json"])),
                request=raw_gateway["httpx"].Request("POST", url),
            )

        modules = payloads["modules"](
            {
                "tracer.services.analysis_result_completion": raw_gateway["module"](
                    "completion", **evidence
                ),
            }
        )
        modules.update(raw_gateway["managed_modules"](post))
        isolation = patch.dict("sys.modules", modules)
        isolation.start()
        self.addCleanup(isolation.stop)
        witness = evidence["CompletionWitness"](max_calls=limit)
        client = namespace["BackgroundLLM"](
            witness=witness,
            provider="bedrock",
            model_name="offline",
            max_tokens=100,
            temperature=0,
        )
        transport = f["Gateway"](send)
        if gateway:
            client._gateway_client = transport
        return client, witness, sends, transport, base

    def test_content_counts_one_send_not_helper_boundaries(self):
        client, witness, sends, _, _ = self.client([f["response"]()])
        self.assertEqual(client._get_completion_content([]), "complete")
        self.assertTrue(witness.satisfied)
        self.assertEqual(len(sends), len(witness.calls))
        self.assertEqual(len(sends), 1)
        self.assertEqual(witness.calls[0]["raw_provider"], "bedrock")
        self.assertEqual(witness.calls[0]["raw_finish"], "end_turn")
        self.assertFalse(witness.calls[0]["exception"])
        self.assertEqual(witness.calls[0]["finish"], "stop")

    def test_gateway_counts_one_send_and_disables_sdk_retry(self):
        client, witness, sends, gateway, _ = self.client(
            [f["response"]()], gateway=True
        )
        client._get_completion_with_tools([], [], timeout_ms=1000)
        self.assertEqual([item[0] for item in sends], ["gateway"])
        self.assertEqual(len(witness.calls), 1)
        self.assertTrue(witness.satisfied)
        self.assertEqual(gateway.options[0]["max_retries"], 0)
        self.assertGreater(gateway.options[0]["timeout"], 0)
        self.assertLessEqual(gateway.options[0]["timeout"], 1)

    def test_gateway_error_never_falls_through_to_direct_provider(self):
        client, witness, sends, _, _ = self.client(
            [RuntimeError("synthetic"), f["response"]()], gateway=True
        )
        with self.assertRaises(RuntimeError):
            client._get_completion_with_tools([], [])
        self.assertEqual([item[0] for item in sends], ["gateway"])
        self.assertTrue(witness.failed)
        self.assertTrue(witness.calls[0]["exception"])
        with self.assertRaises(RuntimeError):
            client._get_completion_content([])
        self.assertEqual(len(sends), 1)

    def test_all_unknown_or_truncated_finishes_raise_and_stay_failed(self):
        for reason in (
            None,
            "",
            "length",
            "max_tokens",
            "unknown",
            "content_filter",
        ):
            with self.subTest(reason=reason):
                client, witness, sends, _, _ = self.client(
                    [f["response"](reason=reason), f["response"]()]
                )
                with self.assertRaises(RuntimeError):
                    client._get_completion_with_tools([], [])
                self.assertTrue(witness.failed)
                self.assertFalse(witness.satisfied)
                self.assertEqual(witness.calls[0]["raw_finish"], reason)
                self.assertFalse(witness.calls[0]["exception"])
                with self.assertRaises(RuntimeError):
                    client._get_completion_with_tools([], [])
                self.assertEqual(len(sends), 1)

    def test_empty_response_and_multiple_choices_fail_closed(self):
        for choices in ([], [f["response"]().choices[0]] * 2):
            client, witness, sends, _, _ = self.client(
                [SimpleNamespace(choices=choices)]
            )
            with self.assertRaises(RuntimeError):
                client._get_completion_content([])
            self.assertTrue(witness.failed)
            self.assertEqual(len(sends), len(witness.calls))

    def test_empty_content_cannot_complete(self):
        client, witness, _, _, _ = self.client([f["response"](content="")])
        with self.assertRaises(RuntimeError):
            client._get_completion_content([])
        self.assertTrue(witness.failed)

    def test_tool_response_is_intermediate_not_terminal(self):
        client, witness, sends, _, _ = self.client(
            [
                f["response"](reason="tool_calls"),
                f["response"](),
            ]
        )
        client._get_completion_with_tools([], [])
        self.assertFalse(witness.satisfied)
        client._get_completion_with_tools([], [])
        self.assertTrue(witness.satisfied)
        self.assertEqual(len(sends), len(witness.calls))
        self.assertEqual(len(sends), 2)

    def test_tool_response_is_invalid_for_content_only_client(self):
        client, witness, _, _, _ = self.client([f["response"](reason="tool_calls")])
        with self.assertRaises(RuntimeError):
            client._get_completion_content([])
        self.assertTrue(witness.failed)

    def test_actual_attempt_limit_is_strict(self):
        client, witness, sends, _, _ = self.client(
            [f["response"](), f["response"]()], limit=2
        )
        client._get_completion_with_tools([], [])
        with self.assertRaises(RuntimeError):
            client._get_completion_with_tools([], [])
        self.assertEqual(len(sends), 2)
        self.assertEqual(len(witness.calls), 2)
        self.assertFalse(witness.satisfied)

    def test_managed_helper_is_counted_once_and_never_retried(self):
        client, witness, sends, _, _ = self.client([f["response"]()])
        client._requires_managed_transport = lambda *args: True
        client.model_name = "turing_large"
        managed = []

        def managed_send(payload):
            managed.append(payload)
            raise RuntimeError("synthetic managed error")

        self.managed_send = managed_send
        with self.assertRaises(RuntimeError):
            client._get_completion_content([])
        self.assertEqual(len(managed), 1)
        self.assertEqual(sends, [])
        self.assertEqual(len(witness.calls), 1)
        self.assertTrue(witness.calls[0]["exception"])
        self.assertTrue(witness.failed)

    def test_managed_success_and_async_gateway_share_send_accounting(self):
        client, witness, sends, _, _ = self.client([])
        client._requires_managed_transport = lambda *args: True
        asyncio.run(
            client._try_gateway_completion_async(
                {"model": "turing_large", "messages": []}
            )
        )
        self.assertTrue(witness.satisfied)
        self.assertEqual(len(witness.calls), 1)
        self.assertEqual(sends, [])

    def test_async_content_failure_has_no_hidden_retry(self):
        client, witness, sends, _, _ = self.client(
            [RuntimeError("synthetic"), f["response"]()]
        )
        with self.assertRaises(RuntimeError):
            asyncio.run(client._get_completion_content_async([]))
        self.assertEqual(len(sends), 1)
        self.assertTrue(witness.failed)

    def test_async_content_success(self):
        client, witness, sends, _, _ = self.client([f["response"]()])
        self.assertEqual(
            asyncio.run(client._get_completion_content_async([])), "complete"
        )
        self.assertTrue(witness.satisfied)
        self.assertEqual(len(sends), len(witness.calls))

    def test_async_gateway_disables_sdk_retry(self):
        client, witness, sends, gateway, _ = self.client(
            [f["response"]()], gateway=True
        )
        asyncio.run(
            client._try_gateway_completion_async({"model": "offline", "messages": []})
        )
        self.assertTrue(witness.satisfied)
        self.assertEqual(len(sends), 1)
        self.assertEqual(gateway.options[0]["max_retries"], 0)

    def test_fallback_only_apis_refuse_without_a_send(self):
        for name, args in (
            ("_handle_final_fallback", ([], {})),
            ("_handle_protect_completion", ({},)),
            ("transcribe_audio", (b"offline",)),
        ):
            with self.subTest(method=name):
                client, witness, sends, _, _ = self.client([])
                with self.assertRaises(RuntimeError):
                    getattr(client, name)(*args)
                self.assertTrue(witness.failed)
                self.assertEqual(witness.calls, [])
                self.assertEqual(sends, [])
        client, witness, sends, _, _ = self.client([])
        with self.assertRaises(RuntimeError):
            asyncio.run(client._handle_final_fallback_async([], {}))
        self.assertTrue(witness.failed)
        self.assertEqual(sends, [])

    def test_opaque_sdk_refused_before_send(self):
        client, witness, sends, _, _ = self.client([])
        client._gateway_client = SimpleNamespace()
        with self.assertRaises(RuntimeError):
            client._get_completion_with_tools([], [])
        self.assertTrue(witness.failed)
        self.assertEqual(witness.calls, [])
        self.assertEqual(sends, [])

    def test_call_llm_uses_same_one_shot_content_path(self):
        client, witness, sends, _, _ = self.client([f["response"]()])
        self.assertEqual(client.call_llm([], "bedrock"), "complete")
        self.assertTrue(witness.satisfied)
        self.assertEqual(len(sends), 1)


if __name__ == "__main__":
    unittest.main()
