"""Sub-goal verdicts in the run-results CSV export."""

import csv
import io

import pytest

from simulate.views.run_results_v3 import _csv_rows


def _export(sub_goal_results, **overrides):
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
        **overrides,
    }
    columns = [
        {"id": "accuracy", "name": "Accuracy"},
        {"id": "unmeasured", "name": "Unmeasured"},
        *(
            {"id": item["id"], "name": item["id"]}
            for item in row["evaluations"]
            if item["id"] != "accuracy"
        ),
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


def test_csv_names_the_scenario_and_trial_the_table_shows():
    exported = _export([], source_scenario_key="cancel-within-24-hours", trial_index=2)[
        0
    ]

    assert (exported["scenario"], exported["trial"]) == ("cancel-within-24-hours", "2")


def test_csv_words_the_outcome_and_says_why_with_the_required_evals_that_failed():
    exported = _export(
        [],
        outcome="error",
        outcome_reason="Conversation ended early",
        required_evals_failed=["Accuracy", "Tone"],
    )[0]

    assert exported["outcome"] == "Error"
    assert exported["outcome_reason"] == "Conversation ended early"
    assert exported["required_evals_failed"] == "Accuracy, Tone"


def test_csv_says_not_scored_for_an_errored_call_instead_of_a_value():
    exported = _export(
        [],
        outcome="error",
        csat=2.0,
        csat_status="skipped",
        evaluations=[
            {"id": "accuracy", "value": "Passed", "passed": True, "status": "skipped"},
            {"id": "tone", "value": None, "score": None, "status": "pending"},
        ],
    )[0]

    assert exported["csat"] == "Not scored"
    assert exported["evaluation:Accuracy"] == "Not scored"
    assert exported["evaluation:tone"] == "Scoring"


@pytest.mark.parametrize(
    ("evaluation", "cell"),
    [
        ({"value": {"score": 1.0, "choice": "never"}, "score": 1.0}, "never (100%)"),
        ({"value": ["sometimes"], "score": None}, "sometimes"),
        ({"value": 0.85, "score": 0.85, "passed": True}, "85%"),
        ({"value": "Passed", "score": 1.0, "passed": True}, "Passed"),
        ({"value": "Failed", "score": 0.0, "passed": False}, "Failed"),
        ({"value": "Not applicable", "score": None, "passed": None}, "Not applicable"),
        ({"value": None, "score": None, "status": "timed_out"}, "Timed out"),
        ({"value": None, "score": None, "status": "failed"}, "Scoring failed"),
    ],
)
def test_csv_writes_each_eval_as_the_table_reads_it(evaluation, cell):
    exported = _export(
        [],
        evaluations=[{"id": "accuracy", "status": "succeeded", **evaluation}],
    )[0]

    assert exported["evaluation:Accuracy"] == cell
