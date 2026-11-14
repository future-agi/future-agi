"""The link allowlist is deployment policy, never evaluator input."""

import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load_executor():
    falcon = types.ModuleType("falcon")
    falcon.App = lambda: mock.Mock()
    with mock.patch.dict(sys.modules, {"falcon": falcon}):
        spec = importlib.util.spec_from_file_location(
            "link_policy_executor", ROOT / "futureagi/code-executor/server.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def test_python_child_inherits_only_operator_policy():
    with mock.patch.dict(os.environ, {"LINK_EVAL_ALLOWED_HOSTS": "allowed.example"}):
        executor = load_executor()
        result = executor._execute_python_fallback(
            "import os\ndef evaluate(**kwargs):\n"
            "    return {'hosts': os.environ.get('LINK_EVAL_ALLOWED_HOSTS')}\n",
            {
                "LINK_EVAL_ALLOWED_HOSTS": "other.example",
                "allowed_hosts": ["other.example"],
            },
            10,
        )
    assert result == {"status": "success", "data": {"hosts": "allowed.example"}}


def test_python_child_without_policy_denies_by_default():
    with mock.patch.dict(os.environ, {"LINK_EVAL_ALLOWED_HOSTS": ""}):
        executor = load_executor()
        result = executor._execute_python_fallback(
            "import os\ndef evaluate(**kwargs):\n"
            "    return {'hosts': os.environ.get('LINK_EVAL_ALLOWED_HOSTS')}\n",
            {"allowed_hosts": ["other.example"]},
            10,
        )
    assert result == {"status": "success", "data": {"hosts": ""}}


def test_nsjail_forwards_only_the_operator_allowlist():
    with mock.patch.dict(
        os.environ,
        {
            "LINK_EVAL_ALLOWED_HOSTS": "allowed.example",
            "SYNTHETIC_SECRET": "must-not-forward",
        },
    ):
        executor = load_executor()
        with (
            mock.patch.object(executor.os, "makedirs"),
            mock.patch("builtins.open", mock.mock_open()),
            mock.patch.object(executor.os, "unlink"),
            mock.patch.object(executor.subprocess, "run") as run,
        ):
            run.return_value = types.SimpleNamespace(
                stdout='{"status":"success","data":1}', stderr="", returncode=0
            )
            executor._execute_python_nsjail(
                "def evaluate(**kw): return 1", {"allowed_hosts": ["other.example"]}, 10
            )
        argv = run.call_args.args[0]
        assert (
            argv[argv.index("--env") + 1] == "LINK_EVAL_ALLOWED_HOSTS=allowed.example"
        )
        assert "must-not-forward" not in " ".join(str(item) for item in argv)
        assert "--keep_env" not in argv
