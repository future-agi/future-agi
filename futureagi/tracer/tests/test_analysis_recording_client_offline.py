"""Positive witness invariants; no providers, sockets or database."""

import asyncio
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
ns = runpy.run_path(str(ROOT / "tracer/services/analysis_result_completion.py"))
Witness, Recording, Incomplete = (
    ns["CompletionWitness"],
    ns["RecordingLLM"],
    ns["IncompleteAnalysis"],
)
support = runpy.run_path(
    str(Path(__file__).with_name("test_analysis_legacy_snapshot_offline.py"))
)


def chunk(reason):
    return {"choices": [{"finish_reason": reason, "delta": {"content": "synthetic"}}]}


class RecordingTests(unittest.TestCase):
    def stream(self, chunks, witness=None, error=None):
        witness = witness or Witness(10)

        async def raw(*args):
            for item in chunks:
                yield item
            if error:
                raise error

        if not hasattr(witness, "test_client"):
            witness.test_client = Recording(
                SimpleNamespace(stream_with_retry=raw), witness
            )
        client = witness.test_client
        client._client.stream_with_retry = raw

        async def consume():
            return [item async for item in client.stream_with_retry([])]

        try:
            asyncio.run(consume())
        except RuntimeError:
            pass  # Exactly the caller swallowing that the recorder must defeat.
        return witness

    def test_only_documented_normal_finishes_can_complete(self):
        for reason in (
            "stop",
            "end_turn",
            "tool_calls",
            "tool_use",
            "length",
            "max_tokens",
            "content_filter",
            "error",
            "unknown",
            "",
            None,
        ):
            with self.subTest(reason=reason):
                witness = self.stream(
                    [chunk(reason), {"usage": {"completion_tokens": 1}}]
                )
                self.assertEqual(witness.satisfied, reason in ("stop", "end_turn"))

    def test_failed_prior_call_cannot_be_repaired_by_later_stop(self):
        for chunks, error in (
            ([], None),
            ([chunk("length")], None),
            ([chunk("stop")], RuntimeError("synthetic")),
        ):
            with self.subTest(chunks=chunks, error=error):
                witness = self.stream(chunks, error=error)
                self.stream([chunk("stop")], witness)
                self.assertFalse(witness.satisfied)

    def test_finish_is_not_reused_for_markerless_trailing_content(self):
        self.assertFalse(self.stream([chunk("stop"), chunk(None)]).satisfied)

    def test_normal_tool_round_then_final_stop_is_sufficient(self):
        witness = self.stream([chunk("tool_calls")])
        self.stream([chunk("stop")], witness)
        self.assertTrue(witness.satisfied)
        witness.require()
        with self.assertRaises(Incomplete):
            witness.require(fallback=True)
        self.assertFalse(witness.satisfied)

    def test_limit_is_strict_and_zero_calls_never_complete(self):
        for limit in (None, 0, 1, 2):
            witness = Witness(limit)
            self.assertFalse(witness.satisfied)
            self.stream([chunk("stop")], witness)
            self.assertEqual(witness.satisfied, limit == 2)

    def test_bypass_falcon_retry_hides_no_provider_exception(self):
        calls = []

        async def raw(*args):
            calls.append("raw")
            raise RuntimeError("synthetic")
            yield  # Make the stub an async generator.

        async def retry(*args):
            calls.append("retry")
            yield chunk("stop")

        witness = Witness(3)
        client = Recording(
            SimpleNamespace(stream_completion=raw, stream_with_retry=retry), witness
        )

        async def consume():
            return [item async for item in client.stream_with_retry([])]

        with self.assertRaises(RuntimeError):
            asyncio.run(consume())
        self.assertEqual(calls, ["raw"])
        self.assertFalse(witness.satisfied)

    def test_auxiliary_success_cannot_mask_unfinished_primary(self):
        witness = self.stream([chunk("tool_calls")])
        client = Recording(
            SimpleNamespace(_get_completion_with_tools=lambda: chunk("stop")), witness
        )
        client._get_completion_with_tools()
        self.assertFalse(witness.satisfied)
        self.stream([chunk("stop")], witness)
        self.assertTrue(witness.satisfied)

    def test_string_api_must_refresh_its_terminal_metadata(self):
        client = SimpleNamespace(
            last_finish_reason="stop", _get_completion_content=lambda: "text"
        )
        witness = Witness(3)
        Recording(client, witness)._get_completion_content()
        self.assertFalse(witness.satisfied)

    def test_judge_score_and_summary_defaults_are_explicit_fallbacks(self):
        for method in ("_tool_submit_scores", "_tool_submit_summary"):
            witness = self.stream([chunk("stop")])
            agent = SimpleNamespace(completion_witness=witness, errors=[])
            submit = support["definition"](
                "ee/agenthub/traceerroragent/judge.py",
                method,
                {"Any": object},
                "JudgeAgent",
            )
            submit(agent)
            self.assertTrue(witness.fallback_used)
            self.assertFalse(witness.satisfied)
            legacy = SimpleNamespace(errors=[])
            self.assertEqual(submit(legacy)["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
