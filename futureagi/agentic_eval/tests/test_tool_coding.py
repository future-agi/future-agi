"""Tests for ToolCallF1, TrajectoryEfficiency, and coding adapter."""

import json
import os
import tempfile

import pytest

from agentic_eval.core_evals.fi_evals.eval_type import FunctionEvalTypeId
from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_tool_call_f1,
    calculate_trajectory_efficiency,
)
from agentic_eval.core_evals.fi_evals.function.wrapper import (
    ToolCallF1,
    TrajectoryEfficiency,
)
from simulate.services.coding_agent_adapter import apply_tool_calls


TRAJECTORY = [
    {"name": "get_weather", "arguments": {"city": "Paris"}},
    {"name": "book_flight", "arguments": {"to": "Paris", "seats": 2}},
]


class TestToolCallF1:
    def test_perfect_match(self):
        res = calculate_tool_call_f1(output=TRAJECTORY, expected=TRAJECTORY)
        assert res["result"] == 1.0

    def test_reordered_partial_credit(self):
        res = calculate_tool_call_f1(output=list(reversed(TRAJECTORY)), expected=TRAJECTORY)
        assert res["result"] == 1.0

    def test_wrong_args_partial(self):
        actual = [
            {"name": "get_weather", "arguments": {"city": "London"}},
            {"name": "book_flight", "arguments": {"to": "Paris", "seats": 2}},
        ]
        res = calculate_tool_call_f1(output=actual, expected=TRAJECTORY)
        assert 0.0 < res["result"] < 1.0

    def test_schema_violation_penalty(self):
        schemas = {
            "book_flight": {
                "type": "object",
                "required": ["to", "seats"],
                "properties": {"to": {"type": "string"}, "seats": {"type": "integer"}},
            }
        }
        actual = [{"name": "book_flight", "arguments": {"to": "Paris", "seats": "two"}}]
        wanted = [{"name": "book_flight", "arguments": {"to": "Paris", "seats": 2}}]
        clean = calculate_tool_call_f1(output=wanted, expected=wanted, schemas=schemas)
        bad = calculate_tool_call_f1(output=actual, expected=wanted, schemas=schemas)
        assert bad["result"] < clean["result"]
        assert "schema" in bad["reason"].lower()

    def test_empty_both(self):
        assert calculate_tool_call_f1(output=[], expected=[])["result"] == 1.0

    def test_wrapper(self):
        ev = ToolCallF1(schemas={})
        assert ev.function_name == FunctionEvalTypeId.TOOL_CALL_F1.value


class TestTrajectoryEfficiency:
    def test_optimal_scores_high(self):
        actions = ["search", "read", "patch", "test"]
        res = calculate_trajectory_efficiency(
            output=actions, expected=actions, optimal_steps=4
        )
        assert res["result"] >= 0.9

    def test_bloated_scores_lower(self):
        wanted = ["search", "patch", "test"]
        actual = ["search", "search", "read", "read", "patch", "test", "test"]
        efficient = calculate_trajectory_efficiency(
            output=wanted, expected=wanted, optimal_steps=3
        )
        bloated = calculate_trajectory_efficiency(
            output=actual, expected=wanted, optimal_steps=3
        )
        assert bloated["result"] < efficient["result"]

    def test_state_diff_bonus(self):
        output = {
            "actions": ["patch", "test"],
            "before": {"tests_passing": 1},
            "after": {"tests_passing": 5},
        }
        expected = {"actions": ["patch", "test"], "state": {"tests_passing": 5}}
        res = calculate_trajectory_efficiency(output=output, expected=expected)
        assert res["result"] >= 0.8

    def test_wrapper(self):
        ev = TrajectoryEfficiency(optimal_steps=3)
        assert ev.function_name == FunctionEvalTypeId.TRAJECTORY_EFFICIENCY.value


class TestCodingAdapter:
    def test_write_and_run_tests(self):
        repo = tempfile.mkdtemp()
        try:
            with open(os.path.join(repo, "test_sample.py"), "w") as handle:
                handle.write("def test_ok():\n    assert 1 + 1 == 2\n")
            result = apply_tool_calls(
                repo,
                [
                    {
                        "name": "write_file",
                        "arguments": {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"},
                    }
                ],
                timeout=60,
            )
            assert "calc.py" in result["applied"]
            assert result["total"] >= 1
            assert result["score"] >= 0.5
        finally:
            import shutil

            shutil.rmtree(repo, ignore_errors=True)

    def test_rejects_path_traversal(self):
        repo = tempfile.mkdtemp()
        try:
            result = apply_tool_calls(
                repo,
                [{"name": "write_file", "arguments": {"path": "../evil.py", "content": "x"}}],
                timeout=30,
            )
            assert result["applied"] == []
        finally:
            import shutil

            shutil.rmtree(repo, ignore_errors=True)
