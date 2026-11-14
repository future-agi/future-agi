"""
Tests for the sandboxed code execution engine.

Verifies that:
  - Valid eval functions execute correctly
  - Dangerous operations are blocked (imports, file I/O, exec, network)
  - Timeouts work
  - Return format handling (bool, float, dict)
"""

import pytest

from agentic_eval.core_evals.fi_utils.restricted_code_execution import (
    execute_restricted_python,
)


class TestHappyPath:
    """Tests for valid eval code that should execute successfully."""

    def test_evaluate_function_bool_return(self):
        code = """
def evaluate(output, expected, **kwargs):
    return output == expected
"""
        result = execute_restricted_python(
            code, {"output": "hello", "expected": "hello"}
        )
        assert result["status"] == "success"
        assert result["data"] is True

    def test_evaluate_function_bool_false(self):
        code = """
def evaluate(output, expected, **kwargs):
    return output == expected
"""
        result = execute_restricted_python(
            code, {"output": "hello", "expected": "world"}
        )
        assert result["status"] == "success"
        assert result["data"] is False

    def test_main_function_backward_compat(self):
        code = """
def main(**kwargs):
    return kwargs.get("output") == kwargs.get("expected")
"""
        result = execute_restricted_python(code, {"output": "test", "expected": "test"})
        assert result["status"] == "success"
        assert result["data"] is True

    def test_float_score_return(self):
        code = """
def evaluate(output, expected, **kwargs):
    return 0.75
"""
        result = execute_restricted_python(code, {"output": "x", "expected": "y"})
        assert result["status"] == "success"
        assert result["data"] == 0.75

    def test_dict_return_with_reason(self):
        code = """
def evaluate(output, expected, **kwargs):
    score = 1.0 if output == expected else 0.0
    return {"result": score, "reason": "Exact match check"}
"""
        result = execute_restricted_python(code, {"output": "a", "expected": "a"})
        assert result["status"] == "success"
        assert result["data"]["result"] == 1.0
        assert result["data"]["reason"] == "Exact match check"

    def test_evaluate_takes_priority_over_main(self):
        code = """
def main(**kwargs):
    return False

def evaluate(**kwargs):
    return True
"""
        result = execute_restricted_python(code, {})
        assert result["status"] == "success"
        assert result["data"] is True


class TestAllowedModules:
    """Tests that whitelisted modules work correctly."""

    def test_json_module(self):
        code = """
import json
def evaluate(output, **kwargs):
    data = json.loads(output)
    return isinstance(data, dict)
"""
        result = execute_restricted_python(code, {"output": '{"key": "value"}'})
        assert result["status"] == "success"
        assert result["data"] is True

    def test_re_module(self):
        code = """
import re
def evaluate(output, **kwargs):
    return bool(re.match(r'^[a-z]+$', output))
"""
        result = execute_restricted_python(code, {"output": "hello"})
        assert result["status"] == "success"
        assert result["data"] is True

    def test_math_module(self):
        code = """
import math
def evaluate(output, expected, **kwargs):
    return math.isclose(float(output), float(expected), rel_tol=0.01)
"""
        result = execute_restricted_python(code, {"output": "3.14", "expected": "3.14"})
        assert result["status"] == "success"
        assert result["data"] is True

    def test_collections_module(self):
        code = """
import collections
def evaluate(output, expected, **kwargs):
    c1 = collections.Counter(output.split())
    c2 = collections.Counter(expected.split())
    return c1 == c2
"""
        result = execute_restricted_python(
            code, {"output": "a b c", "expected": "c b a"}
        )
        assert result["status"] == "success"
        assert result["data"] is True

    def test_difflib_module(self):
        code = """
import difflib
def evaluate(output, expected, **kwargs):
    ratio = difflib.SequenceMatcher(None, output, expected).ratio()
    return ratio
"""
        result = execute_restricted_python(
            code, {"output": "hello", "expected": "hallo"}
        )
        assert result["status"] == "success"
        assert isinstance(result["data"], float)
        assert result["data"] > 0.5

    def test_preimported_modules(self):
        """Modules should be available without explicit import."""
        code = """
def evaluate(output, **kwargs):
    return len(json.dumps({"test": output})) > 0
"""
        result = execute_restricted_python(code, {"output": "hello"})
        assert result["status"] == "success"
        assert result["data"] is True


