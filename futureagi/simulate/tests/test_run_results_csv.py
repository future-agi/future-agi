"""Sub-goal verdicts in the run-results CSV export."""

import csv
import io

import pytest

from simulate.views.run_results_v3 import _csv_rows


def _export(sub_goal_results):
    row = {
        "id": "call-1",
        "scenario": "Invoice",
        "scenario_details": "Explain the invoice",
        "goal": "Resolve the question",
        "ideal_outcome": "Customer understands the invoice",
        "conversation_branch": "Billing",
        "sub_goal_results": sub_goal_results,
        "persona": "Customer",
        "outcome": "failed",
        "execution_status": "completed",
        "provider": "phone",
        "modality": "voice",
        "duration_seconds": 30,
        "latency_ms": 250,
        "turn_count": 4,
        "tokens": 100,
        "cost_cents": 1,
        "csat": None,
        "ended_reason": "completed",
        "error_message": "",
        "evaluations": [{"id": "accuracy", "value": "Failed"}],
    }
    columns = [
        {"id": "accuracy", "name": "Accuracy"},
        {"id": "unmeasured", "name": "Unmeasured"},
    ]
    return list(csv.DictReader(io.StringIO("".join(_csv_rows([row], columns)))))


def test_csv_includes_each_sub_goal_verdict_and_retains_evaluations():
    exported = _export(
        [
            {"name": "Verify identity", "passed": True},
            {"name": "Explain charges", "passed": False},
            {"name": "Send receipt", "passed": None},
        ]
    )

    assert len(exported) == 1
    assert exported[0]["sub_goals"] == (
        "Verify identity (Passed), Explain charges (Failed), "
        "Send receipt (Inconclusive)"
    )
    assert exported[0]["evaluation:Accuracy"] == "Failed"
    assert exported[0]["evaluation:Unmeasured"] == ""
    assert "sub_goal_results" not in exported[0]


def test_csv_exports_empty_sub_goals_as_empty_cell():
    assert _export([])[0]["sub_goals"] == ""


def test_csv_preserves_quoted_multiline_sub_goal_names():
    name = 'Explain "taxes", fees\nand surcharges'
    exported = _export([{"name": name, "passed": True}])

    assert len(exported) == 1
    assert exported[0]["sub_goals"] == f"{name} (Passed)"
    assert exported[0]["persona"] == "Customer"


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_escapes_formula_prefixes_in_sub_goal_names(prefix):
    exported = _export([{"name": f"{prefix}SUM(1,1)", "passed": False}])

    assert exported[0]["sub_goals"] == f"'{prefix}SUM(1,1) (Failed)"
