"""Closed background client/terminal-normalizer census; changes require review."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OWNED = ROOT / "ee/agenthub/traceerroragent"


def methods(path, name):
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    return {
        n.name: n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class RawCoverage(unittest.TestCase):
    def test_every_falcon_terminal_normalizer_is_replaced(self):
        base = methods(ROOT / "ee/falcon_ai/llm_client.py", "FalconLLMClient")
        sites = {}
        for name, method in base.items():
            hits = [
                n
                for n in ast.walk(method)
                if isinstance(n, ast.Constant)
                and n.value in ("finish_reason", "stop_reason", "[DONE]")
            ]
            if hits:
                sites[name] = len(hits)
        # Exact occurrence counts catch a new normalization inside an existing
        # method too, rather than only noticing newly named methods.
        self.assertEqual(
            sites,
            {
                "_stream_managed": 2,
                "_stream_bedrock": 5,
                "_stream_anthropic": 7,
                "_stream_openai": 1,
            },
        )
        path = OWNED / "background_falcon.py"
        self.assertTrue(path.exists(), "raw Falcon evidence adapter missing")
        overrides = methods(path, "BackgroundFalconClient")
        self.assertFalse(set(sites) - set(overrides))
        for name in sites:
            self.assertIn("_raw_completion", ast.unparse(overrides[name]))

    def test_every_generation_client_has_raw_adapter_wiring(self):
        constructors = {}
        for name in ("judge", "chauffeur"):
            init = methods(
                OWNED / f"{name}.py",
                "JudgeAgent" if name == "judge" else "ChauffeurAgent",
            )["__init__"]
            calls = {
                n.id
                for n in ast.walk(init)
                if isinstance(n, ast.Name) and n.id.endswith("LLM")
            }
            constructors[name] = calls
        self.assertEqual(
            constructors,
            {"judge": {"LLM", "BackgroundLLM"}, "chauffeur": {"LLM", "BackgroundLLM"}},
        )
        agent = methods(ROOT / "ee/falcon_ai/agent.py", "AgentLoop")
        clients = {
            n.func.id
            for n in ast.walk(agent["__init__"])
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id.endswith("Client")
        }
        self.assertEqual(clients, {"FalconLLMClient"})
        wrapper = methods(
            ROOT / "tracer/services/analysis_result_completion.py", "RecordingLLM"
        )
        self.assertIn(
            "BackgroundFalconClient", ast.unparse(wrapper["stream_with_retry"])
        )
        llm = methods(OWNED / "background_llm.py", "BackgroundLLM")
        self.assertIn("direct_completion", ast.unparse(llm["_complete"]))
        self.assertNotIn("litellm.completion", ast.unparse(llm["_complete"]))
        worker = (ROOT / "tracer/tasks/error_analysis.py").read_text()
        self.assertIn("RecordingLLM(agent.llm_client, witness, raw=True)", worker)

    def test_new_clients_anywhere_in_trace_analysis_require_census_update(self):
        census = {}
        for path in OWNED.glob("*.py"):
            calls = [
                node
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and (node.func.id.endswith("LLM") or node.func.id == "llm_class")
            ]
            if calls:
                census[path.name] = len(calls)
        self.assertEqual(
            census,
            {
                "judge.py": 1,
                "chauffeur.py": 1,
                "voice_compass.py": 3,
                "background_tools.py": 1,
            },
        )
        # VoiceCompass is a legacy caller, not a lifecycle background dispatch.
        for path in (OWNED / "traceerror.py", ROOT / "tracer/tasks/error_analysis.py"):
            self.assertNotIn("VoiceCompass", path.read_text())

    def test_new_normalization_is_dominated_by_raw_observation(self):
        for filename, cls, method in (
            ("background_falcon.py", "BackgroundFalconClient", "_raw_completion"),
            ("background_llm.py", "BackgroundLLM", "_attempt"),
        ):
            node = methods(OWNED / filename, cls)[method]
            observe = [
                n.lineno
                for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "observe_raw"
            ]
            normalize = [
                n.lineno
                for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "normalize_response"
            ]
            self.assertEqual(len(observe), 1)
            self.assertEqual(len(normalize), 1)
            self.assertLess(observe[0], normalize[0])


if __name__ == "__main__":
    unittest.main()
