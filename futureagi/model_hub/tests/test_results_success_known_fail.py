"""A known fail decides a try, so Success and the Fails filter agree.

A completed try with more than one Pass/fail evaluation fails when any of
them failed, passes when every one gave a result and all passed, and is not
counted when none failed and at least one gave no result. The Fails filter
lists a row by the evaluation that failed, so it and Success agree try by
try. Each try also names the evaluations that passed, so a screen can tell a
try missing a Pass/fail result from one with none at all. Pure projection
tests: the selection is built in memory, no database.
"""

from types import SimpleNamespace

import pytest

from model_hub.services import results_matrix
from model_hub.services.results_grid import result_cell, trial_record
from model_hub.services.results_selection import filter_rows

RUN = "22222222-2222-4222-8222-222222222222"
JUDGE = "33333333-3333-4333-8333-333333333333"
CHECK = "44444444-4444-4444-8444-444444444444"
SCORE = "55555555-5555-4555-8555-555555555555"
VERSION = "66666666-6666-4666-8666-666666666666"

PASS_FAIL = {"evaluator_version_id": VERSION, "output_type_normalized": "pass_fail"}
# A Scoring evaluation with a pass threshold and a direction has a rule.
GRADED_SCORE = {
    "evaluator_version_id": VERSION,
    "output_type_normalized": "percentage",
    "pass_threshold": 0.5,
    "higher_is_better": True,
}


def ref(metric, value, status="assessed"):
    return {
        "metric_id": metric,
        "evaluator_version_id": VERSION,
        "status": status,
        "value": value,
    }


def no_result(metric):
    return ref(metric, None, "not_assessed")


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


def selection_of(bindings, rows_of_tries):
    """One run; row n holds the tries of rows_of_tries[n - 1]."""
    rows = [
        {
            "example_id": f"example-{n}",
            "row_label": str(n),
            "values": {"question": f"Question {n}"},
            "inputs": {"question": f"Question {n}"},
            "expected": {"value": None},
            "results": {RUN: result_cell(tries)},
        }
        for n, tries in enumerate(rows_of_tries, start=1)
    ]
    return SimpleNamespace(
        rows=rows,
        runs=[SimpleNamespace(id=RUN, status="completed")],
        trial_numbers={
            RUN: {
                row["example_id"]: [t["trial_number"] for t in tries]
                for row, tries in zip(rows, rows_of_tries)
            }
        },
        bindings={RUN: bindings},
        baseline_run_id=None,
    )


def matrix_of(monkeypatch, selection, **query):
    monkeypatch.setattr(
        results_matrix, "response_parts", lambda selection, query: {"summary": {}}
    )
    return results_matrix.matrix_results(
        selection, {"page": 0, "page_size": 500, **query}
    )


def test_a_known_fail_decides_the_try(monkeypatch):
    bindings = {JUDGE: PASS_FAIL, CHECK: PASS_FAIL}
    tries = [
        # One failed, the other gave no result.
        a_try(1, [ref(JUDGE, 0), no_result(CHECK)], bindings),
        # One failed, the other's value is no Pass/fail answer.
        a_try(2, [ref(JUDGE, 0), ref(CHECK, 0.7)], bindings),
        # None failed, one gave no result.
        a_try(3, [ref(JUDGE, 1), no_result(CHECK)], bindings),
        # Every one gave a result and all passed.
        a_try(4, [ref(JUDGE, 1), ref(CHECK, 1)], bindings),
        # Both gave a result, one failed.
        a_try(5, [ref(JUDGE, 0), ref(CHECK, 1)], bindings),
        # The try errored.
        a_try(6, [], bindings, execution="failed"),
    ]
    data = matrix_of(monkeypatch, selection_of(bindings, [tries]))
    cell = data["rows"][0]["cells"][RUN]
    assert cell["trials"] == [False, False, None, True, False, "error"]
    # The two known fails finished and name their failed evaluation, so a
    # screen reading the matrix lists them as fails, never as tries with no
    # Pass/fail result.
    statuses = [t["status"] for t in cell["trace_summary"][:2]]
    assert statuses == ["completed", "completed"]
    assert cell["failed_evals"][:2] == [[JUDGE], [JUDGE]]
    assert data["columns"][RUN]["pass_fail_evals"] == 2