class TestBlockedOperations:
    """Tests that dangerous operations are properly blocked."""

    def test_import_os_blocked(self):
        code = """
import os
def evaluate(**kwargs):
    return os.environ.get("SECRET")
"""
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"
        assert (
            "not allowed" in result["data"].lower()
            or "import" in result["data"].lower()
        )

    def test_import_subprocess_blocked(self):
        code = """
import subprocess
def evaluate(**kwargs):
    return subprocess.run(["ls"], capture_output=True).stdout
"""
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"

    def test_import_socket_blocked(self):
        code = """
import socket
def evaluate(**kwargs):
    s = socket.socket()
    s.connect(("evil.com", 80))
"""
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"

    def test_import_sys_blocked(self):
        code = """
import sys
def evaluate(**kwargs):
    return sys.path
"""
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"

    def test_open_file_blocked(self):
        code = """
def evaluate(**kwargs):
    with open("/etc/passwd") as f:
        return f.read()
"""
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"

    def test_exec_blocked(self):
        code = """
def evaluate(**kwargs):
    exec("x = 1")
    return True
"""
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"

    def test_eval_blocked(self):
        code = """
def evaluate(**kwargs):
    return eval("1 + 1")
"""
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"

    def test_dunder_access_blocked(self):
        """Accessing __class__, __mro__, etc. should be blocked."""
        code = """
def evaluate(**kwargs):
    return "".__class__.__mro__
"""
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"

    def test_globals_blocked(self):
        code = """
def evaluate(**kwargs):
    return globals()
"""
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"

    def test_import_http_blocked(self):
        code = """
import http.client
def evaluate(**kwargs):
    conn = http.client.HTTPConnection("evil.com")
    conn.request("GET", "/")
"""
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"


class TestErrorHandling:
    """Tests for error cases."""

    def test_no_function_defined(self):
        code = "x = 1 + 1"
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"
        assert "evaluate" in result["data"].lower() or "main" in result["data"].lower()

    def test_syntax_error(self):
        code = "def evaluate(**kwargs"
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"

    def test_runtime_error(self):
        code = """
def evaluate(**kwargs):
    return 1 / 0
"""
        result = execute_restricted_python(code, {})
        assert result["status"] == "error"
        assert "ZeroDivision" in result["data"] or "division" in result["data"].lower()

    def test_empty_code(self):
        result = execute_restricted_python("", {})
        assert result["status"] == "error"

    def test_none_input_handled(self):
        code = """
def evaluate(output=None, expected=None, **kwargs):
    if output is None:
        return False
    return True
"""
        result = execute_restricted_python(code, {"output": None})
        assert result["status"] == "success"
        assert result["data"] is False


class TestTimeout:
    """Tests for timeout enforcement."""

    def test_infinite_loop_times_out(self):
        code = """
def evaluate(**kwargs):
    while True:
        pass
"""
        result = execute_restricted_python(code, {}, timeout_seconds=3)
        assert result["status"] == "error"
        assert "timed out" in result["data"].lower()

    def test_recursive_computation_times_out(self):
        code = """
def evaluate(**kwargs):
    x = 0
    while x < 10**15:
        x += 1
    return x
"""
        result = execute_restricted_python(code, {}, timeout_seconds=3)
        assert result["status"] == "error"
        assert "timed out" in result["data"].lower()


class TestPrintCapture:
    """Tests that print() is captured, not leaked to stdout."""

    def test_print_captured(self):
        code = """
def evaluate(output, **kwargs):
    print("Debug:", output)
    return True
"""
        result = execute_restricted_python(code, {"output": "test"})
        assert result["status"] == "success"
        assert result["data"] is True
        # print output is captured, not leaked
        assert result.get("print_output") is None or "Debug: test" in result.get(
            "print_output", ""
        )
