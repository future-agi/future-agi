"""Socket-free controls for the actual worker admission functions; no Django."""

import ast
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "agentic_eval/core_evals/fi_utils/sandbox.py"


def load_worker():
    tree = ast.parse(SOURCE.read_text())
    names = {
        "_local_fallback_allowed",
        "execute_sandboxed_python",
        "execute_sandboxed_javascript",
    }
    tree.body = [
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names
    ]
    temp = Mock()
    temp.NamedTemporaryFile.return_value.__enter__ = Mock(
        return_value=Mock(name="synthetic")
    )
    temp.NamedTemporaryFile.return_value.__exit__ = Mock(return_value=False)
    runner = Mock(
        return_value=SimpleNamespace(stdout='{"status":"success"}', returncode=0)
    )
    ns = {
        "DEFAULT_TIMEOUT_SECONDS": 1,
        "MAX_OUTPUT_BYTES": 1024,
        "EXECUTOR_UNAVAILABLE_MESSAGE": "synthetic executor unavailable",
        "LOCAL_NO_NODE_MESSAGE": "synthetic node unavailable",
        "settings": SimpleNamespace(
            CODE_EXECUTOR_LOCAL_FALLBACK=True, CLOUD_DEPLOYMENT="self-hosted"
        ),
        "is_cloud_env": lambda value: value == "cloud",
        "logger": Mock(),
        "_call_executor_service": Mock(return_value=None),
        "_build_python_sandbox_script": Mock(return_value="synthetic"),
        "_build_js_sandbox_single_file": Mock(return_value="synthetic"),
        "_set_resource_limits": Mock(),
        "tempfile": temp,
        "subprocess": SimpleNamespace(run=runner, TimeoutExpired=TimeoutError),
        "os": Mock(),
        "sys": SimpleNamespace(executable="synthetic-python"),
        "json": json,
    }
    exec(compile(tree, str(SOURCE), "exec"), ns)
    return ns, runner


class WorkerBoundaryControls(unittest.TestCase):
    def test_unreachable_executor_never_falls_back_to_worker_network(self):
        for language in ("python", "javascript"):
            for deployment in ("self-hosted", "cloud", ""):
                with self.subTest(language=language, deployment=deployment):
                    ns, runner = load_worker()
                    ns["settings"].CLOUD_DEPLOYMENT = deployment
                    result = ns[f"execute_sandboxed_{language}"]("synthetic", {})
                    runner.assert_not_called()
                    self.assertEqual(result["status"], "error")
                    self.assertTrue(result["setup_error"])
                    self.assertEqual(result["error_code"], "EGRESS_POLICY_UNAVAILABLE")

    def test_local_opt_in_is_not_a_boundary_attestation(self):
        for enabled in (False, True):
            ns, _ = load_worker()
            ns["settings"].CODE_EXECUTOR_LOCAL_FALLBACK = enabled
            self.assertFalse(ns["_local_fallback_allowed"]("python"))

    def test_executor_success_and_typed_refusal_are_preserved(self):
        for language in ("python", "javascript"):
            for answer in (
                {"status": "success", "data": "synthetic"},
                {"status": "error", "error_code": "EGRESS_POLICY_UNAVAILABLE"},
            ):
                with self.subTest(language=language, answer=answer):
                    ns, runner = load_worker()
                    ns["_call_executor_service"].return_value = answer
                    self.assertIs(
                        ns[f"execute_sandboxed_{language}"]("synthetic", {}), answer
                    )
                    runner.assert_not_called()


def deny_sockets(event, args):
    if event.startswith("socket."):
        raise PermissionError("socket-free control boundary")


if __name__ == "__main__":
    sys.addaudithook(deny_sockets)
    try:
        sys.audit("socket.connect", None, ("synthetic", 443))
    except PermissionError:
        pass
    else:
        raise AssertionError("socket guard did not refuse")
    unittest.main()
