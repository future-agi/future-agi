"""Original provider wire -> raw adapter -> AgentLoop -> tool.run -> worker."""

import json
import runpy
import unittest
from pathlib import Path

HERE = Path(__file__).parent
p = runpy.run_path(str(HERE / "test_analysis_payloads_offline.py"))


def closure(fn):
    return dict(
        zip(
            fn.__code__.co_freevars,
            [c.cell_contents for c in fn.__closure__],
            strict=True,
        )
    )


class WirePayloads(p["OriginalArguments"]):
    variant = "healthy"

    def install(self):
        super().install()
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch

        self.order = []
        payloads = sys.modules["ee.agenthub.traceerroragent.background_payloads"]
        wire = sys.modules["ee.agenthub.traceerroragent.background_payload_wire"]
        adapter = sys.modules["ee.agenthub.traceerroragent.background_falcon"]
        scope = closure(self.agent_runner)["run"].__globals__

        def trace(fn, label):
            def wrapped(*a, **k):
                self.order.append(label)
                return fn(*a, **k)

            return wrapped

        replacements = (
            patch.object(
                payloads,
                "original_json",
                trace(payloads.original_json, "strict-original"),
            ),
            patch.object(
                wire, "validate_payload", trace(wire.validate_payload, "validate-wire")
            ),
            patch.object(
                wire,
                "consume_tool_payload",
                trace(wire.consume_tool_payload, "consume-provenance"),
            ),
            patch.dict(
                adapter.BackgroundFalconClient._raw_completion.__globals__,
                {"normalize_response": trace(adapter.normalize_response, "normalize")},
            ),
            patch.dict(
                scope,
                {
                    "json": SimpleNamespace(
                        loads=trace(json.loads, "agentloop-loads"),
                        dumps=json.dumps,
                        JSONDecodeError=json.JSONDecodeError,
                    )
                },
            ),
        )
        for replacement in replacements:
            replacement.start()
            self.addCleanup(replacement.stop)

    def worker(self, boundary):
        agent = self.agent_runner.agent
        old_execute = agent._execute_tool
        selected = closure(old_execute)["explore"]
        raw = closure(selected.execute)["raw"]
        wire = json.dumps(raw)
        key = {
            "submit_trace_scores": "overall_score",
            "submit_trace_finding": "confidence",
            "read_trace_span": "span_id",
            "explore_trace_legacy": "trace_id",
        }[selected.name]
        if self.variant == "duplicate":
            wire = "{" + json.dumps(key) + ":true," + wire[1:]
        elif self.variant == "trailing":
            wire += "{}"
        elif self.variant == "nonobject":
            wire = "[" + wire + "]"
        elif self.variant == "nan":
            wire = '{"unknown":NaN,' + wire[1:]
        self.original_wire = wire
        old_stream = agent.llm_client.stream_with_retry

        async def stream(*a, **k):
            async for chunk in old_stream(*a, **k):
                for choice in chunk.get("choices", []):
                    calls = choice.get("delta", {}).get("tool_calls", [])
                    # This test exercises precisely one actual canonical tool.
                    calls[:] = [
                        call
                        for call in calls
                        if call["function"]["name"] == selected.name
                    ]
                    for call in calls:
                        call["function"]["arguments"] = wire
                    if calls and self.variant in {"bad-sibling", "duplicate-id"}:
                        sibling = json.loads(json.dumps(calls[0]))
                        if self.variant == "bad-sibling":
                            sibling["id"] = "bad-sibling"
                            sibling["function"]["arguments"] = (
                                "{" + json.dumps(key) + ":true," + wire[1:]
                            )
                        calls.append(sibling)
                yield chunk

        agent.llm_client.stream_with_retry = stream

        async def execute(tool, args):
            if tool.name == selected.name:
                raw.clear()
                raw.update(args)
                if self.variant == "tampered-dict":
                    raw[key] = True
                elif self.variant == "missing-dict":
                    raw.clear()
            return await old_execute(tool, args)

        agent._execute_tool = execute
        value = super().worker(boundary)
        self.worker_result, self.effects, self.marker = value
        return value

    def test_duplicate_member_every_allowed_tool_aborts_before_side_effects(self):
        self.variant = "duplicate"
        for name in p["MODELS"]:
            with self.subTest(tool=name):
                witness = self.raw(name, {})
                self.assertTrue(witness.failed)
                self.assertEqual(self.effects, [])
                self.assertEqual(self.saved, [])
                self.assertEqual(self.tool_results, [])
                self.assertNotIn("agentloop-loads", self.order)
                self.assertNotIn("normalize", self.order)

    def test_other_invalid_original_wire_never_reaches_dispatch(self):
        for variant in ("trailing", "nonobject", "nan"):
            self.variant = variant
            for name in p["MODELS"]:
                with self.subTest(tool=name, variant=variant):
                    self.raw(name, {})
                    self.assertEqual(self.tool_results, [])

    def test_bad_sibling_or_duplicate_id_aborts_whole_batch_before_any_write(self):
        for variant in ("bad-sibling", "duplicate-id"):
            self.variant = variant
            for name in p["MODELS"]:
                with self.subTest(tool=name, variant=variant):
                    self.raw(name, {})
                    self.assertEqual(self.tool_results, [])

    def test_missing_or_changed_decoded_dictionary_cannot_substitute_for_wire(self):
        for variant in ("tampered-dict", "missing-dict"):
            self.variant = variant
            for name in p["MODELS"]:
                with self.subTest(tool=name, variant=variant):
                    self.raw(name, {})
                    self.assertEqual(self.tool_results, [])

    def test_valid_original_wire_all_tools_complete(self):
        self.variant = "healthy"
        for name in p["MODELS"]:
            with self.subTest(tool=name):
                self.raw(name, {}, success=True)
                steps = (
                    "strict-original",
                    "normalize",
                    "agentloop-loads",
                    "consume-provenance",
                )
                self.assertEqual(
                    sorted(self.order.index(step) for step in steps),
                    [self.order.index(step) for step in steps],
                )
                witness = self.worker_agent.llm_client._witness
                self.assertTrue(witness.wire_calls)
                self.assertTrue(
                    all(call.consumed for call in witness.wire_calls.values())
                )


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        WirePayloads(name) for name in vars(WirePayloads) if name.startswith("test_")
    )


if __name__ == "__main__":
    unittest.main()
