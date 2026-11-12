"""Real LLM retries cannot hide failed background attempts."""

import ast
import runpy
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).parent
fixture = runpy.run_path(str(HERE / "analysis_llm_fixture.py"))
j = runpy.run_path(str(HERE / "test_analysis_judge_completion_offline.py"))
original_definition = j["definition"]


class ProviderAttempts(j["JudgeCompletion"]):
    def replay(self, failure=None, legacy=False, gateway=False, mode="success"):
        attempts, witnesses, gateways = [], [], []
        active = {}

        def send(route, kwargs):
            label = active["label"]
            attempts.append(label)
            if failure == label and attempts.count(label) == 1:
                raise RuntimeError("synthetic first provider failure")
            if label == "chauffeur":
                return fixture["response"](
                    '{"sub_flows":[{"name":"offline","spans":["span"],"summary":"Answered query","flags":[]}],"trace_overview":"Answered query"}'
                )
            return active["target"].provider_completion(**kwargs)

        LLM, adapter_ns, _ = fixture["clients"](send)
        adapter_module = j["module"]("background_llm", **adapter_ns)
        config = SimpleNamespace(
            provider="bedrock", model_name="offline", temperature=0, max_tokens=100
        )
        constructor_ns = {
            "LLM": LLM,
            "threading": threading,
            "AGENT_COMPASS_SPAN_CONTENT_THRESHOLD": 3000,
            "AGENT_COMPASS_JUDGE_MAX_TURNS": 4,
            "ModelConfigs": SimpleNamespace(
                HAIKU_4_5_BEDROCK_ARN=config, SONNET_4_5_BEDROCK_ARN=config
            ),
        }

        def definition(path, name, ns, parent=None):
            if name == "_summarize_logic_v2":
                for label, clsname in (
                    ("chauffeur", "ChauffeurAgent"),
                    ("judge", "JudgeAgent"),
                ):
                    factory = ns[clsname]

                    def construct(
                        *, _label=label, _factory=factory, _clsname=clsname, **kwargs
                    ):
                        target = _factory(**{**kwargs, "completion_witness": None})
                        if _label == "judge":
                            target.provider_completion = (
                                target.llm._get_completion_with_tools
                            )
                        initialized = SimpleNamespace()
                        init = original_definition(
                            f"ee/agenthub/traceerroragent/{_label}.py",
                            "__init__",
                            constructor_ns,
                            _clsname,
                        )
                        init(initialized, **kwargs)
                        target.llm = initialized.llm
                        target.completion_witness = kwargs.get("completion_witness")
                        active.update(label=_label, target=target)
                        witness = kwargs.get("completion_witness")
                        if witness is not None:
                            witnesses.append(witness)
                        if gateway:
                            transport = fixture["Gateway"](send)
                            gateways.append(transport)
                            target.llm._gateway_client = transport
                        return target

                    ns[clsname] = construct
            method = original_definition(path, name, ns, parent)
            if name == "run" and path.endswith(("/judge.py", "/chauffeur.py")):

                def run(agent, *args, **kwargs):
                    wrapped = agent.llm
                    if hasattr(wrapped, "_witness"):
                        witnesses.append(wrapped._witness)
                    return method(agent, *args, **kwargs)

                return run
            return method

        with (
            patch.dict(
                j["JudgeCompletion"].execute.__globals__, {"definition": definition}
            ),
            patch.dict(
                "sys.modules",
                {"ee.agenthub.traceerroragent.background_llm": adapter_module},
            ),
        ):
            outcome, calls, cache = self.execute(mode, legacy=legacy)
        return outcome, calls, cache, attempts, witnesses, gateways

    def assert_failed_attempt(self, label, gateway=False):
        outcome, _, cache, attempts, witnesses, gateways = self.replay(
            label, gateway=gateway
        )
        self.assertFalse(outcome["success"], outcome)
        self.assertEqual(self.state.get_state(self.trace).status, "aborted")
        self.assertIsNone(self.state.get_state(self.trace).result_analysis_id)
        self.assertEqual(self.parents, [], "no partial candidate persisted")
        self.assertEqual(self.patterns, [], "no failed analysis teaches")
        cache.clear.assert_not_called()
        self.assertEqual(
            attempts, ["chauffeur", "judge"] if label == "judge" else ["chauffeur"]
        )
        witness = witnesses[0]
        self.assertTrue(witness.failed)
        self.assertFalse(witness.satisfied)
        self.assertEqual(len(witness.calls), len(attempts))
        self.assertTrue(witness.calls[-1]["exception"])
        if gateway:
            self.assertTrue(gateways[-1].options)
            self.assertEqual(gateways[-1].options[-1]["max_retries"], 0)

    def test_caught_judge_client_exception_aborts(self):
        self.assert_failed_attempt("judge")

    def test_caught_judge_gateway_exception_aborts(self):
        self.assert_failed_attempt("judge", gateway=True)

    def test_caught_chauffeur_content_exception_aborts(self):
        self.assert_failed_attempt("chauffeur")

    def test_actual_clients_success_completes_and_learns(self):
        outcome, _, cache, attempts, witnesses, _ = self.replay()
        self.assertTrue(outcome["success"], outcome)
        self.assertEqual(self.state.get_state(self.trace).status, "completed")
        self.assertEqual(len(self.patterns), 1)
        self.assertEqual(attempts, ["chauffeur", "judge", "judge"])
        self.assertEqual(len(witnesses[0].calls), len(attempts))
        self.assertTrue(witnesses[0].satisfied)
        cache.clear.assert_called_once()

    def test_actual_gateway_success_completes_and_learns(self):
        outcome, _, _, attempts, witnesses, gateways = self.replay(gateway=True)
        self.assertTrue(outcome["success"], outcome)
        self.assertEqual(self.state.get_state(self.trace).status, "completed")
        self.assertEqual(len(self.patterns), 1)
        self.assertTrue(witnesses[0].satisfied)
        self.assertEqual(len(witnesses[0].calls), len(attempts))
        self.assertTrue(
            all(
                option["max_retries"] == 0
                for gateway in gateways
                for option in gateway.options
            )
        )

    def assert_bad_finish(self, mode):
        outcome, _, cache, attempts, witnesses, _ = self.replay(mode=mode)
        self.assertFalse(outcome["success"], outcome)
        self.assertEqual(self.state.get_state(self.trace).status, "aborted")
        self.assertEqual(self.parents, [])
        self.assertEqual(self.patterns, [])
        cache.clear.assert_not_called()
        self.assertTrue(witnesses[0].failed)
        self.assertEqual(len(witnesses[0].calls), len(attempts))

    def test_actual_client_truncation_aborts_before_persistence(self):
        self.assert_bad_finish("max_tokens")

    def test_actual_client_unknown_finish_aborts_before_persistence(self):
        self.assert_bad_finish("unknown")

    def test_actual_client_empty_response_aborts_before_persistence(self):
        self.assert_bad_finish("empty")

    def test_foreground_client_retries_unchanged(self):
        outcome, _, _, attempts, witnesses, _ = self.replay("judge", legacy=True)
        self.assertTrue(outcome["success"], outcome)
        self.assertEqual(attempts, ["chauffeur", "judge", "judge"])
        self.assertEqual(witnesses, [])
        self.assertIsNone(self.state.get_state(self.trace))
        self.assertEqual(len(self.patterns), 1)

    def test_foreground_gateway_retries_unchanged(self):
        outcome, _, _, attempts, witnesses, _ = self.replay(
            "judge", legacy=True, gateway=True
        )
        self.assertTrue(outcome["success"], outcome)
        self.assertEqual(attempts, ["chauffeur", "judge", "judge"])
        self.assertEqual(witnesses, [])

    def test_foreground_content_retries_unchanged(self):
        outcome, _, _, attempts, witnesses, _ = self.replay("chauffeur", legacy=True)
        self.assertTrue(outcome["success"], outcome)
        self.assertEqual(attempts, ["chauffeur", "chauffeur", "judge"])
        self.assertEqual(witnesses, [])


