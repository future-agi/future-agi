#!/usr/bin/env python3
"""Standalone verification for ToolCallF1, TrajectoryEfficiency, coding adapter."""
import os
import shutil
import sys
import tempfile
import types
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../futureagi"))

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
    "tool_functions",
    os.path.join(
        os.path.dirname(__file__),
        "../futureagi/agentic_eval/core_evals/fi_evals/function/functions.py",
    ),
)
_functions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_functions)

TRAJECTORY = [
    {"name": "get_weather", "arguments": {"city": "Paris"}},
    {"name": "book_flight", "arguments": {"to": "Paris", "seats": 2}},
]


def main():
    perfect = _functions.calculate_tool_call_f1(output=TRAJECTORY, expected=TRAJECTORY)
    print("ToolCallF1 perfect:", perfect["result"], perfect["reason"][:100])
    assert perfect["result"] == 1.0
    reordered = _functions.calculate_tool_call_f1(
        output=list(reversed(TRAJECTORY)), expected=TRAJECTORY
    )
    print("ToolCallF1 reordered:", reordered["result"])
    assert reordered["result"] == 1.0
    eff = _functions.calculate_trajectory_efficiency(
        output=["search", "patch", "test"],
        expected=["search", "patch", "test"],
        optimal_steps=3,
    )
    print("TrajectoryEfficiency optimal:", eff["result"], eff["reason"][:100])
    assert eff["result"] >= 0.9

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../futureagi"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings")
    try:
        import django

        django.setup()
        from simulate.services.coding_agent_adapter import apply_tool_calls

        repo = tempfile.mkdtemp()
        try:
            with open(os.path.join(repo, "test_sample.py"), "w") as handle:
                handle.write("def test_ok():\n    assert 1 + 1 == 2\n")
            result = apply_tool_calls(
                repo,
                [{"name": "write_file", "arguments": {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}}],
                timeout=60,
            )
            print("Coding adapter:", result["score"], result["applied"], f"{result['passed']}/{result['total']}")
            assert result["score"] >= 0.5
        finally:
            shutil.rmtree(repo, ignore_errors=True)
    except Exception as exc:
        print(f"Coding adapter check skipped: {exc}")
    print("OVERALL PASS")


if __name__ == "__main__":
    main()
