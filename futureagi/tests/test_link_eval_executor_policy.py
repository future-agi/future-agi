"""Link checks stay offline inside the real executor child, without host policy."""

import importlib.util
import os
import sys
import textwrap
import types
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAMES = ("contains_valid_link", "no_invalid_links")


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


def guarded_code(name):
    path = ROOT / "futureagi/model_hub/system_evals/function" / (name + ".yaml")
    code = textwrap.dedent(path.read_text().split("  code: |\n", 1)[1])
    # Patch inside the subprocess, not in its parent. Assertions after evaluate
    # catch an attempted call even if the evaluator swallows the raised error.
    return code + textwrap.dedent("""
        import http.client
        import urllib.request
        from contextlib import ExitStack
        from unittest import mock

        shipped_evaluate = evaluate
        def evaluate(**kwargs):
            with ExitStack() as stack:
                calls = [stack.enter_context(mock.patch(target,
                    side_effect=AssertionError("must stay offline"))) for target in (
                    "socket.socket", "socket.create_connection", "socket.getaddrinfo",
                    "socket.gethostbyname", "socket.gethostbyname_ex",
                    "http.client.HTTPConnection", "http.client.HTTPSConnection",
                    "urllib.request.urlopen",
                )]
                result = shipped_evaluate(**kwargs)
                for call in calls:
                    call.assert_not_called()
                return result
    """)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "text,scores",
    [
        ("https://never-resolved.example/ok", (1.0, 1.0)),
        ("www.example.com http://example.com:5432/path", (1.0, 1.0)),
        ("http://bad-.example/ https://example.com/", (1.0, 0.0)),
        ("http://127.0.0.1/private", (0.0, 0.0)),
        ("http://private.example/private", (1.0, 1.0)),
        ("http://example.com:65536/", (0.0, 0.0)),
        ("//example.com/path", (0.0, 0.0)),
        ("Contact support@example.com; value 3.14", (0.0, 1.0)),
        (" ".join(f"https://example.com/{i}" for i in range(21)), (0.0, 0.0)),
    ],
)
def test_shipped_link_checks_are_offline_in_python_child(name, text, scores):
    executor = load_executor()
    result = executor._execute_python_fallback(
        guarded_code(name),
        {"input": None, "output": text, "expected": None, "context": None},
        10,
    )
    assert result["status"] == "success", result
    # The executor normalizes YAML `score` to its public `result` field.
    assert result["data"]["result"] == scores[NAMES.index(name)]


@pytest.mark.parametrize("setting", ["", "allowed.example"])
def test_python_child_does_not_inherit_retired_link_setting(setting):
    with mock.patch.dict(os.environ, {"LINK_EVAL_ALLOWED_HOSTS": setting}):
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
    assert result == {"status": "success", "data": {"hosts": None}}
    assert "LINK_EVAL_ALLOWED_HOSTS" not in executor.FALLBACK_ENV


def test_nsjail_does_not_forward_retired_link_setting_or_other_environment():
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
            mock.patch.object(executor.os, "remove"),
            mock.patch.object(executor.subprocess, "run") as run,
        ):
            run.return_value = types.SimpleNamespace(
                stdout='{"status":"success","data":1}', stderr="", returncode=0
            )
            executor._execute_python_nsjail(
                "def evaluate(**kw): return 1", {"allowed_hosts": ["other.example"]}, 10
            )
        argv = run.call_args.args[0]
        assert not any("LINK_EVAL_ALLOWED_HOSTS" in str(item) for item in argv)
        assert "must-not-forward" not in " ".join(str(item) for item in argv)
        assert "--env" not in argv
        assert "--keep_env" not in argv