class RetryCoverage(unittest.TestCase):
    def test_every_except_retry_or_fallback_method_is_overridden(self):
        tree = ast.parse(fixture["LLM_PATH"].read_text())
        cls = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "LLM"
        )
        retry_methods = set()
        # Catch retry loops, exception-handler fallback calls, and sequential
        # provider try-blocks. Batch evaluation loops are not provider retries.
        for method in cls.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nodes = list(ast.walk(method))
            handlers = [node for node in nodes if isinstance(node, ast.ExceptHandler)]
            sends = [
                node
                for node in nodes
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and (
                    node.func.attr in {"create", "post", "completion", "acompletion"}
                    or "completion" in node.func.attr
                    or "transcription" in node.func.attr
                )
            ]
            loops = any(
                isinstance(node, (ast.For, ast.While, ast.AsyncFor)) for node in nodes
            )
            if handlers and sends and (loops or len(sends) > 1):
                retry_methods.add(method.name)
        self.assertTrue(
            {
                "_get_completion_with_tools",
                "_get_completion_content",
                "_try_gateway_completion",
            }
            <= retry_methods
        )
        adapter = fixture["ADAPTER_PATH"]
        self.assertTrue(adapter.exists(), "background one-shot adapter is missing")
        adapter_cls = next(
            node
            for node in ast.parse(adapter.read_text()).body
            if isinstance(node, ast.ClassDef) and node.name == "BackgroundLLM"
        )
        overrides = {
            node.name
            for node in adapter_cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(
            retry_methods - overrides,
            set(),
            f"uncovered provider retry methods: {sorted(retry_methods - overrides)}",
        )


if __name__ == "__main__":
    unittest.main()
