"""Success counts a finished try by its Pass/fail evaluations alone.

A completed try passes when every evaluation with a pass/fail rule passed,
fails when one of them failed, and is left out when it has none or when one
of them was not assessed. An evaluation with no pass/fail rule (a Choices or
a Scoring evaluation without a threshold) neither passes nor fails the try.
The matrix says how many of a run's evaluations have a rule, so a run with
none shows no Success figure. Pure projection tests: the selection is built
in memory, no database.
"""

from types import SimpleNamespace

import pytest

from model_hub.services import results_matrix
from model_hub.services.results_grid import result_cell, trial_record
from model_hub.services.results_selection import filter_rows

RUN = "22222222-2222-4222-8222-222222222222"
JUDGE = "33333333-3333-4333-8333-333333333333"
CHOICE = "44444444-4444-4444-8444-444444444444"
SCORE = "55555555-5555-4555-8555-555555555555"
VERSION = "66666666-6666-4666-8666-666666666666"

# A Pass/fail evaluation, a Choices evaluation and a Scoring evaluation, as
# experiment_run_creation pins them (no direction, no threshold).
PASS_FAIL = {"evaluator_version_id": VERSION, "output_type_normalized": "pass_fail"}
CHOICES = {
    "evaluator_version_id": VERSION,
    "output_type_normalized": "deterministic",
    "choice_scores": {"good": 1, "bad": 0},
    "pass_threshold": None,
}
SCORING = {
    "evaluator_version_id": VERSION,
    "output_type_normalized": "percentage",
    "pass_threshold": None,
}


def ref(metric, value, status="assessed"):
    return {
        "metric_id": metric,
        "evaluator_version_id": VERSION,
        "status": status,
        "value": value,
    }


def a_try(number, refs, bindings, execution="completed"):
    """One recorded try, read the way the grid reads a run item."""
    item = SimpleNamespace(
        metadata={},
        error=None if execution == "completed" else {"message": "boom"},
        status=execution,
        score_refs=refs,
        output="answer",
        cost=None,
        latency_ms=10,
        trace_id=None,
        id=f"item-{number}",
    )
    return trial_record(item, number, SimpleNamespace(status="completed"), bindings)


def selection_of(bindings, tries):
    row = {
        "example_id": "example-1",
        "row_label": "1",
        "values": {"question": "What is 2 + 2?"},
        "inputs": {"question": "What is 2 + 2?"},
        "expected": {"value": None},
        "results": {RUN: result_cell(tries)},
    }
    return SimpleNamespace(
        rows=[row],
        runs=[SimpleNamespace(id=RUN, status="completed")],
        trial_numbers={RUN: {"example-1": [t["trial_number"] for t in tries]}},
        bindings={RUN: bindings},
        baseline_run_id=None,
    )


def project(monkeypatch, bindings, tries, **query):
    monkeypatch.setattr(
        results_matrix, "response_parts", lambda selection, query: {"summary": {}}
    )
    data = results_matrix.matrix_results(
        selection_of(bindings, tries), {"page": 0, "page_size": 500, **query}
    )
    return data["rows"][0]["cells"][RUN], data["columns"][RUN]


def test_a_try_is_counted_by_its_pass_fail_evaluation_next_to_a_choices_one(
    monkeypatch,
):
    bindings = {JUDGE: PASS_FAIL, CHOICE: CHOICES}
    tries = [
        a_try(1, [ref(JUDGE, 1), ref(CHOICE, 1)], bindings),
        a_try(2, [ref(JUDGE, 0), ref(CHOICE, 1)], bindings),
        a_try(3, [ref(JUDGE, None, "not_assessed"), ref(CHOICE, 1)], bindings),
        a_try(4, [], bindings, execution="failed"),
    ]
    cell, column = project(monkeypatch, bindings, tries)
    # Pass; fail; its Pass/fail evaluation not assessed (left out); errored.
    assert cell["trials"] == [True, False, None, "error"]
    assert [t["status"] for t in cell["trace_summary"]] == [
        "completed",
        "completed",
        "completed",
        "error",
    ]
    # The Choices score still shows in the cell.
    assert cell["scores"][CHOICE]["value"] == 1
    assert column.get("pass_fail_evals") == 1


