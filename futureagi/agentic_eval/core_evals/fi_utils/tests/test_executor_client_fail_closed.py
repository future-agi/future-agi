"""Fail-closed behavior of the code-executor client (sandbox.py).

Nikhil's #1: a 401/unreachable executor used to be swallowed, silently dropping
untrusted code onto the weaker in-process sandbox on the eval-worker host. These
tests drive the real `execute_sandboxed_python/javascript` decision path and assert
it now fails closed when the executor is the mandated path (a key is configured).
Each goes RED if the swallow-to-None / fall-through behavior is restored.
"""

import io
import json
import shutil
import subprocess
import tempfile
import urllib.error
from unittest import mock

import pytest

from agentic_eval.core_evals.fi_utils import sandbox

REFUSAL = "refusing to run untrusted code"


def _http_error(code):
    return urllib.error.HTTPError("http://x/execute", code, "err", {}, io.BytesIO(b""))


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.setattr(sandbox, "CODE_EXECUTOR_API_KEY", "")


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(sandbox, "CODE_EXECUTOR_API_KEY", "secret")


class TestDistinguishRanFromUnavailable:
    def test_success_result_returned(self, with_key):
        with mock.patch.object(sandbox, "_call_executor_service",
                               return_value={"status": "success", "data": {"result": 1.0}}):
            r = sandbox.execute_sandboxed_python("def evaluate(**k): return True", {})
        assert r["status"] == "success"

    def test_executor_ran_and_code_errored_is_passed_through(self, with_key):
        # Executor reachable (HTTP 200) but the user code errored — that is a real
        # eval result, NOT an "unavailable" event, so it must be returned, not
        # fail-closed. Distinguishing these two is the core of the fix.
        ran_and_errored = {"status": "error", "data": "Runtime error: boom"}
        with mock.patch.object(sandbox, "_call_executor_service", return_value=ran_and_errored):
            r = sandbox.execute_sandboxed_python("def evaluate(**k): raise ValueError()", {})
        assert r == ran_and_errored
        assert REFUSAL not in r["data"]


class TestFailsClosedWhenExecutorMandated:
    def test_auth_401_fails_closed(self, with_key):
        with mock.patch.object(sandbox.urllib.request, "urlopen", side_effect=_http_error(401)):
            r = sandbox.execute_sandboxed_python("def evaluate(**k): return True", {})
        assert r["status"] == "error"
        assert REFUSAL in r["data"]

    def test_unreachable_fails_closed(self, with_key):
        with mock.patch.object(sandbox.urllib.request, "urlopen", side_effect=ConnectionError("refused")):
            r = sandbox.execute_sandboxed_python("def evaluate(**k): return True", {})
        assert r["status"] == "error"
        assert REFUSAL in r["data"]

    def test_js_unreachable_fails_closed(self, with_key):
        with mock.patch.object(sandbox.urllib.request, "urlopen", side_effect=_http_error(401)):
            r = sandbox.execute_sandboxed_javascript("function evaluate(){return true;}", {})
        assert r["status"] == "error"
        assert REFUSAL in r["data"]


class TestLocalFallbackPreservedForDev:
    def test_no_key_skips_executor_and_uses_local(self, no_key):
        # No executor key configured = local dev / OSS without the service. The executor
        # is not even contacted (so a transient 401/outage can't fail evals); the local
        # in-process sandbox runs instead. Behavior preservation, not fix-proving.
        call = mock.Mock(side_effect=AssertionError("executor must not be called without a key"))
        with mock.patch.object(sandbox, "_call_executor_service", call):
            r = sandbox.execute_sandboxed_python("def evaluate(output, **k): return output == 'hi'",
                                                 {"output": "hi"})
        call.assert_not_called()
        assert REFUSAL not in r.get("data", "")


class TestJsFallbackNetworkStripped:
    """The in-process JS fallback (no nsjail netns) must not leave Node's network
    globals reachable. Runs the generated sandbox script through real node."""

    def test_fetch_and_require_are_undefined(self):
        node = shutil.which("node")
        if not node:
            pytest.skip("node not available")
        user_code = (
            "function evaluate(){ return (typeof fetch==='undefined' "
            "&& typeof require==='undefined' && typeof XMLHttpRequest==='undefined'); }"
        )
        script = sandbox._build_js_sandbox_single_file(user_code, {})
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=20)
        result = json.loads(out.stdout.strip())
        assert result["status"] == "success"
        assert result["data"] is True  # fetch/require/XHR all stripped
