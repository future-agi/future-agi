"""Source contract plus real, fresh-process import proof (no LLM mocks).

Run without project conftest for a local, no-configuration diagnostic. Missing
Django/dependencies is SETUP_FAILURE, never an import RED or GREEN. An admitted
integration runner can set PROMPT_OPTIMIZER_PROBE_SETTINGS to its synthetic test
settings module; neither this file nor the probe reads developer settings.
"""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
SOURCE = BACKEND / "ee/agenthub/prompt_optimizer_agent/agent_task_v2.py"
PROBE = HERE / "_prompt_optimizer_import_probe.py"


def run_probe(*args):
    command = [sys.executable, "-I", "-B", str(PROBE), *args]
    settings_module = os.environ.get("PROMPT_OPTIMIZER_PROBE_SETTINGS")
    if settings_module and "--calibrate" not in args:
        command.extend(["--django-settings", settings_module])
    # Local mode must not inherit developer keys or service destinations. Only
    # the explicit admitted-fixture mode retains the runner's synthetic config.
    environment = dict(os.environ) if settings_module else {}
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "GIT_NO_REPLACE_OBJECTS": "1",
                        "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent",
                        "CLOUD_DEPLOYMENT": "DEV", "LITELLM_LOCAL_MODEL_COST_MAP": "True"})
    result = subprocess.run(command, capture_output=True, text=True, timeout=90,
                            env=environment, cwd="/tmp")
    for line in reversed(result.stdout.splitlines()):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and "status" in record:
            record["processExitCode"] = result.returncode
            return record
    return {"status": "SETUP_FAILURE", "processExitCode": result.returncode,
            "errorType": "ProbeDidNotReturnEvidence"}


class PromptOptimizerSourceContractTests(unittest.TestCase):
    def test_no_module_scope_llm_construction(self):
        """Static guard only: this does not certify successful runtime import."""
        tree = ast.parse(SOURCE.read_text())
        eager = [node.lineno for node in tree.body
                 if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr))
                 and isinstance(node.value, ast.Call)
                 and isinstance(node.value.func, ast.Name)
                 and node.value.func.id == "LLM"]
        self.assertEqual(eager, [], "LLM construction must occur only at actual use")


class PromptOptimizerRealImportTests(unittest.TestCase):
    def test_observer_calibration(self):
        result = run_probe("--calibrate")
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(len(result["attempts"]), 7)
        self.assertEqual(len(result["children"]), 1)

    def assert_import_is_inert(self, target):
        result = run_probe("--target", target)
        self.assertEqual(result["status"], "PASS", result)
        self.assertTrue(result["importCompleted"])
        self.assertEqual(result["processExitCode"], 0)
        for name in ("attempts", "constructions", "activations", "children"):
            self.assertEqual(result[name], [], name)

    def test_real_optimizer_import_has_no_client_or_network(self):
        self.assert_import_is_inert("ee.agenthub.prompt_optimizer_agent.agent_task_v2")

    def test_real_task_package_import_has_no_client_or_network(self):
        self.assert_import_is_inert("model_hub.tasks")