def test_a_run_with_no_pass_fail_evaluation_counts_no_try(monkeypatch):
    bindings = {CHOICE: CHOICES, SCORE: SCORING}
    tries = [
        a_try(1, [ref(CHOICE, 1), ref(SCORE, 0.9)], bindings),
        a_try(2, [ref(CHOICE, 0), ref(SCORE, 0.1)], bindings),
        a_try(3, [], bindings, execution="failed"),
    ]
    cell, column = project(monkeypatch, bindings, tries)
    assert cell["trials"] == [None, None, "error"]
    assert column.get("pass_fail_evals") == 0


def test_one_evaluations_matrix_carries_no_pass_fail_count(monkeypatch):
    bindings = {JUDGE: PASS_FAIL, CHOICE: CHOICES}
    tries = [a_try(1, [ref(JUDGE, 1), ref(CHOICE, 0)], bindings)]
    cell, column = project(monkeypatch, bindings, tries, measure=CHOICE)
    assert cell["trials"] == [0]
    assert "pass_fail_evals" not in column


def test_a_scoring_evaluation_with_a_threshold_and_a_direction_has_a_rule(
    monkeypatch,
):
    graded = {**SCORING, "pass_threshold": 0.5, "higher_is_better": True}
    bindings = {SCORE: graded, CHOICE: CHOICES}
    tries = [
        a_try(1, [ref(SCORE, 0.9), ref(CHOICE, 1)], bindings),
        a_try(2, [ref(SCORE, 0.2), ref(CHOICE, 1)], bindings),
    ]
    cell, column = project(monkeypatch, bindings, tries)
    assert cell["trials"] == [True, False]
    assert column.get("pass_fail_evals") == 1


def test_a_pass_fail_value_other_than_0_or_1_leaves_the_try_out(monkeypatch):
    bindings = {JUDGE: PASS_FAIL, CHOICE: CHOICES}
    tries = [a_try(1, [ref(JUDGE, 0.7), ref(CHOICE, 1)], bindings)]
    cell, _ = project(monkeypatch, bindings, tries)
    assert cell["trials"] == [None]


def test_the_flaky_filter_reads_the_same_rule():
    bindings = {JUDGE: PASS_FAIL, CHOICE: CHOICES}
    tries = [
        a_try(1, [ref(JUDGE, 1), ref(CHOICE, 1)], bindings),
        a_try(2, [ref(JUDGE, 0), ref(CHOICE, 1)], bindings),
    ]
    selection = selection_of(bindings, tries)
    shown = filter_rows(selection, {"show": "flaky"})
    assert [row["example_id"] for row in shown] == ["example-1"]
    pill = {"type": "flaky", "run": RUN}
    shown = filter_rows(selection, {"filters": [pill]})
    assert [row["example_id"] for row in shown] == ["example-1"]


@pytest.mark.parametrize(
    "pin,rule",
    [
        (PASS_FAIL, True),
        ({**PASS_FAIL, "output_type_normalized": "boolean"}, True),
        (CHOICES, False),
        (SCORING, False),
        ({**SCORING, "pass_threshold": 0.5}, False),
        ({**SCORING, "higher_is_better": True}, False),
        ({**SCORING, "pass_threshold": 0.5, "higher_is_better": True}, True),
        ({**SCORING, "pass_threshold": True, "higher_is_better": True}, False),
        (
            {
                **SCORING,
                "pass_threshold": 0.3,
                "config_snapshot": {"external": {"higher_is_better": False}},
            },
            True,
        ),
        (
            {**SCORING, "config_snapshot": {"external": {"output_type": "pass_fail"}}},
            False,
        ),
    ],
)
def test_the_rule_is_read_from_the_pin(pin, rule):
    from model_hub.services.results_root_causes import (
        pass_rule_keys,
        pinned_pass_rule,
    )

    assert pinned_pass_rule(pin) is rule
    assert pass_rule_keys({JUDGE: pin}) == ({JUDGE} if rule else set())
