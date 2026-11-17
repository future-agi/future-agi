"""Actual AgentLoop + worker completion contracts. Socket-blocked; modeled ORM."""

import asyncio
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
boundaries = runpy.run_path(
    str(Path(__file__).with_name("test_falcon_background_boundaries_offline.py"))
)
fixture = runpy.run_path(str(Path(__file__).with_name("analysis_stream_fixture.py")))


class FalconCompletion(boundaries["BoundaryTests"]):
    def install(self):
        model = self.modules.get("tracer.models.trace_error_analysis")
        if model is not None and hasattr(self, "unscored"):
            query = model.TraceErrorAnalysis.drafts
            read = query.first.side_effect

            def first():
                analysis = read()
                if self.unscored:
                    analysis.overall_score = None
                analysis.save = lambda **kw: self.saved.append(analysis.overall_score)
                return analysis

            query.first.side_effect = first
            model.TraceErrorDetail = SimpleNamespace(
                drafts=SimpleNamespace(
                    filter=lambda **kw: SimpleNamespace(
                        filter=lambda **where: SimpleNamespace(
                            count=lambda: int(where["impact"] == "HIGH")
                        )
                    )
                )
            )
        super().install()

    def configure(self, mode, context=None):
        submitted = []
        invoke, background = fixture["stream_failure_runner"](
            context or self.context, submitted
        )
        agent = invoke.agent
        calls = 0

        async def stream(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call",
                                        "function": {
                                            "name": "submit_trace_finding",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            elif mode == "exception":
                raise RuntimeError("synthetic terminal provider exception")
            elif mode == "empty":
                return
            else:
                reason = "stop" if mode in ("success", "fallback", "limit") else mode
                yield {
                    "choices": [
                        {"delta": {"content": "analysis"}, "finish_reason": reason}
                    ]
                }
                yield {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}

        agent.llm_client.stream_with_retry = stream
        if mode == "limit":
            # Even a stop on the final allowed call is not a witness.
            agent.MAX_ITERATIONS = 2
        if mode == "exhaustion":
            agent.MAX_ITERATIONS = 1
        self.agent_runner = invoke
        self.modules["ai_tools.background"] = background
        self.unscored = mode != "success"
        self.saved = []
        return invoke, background, submitted

    def check_aborted(self, mode):
        _, _, submitted = self.configure(mode)
        self.finalizer_read = Mock()
        result, effects, marker = self.worker(None)
        self.assertEqual(submitted, ["unscored finding"])
        self.assertFalse(result["success"], result)
        self.assertEqual(result.get("code"), "analysis_failed")
        marker.abort.assert_called_once()
        marker.complete.assert_not_called()
        self.assertEqual(self.saved, [])
        self.assertEqual(effects, [])
        if mode != "fallback":
            self.finalizer_read.assert_not_called()

    def test_max_tokens_aborts(self):
        self.check_aborted("max_tokens")

    def test_length_aborts(self):
        self.check_aborted("length")

    def test_empty_stream_aborts(self):
        self.check_aborted("empty")

    def test_iteration_exhaustion_aborts(self):
        self.check_aborted("exhaustion")

    def test_stop_at_iteration_limit_aborts(self):
        self.check_aborted("limit")

    def test_swallowed_provider_exception_aborts(self):
        self.check_aborted("exception")

    def test_stop_without_scores_cannot_use_fallback(self):
        self.check_aborted("fallback")

    def test_successful_actual_loop_publishes_and_exports(self):
        self.configure("success")
        result, effects, marker = self.worker(None)
        self.assertTrue(result["success"], result)
        marker.complete.assert_called_once()
        marker.abort.assert_not_called()
        self.assertEqual(effects, ["completed", "embedding", "success-log", "cluster"])

    def test_interactive_termination_forms_still_return_normally(self):
        for mode in ("max_tokens", "length", "empty", "exhaustion", "exception"):
            with self.subTest(mode=mode):
                invoke, background, submitted = self.configure(mode, SimpleNamespace())
                events = []

                async def callback(event, events=events):
                    events.append(event)

                with patch.dict("sys.modules", {"ai_tools.background": background}):
                    result = asyncio.run(
                        invoke(
                            user_message="analyze",
                            history_messages=[],
                            send_callback=callback,
                        )
                    )
                self.assertEqual(submitted, ["unscored finding"])
                self.assertNotIn("success", result)
                self.assertNotIn("outcome", result)
                self.assertEqual(
                    sum(e["type"] == "error" for e in events), int(mode == "exception")
                )


if __name__ == "__main__":
    unittest.main()
