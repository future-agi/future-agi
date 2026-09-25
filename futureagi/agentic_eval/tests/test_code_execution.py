"""Tests for code execution evaluators (no live LLM, no GPU)."""

import json

import pytest

from agentic_eval.core_evals.fi_evals.eval_type import FunctionEvalTypeId
from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_code_execution_pass,
    calculate_code_safety,
)
from agentic_eval.core_evals.fi_evals.function.wrapper import (
    CodeExecutionPass,
    CodeSafety,
)


ADD_OK = "def add(a, b):\n    return a + b\n"
ADD_BUGGY = "def add(a, b):\n    return a - b\n"
TESTS = [
    {"func": "add", "args": [2, 3], "expected": 5},
    {"func": "add", "args": [-1, 1], "expected": 0},
    {"func": "add", "args": [0, 0], "expected": 0},
]


class TestCodeExecutionPass:
    def test_correct_code_full_pass(self):
        res = calculate_code_execution_pass(output=ADD_OK, expected=TESTS)
        assert res["result"] == 1.0
        assert "3/3" in res["reason"]

    def test_buggy_code_partial(self):
        res = calculate_code_execution_pass(output=ADD_BUGGY, expected=TESTS)
        assert res["result"] < 1.0

    def test_syntax_error_zero(self):
        res = calculate_code_execution_pass(output="def broken(:", expected=TESTS)
        assert res["result"] == 0.0

    def test_empty_code(self):
        res = calculate_code_execution_pass(output="", expected=TESTS)
        assert res["result"] == 0.0

    def test_no_tests(self):
        res = calculate_code_execution_pass(output=ADD_OK, expected=[])
        assert res["result"] == 0.0

    def test_json_string_tests(self):
        res = calculate_code_execution_pass(output=ADD_OK, expected=json.dumps(TESTS))
        assert res["result"] == 1.0

    def test_assert_style(self):
        code = "def add(a, b):\n    return a + b\n"
        res = calculate_code_execution_pass(
            output=code, expected="assert add(2, 3) == 5\nassert add(0, 0) == 0"
        )
        assert res["result"] == 1.0

    def test_infinite_loop_times_out(self):
        code = "def add(a, b):\n    while True:\n        pass\n"
        res = calculate_code_execution_pass(output=code, expected=TESTS[:1], timeout=1)
        assert res["result"] == 0.0
        assert "timeout" in res["reason"].lower() or "0/1" in res["reason"]

    def test_wrapper(self):
        ev = CodeExecutionPass(timeout=5)
        assert ev.function_name == FunctionEvalTypeId.CODE_EXECUTION_PASS.value
        assert ev.function_arguments["timeout"] == 5

    def test_determinism(self):
        first = calculate_code_execution_pass(output=ADD_OK, expected=TESTS)
        second = calculate_code_execution_pass(output=ADD_OK, expected=TESTS)
        assert first["result"] == second["result"]


class TestCodeSafety:
    def test_clean_code(self):
        res = calculate_code_safety(output=ADD_OK)
        assert res["result"] == 1.0

    def test_flags_subprocess(self):
        res = calculate_code_safety(output="import subprocess\nsubprocess.run(['ls'])")
        assert res["result"] < 1.0
        assert "subprocess" in res["reason"]

    def test_flags_eval(self):
        res = calculate_code_safety(output="eval('1+1')")
        assert res["result"] < 1.0

    def test_wrapper(self):
        ev = CodeSafety()
        assert ev.function_name == FunctionEvalTypeId.CODE_SAFETY.value
