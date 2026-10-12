#!/usr/bin/env python3
"""Standalone verification for code execution evaluators."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../futureagi"))

import types
import unittest.mock as mock

for _mod in [
    "structlog",
    "rest_framework",
    "rest_framework.response",
    "tfc.telemetry",
    "tfc.ee_stub",
    "agentic_eval.core_evals.fi_utils.json",
    "agentic_eval.core_evals.fi_utils.logging",
    "agentic_eval.core_evals.fi_utils.utils",
    "agentic_eval.core_evals.keys.openai_api",
    "agentic_eval.core_evals.llm_services.openai_api",
    "agentic_eval.core_evals.fi_utils.fi_code_execution",
    "agentic_eval.core_evals.fi_utils.exceptions",
    "agentic_eval.core_evals.fi_evals.grounded.similarity",
]:
    if _mod not in sys.modules:
        _m = types.ModuleType(_mod)
        _m.__spec__ = None
        sys.modules[_mod] = _m

sys.modules["agentic_eval.core_evals.fi_utils.json"].extract_json_path = lambda *a, **kw: None
sys.modules["agentic_eval.core_evals.fi_utils.json"].validate_json = lambda *a, **kw: True
sys.modules["agentic_eval.core_evals.fi_utils.logging"].logger = mock.MagicMock()
sys.modules["agentic_eval.core_evals.fi_utils.utils"].PreserveUndefined = object
sys.modules["agentic_eval.core_evals.keys.openai_api"].OpenAiApiKey = object
sys.modules["agentic_eval.core_evals.llm_services.openai_api"].OpenAiService = object
sys.modules["agentic_eval.core_evals.fi_utils.fi_code_execution"].CodeExecution = object
sys.modules["agentic_eval.core_evals.fi_utils.exceptions"].NoOpenAiApiKeyException = Exception
sys.modules["agentic_eval.core_evals.fi_evals.grounded.similarity"].CosineSimilarity = mock.MagicMock
sys.modules["tfc.telemetry"].wrap_for_thread = lambda x: x
sys.modules["tfc.ee_stub"]._ee_stub = lambda x: object
sys.modules["structlog"].get_logger = lambda *a, **kw: mock.MagicMock()

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "code_functions",
    os.path.join(
        os.path.dirname(__file__),
        "../futureagi/agentic_eval/core_evals/fi_evals/function/functions.py",
    ),
)
_functions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_functions)
calculate_code_execution_pass = _functions.calculate_code_execution_pass
calculate_code_safety = _functions.calculate_code_safety

ADD_OK = "def add(a, b):\n    return a + b\n"
ADD_BUGGY = "def add(a, b):\n    return a - b\n"
TESTS = [
    {"func": "add", "args": [2, 3], "expected": 5},
    {"func": "add", "args": [-1, 1], "expected": 0},
    {"func": "add", "args": [0, 0], "expected": 0},
]


def main():
    start = time.time()
    good = calculate_code_execution_pass(output=ADD_OK, expected=TESTS)
    print("correct code:", good["result"], good["reason"][:120])
    assert good["result"] == 1.0
    bad = calculate_code_execution_pass(output=ADD_BUGGY, expected=TESTS)
    print("buggy code:", bad["result"], bad["reason"][:120])
    assert bad["result"] < 1.0
    static_bleu_tie = "CodeBLEU is static and cannot separate these two"
    print("static note:", static_bleu_tie)
    safe = calculate_code_safety(output=ADD_OK)
    risky = calculate_code_safety(output="import subprocess\nsubprocess.run(['ls'])")
    print("safety clean:", safe["result"], "risky:", risky["result"])
    assert safe["result"] == 1.0 and risky["result"] < 1.0
    loop = calculate_code_execution_pass(
        output="def add(a, b):\n    while True:\n        pass\n",
        expected=TESTS[:1],
        timeout=1,
    )
    print("infinite loop:", loop["result"], loop["reason"][:100])
    assert loop["result"] == 0.0
    elapsed = time.time() - start
    print(f"Total time: {elapsed:.2f}s")
    print("OVERALL PASS")


if __name__ == "__main__":
    main()