@pytest.mark.parametrize(
    "with_pass_fail",
    [
        # Green at the base: with one graded evaluation nothing can be mixed.
        pytest.param(False, id="scoring-alone"),
        pytest.param(True, id="scoring-and-a-pass-fail-evaluation"),
    ],
)
def test_success_and_the_fails_filter_agree_on_a_scoring_evaluation(
    monkeypatch, with_pass_fail
):
    bindings = {SCORE: GRADED_SCORE, **({JUDGE: PASS_FAIL} if with_pass_fail else {})}

    def refs(score, judge):
        return [ref(SCORE, score), *([judge] if with_pass_fail else [])]

    rows = [
        [a_try(1, refs(0.9, ref(JUDGE, 1)), bindings)],
        [a_try(1, refs(0.2, ref(JUDGE, 1)), bindings)],
        [a_try(1, refs(0.2, no_result(JUDGE)), bindings)],
        [a_try(1, refs(0.9, no_result(JUDGE)), bindings)],
    ]
    selection = selection_of(bindings, rows)
    data = matrix_of(monkeypatch, selection)
    failing = [
        row["row_id"]
        for row in data["rows"]
        if any(t is False for t in row["cells"][RUN]["trials"])
    ]
    pill = {"type": "fails", "metric": SCORE, "run": RUN}
    listed = [row["example_id"] for row in filter_rows(selection, {"filters": [pill]})]
    assert listed == ["example-2", "example-3"]
    assert failing == listed


def test_a_cell_with_a_known_fail_and_a_pass_is_flaky():
    bindings = {JUDGE: PASS_FAIL, CHECK: PASS_FAIL}
    tries = [
        a_try(1, [ref(JUDGE, 0), no_result(CHECK)], bindings),
        a_try(2, [ref(JUDGE, 1), ref(CHECK, 1)], bindings),
    ]
    selection = selection_of(bindings, [tries])
    shown = filter_rows(selection, {"show": "flaky"})
    assert [row["example_id"] for row in shown] == ["example-1"]
    pill = {"type": "flaky", "run": RUN}
    shown = filter_rows(selection, {"filters": [pill]})
    assert [row["example_id"] for row in shown] == ["example-1"]


def test_each_try_names_the_evaluations_that_passed(monkeypatch):
    bindings = {JUDGE: PASS_FAIL, CHECK: PASS_FAIL}
    tries = [
        # One passed, the other gave no result: missing a Pass/fail result.
        a_try(1, [ref(JUDGE, 1), no_result(CHECK)], bindings),
        # Neither gave a result: no Pass/fail result at all.
        a_try(2, [no_result(JUDGE), no_result(CHECK)], bindings),
        # One failed, the other passed.
        a_try(3, [ref(JUDGE, 0), ref(CHECK, 1)], bindings),
        # The try errored.
        a_try(4, [], bindings, execution="failed"),
    ]
    data = matrix_of(monkeypatch, selection_of(bindings, [tries]))
    cell = data["rows"][0]["cells"][RUN]
    assert cell["trials"] == [None, None, False, "error"]
    assert cell["passed_evals"] == [[JUDGE], [], [CHECK], []]
    assert cell["failed_evals"] == [[], [], [JUDGE], []]


def test_a_read_of_one_evaluation_names_no_passed_evaluations(monkeypatch):
    # Green at the base: the key is only on pass@1 reads.
    bindings = {JUDGE: PASS_FAIL, CHECK: PASS_FAIL}
    tries = [a_try(1, [ref(JUDGE, 1), ref(CHECK, 1)], bindings)]
    data = matrix_of(monkeypatch, selection_of(bindings, [tries]), measure=JUDGE)
    cell = data["rows"][0]["cells"][RUN]
    assert cell["trials"] == [1]
    assert "passed_evals" not in cell
