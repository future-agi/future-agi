"""One declared direction rule across every Results projection.

Agent run walk, head-86: Success (pass@1) read "-" for a run whose judge is a
choice eval (choice_scores Pass: 1, Fail: 0, pass threshold 0.5) with no
declared direction. A direction is never inferred from a choice map: only a
declared direction, or a declared pass/fail output whose stored 1 is the
pass, gives a verdict. Otherwise the result stays unknown and Success stays
"-". The same evaluation is fed to the Success source (grid score -> matrix
try), the grid/analysis rule and the catalog/Regressed-first definition, and
they must agree. Every verdict is computed from ONE try's value (a run
item's score_refs), never from an aggregate, so a pass/fail output whose only
direction is its declared type gives a verdict from its stored 0/1 alone.
Pure functions; no database.
"""

from types import SimpleNamespace

import pytest

from model_hub.services.results_catalog import metric_shape
from model_hub.services.results_grid import assessed_scores, result_cell, trial_record
from model_hub.services.results_matrix import trial_value
from model_hub.services.results_root_causes import (
    observed_failure_group,
    pinned_metric_direction,
)
from model_hub.services.results_selection import regressed_first

CHOICE = {
    "evaluator_version_id": "version",
    "output_type_normalized": "choices",
    "pass_threshold": 0.5,
    "choice_scores": {"Pass": 1, "Fail": 0},
}
NUMERIC = {
    "evaluator_version_id": "version",
    "output_type_normalized": "numeric",
    "pass_threshold": 0.5,
}

# (binding, values, verdicts, direction)
CASES = [
    pytest.param(dict(CHOICE), [1, 0], [None, None], None, id="choice-walk-native-pin"),
    pytest.param(
        {**CHOICE, "config_snapshot": {"reverse_output": False}},
        [1, 0],
        [None, None],
        None,
        id="choice-normal",
    ),
    pytest.param(
        {**CHOICE, "config_snapshot": {"reverse_output": True}},
        [1, 0],
        [None, None],
        None,
        id="choice-reversed",
    ),
    pytest.param(
        {**CHOICE, "reverse_output": True},
        [1, 0],
        [None, None],
        None,
        id="choice-reversed-pin-field",
    ),
    pytest.param(
        {**CHOICE, "higher_is_better": True},
        [1, 0, 0.5],
        [True, False, True],
        True,
        id="choice-declared-direction",
    ),
    pytest.param(
        dict(NUMERIC), [0.9, 0.1], [None, None], None, id="numeric-undeclared"
    ),
    pytest.param(
        {**NUMERIC, "config_snapshot": {"external": {"higher_is_better": False}}},
        [0.1, 0.9, 0.5],
        [True, False, True],
        False,
        id="numeric-declared-lower",
    ),
    pytest.param(
        {**NUMERIC, "higher_is_better": True, "config_snapshot": {}},
        [0.1, 0.9, 0.5],
        [False, True, True],
        True,
        id="numeric-declared-higher-legacy",
    ),
    pytest.param(
        {
            "evaluator_version_id": "version",
            "output_type_normalized": "pass_fail",
            "pass_threshold": None,
        },
        [1, 0],
        [True, False],
        True,
        id="pass-fail-output",
    ),
    pytest.param(
        {
            "evaluator_version_id": "version",
            "output_type_normalized": "pass_fail",
            "pass_threshold": 0.5,
        },
        [0.7, 0.2],
        [None, None],
        True,
        id="pass-fail-output-one-try-not-0-1",
    ),
]


def _score(binding, value=None, status="assessed", refs=None):
    if refs is None:
        refs = [
            {
                "metric_id": "metric",
                "evaluator_version_id": "version",
                "status": status,
                "value": value,
            }
        ]
    return assessed_scores(refs, {"metric": binding})["metric"]


def _try(score):
    return trial_value({"status": "completed", "scores": {"metric": score}})


def _record(value, status="assessed"):
    return SimpleNamespace(
        example_id="example",
        status="completed",
        scores={"metric": {"value": value, "status": status}},
    )


def _ranked(binding):
    def cell(value):
        return {
            "scores": {"metric": {"value": value}},
            "cost_usd": None,
            "latency_ms": None,
        }

    rows = [
        {"example_id": "flat", "results": {"base": cell(1), "cand": cell(1)}},
        {"example_id": "up", "results": {"base": cell(0), "cand": cell(1)}},
        {"example_id": "down", "results": {"base": cell(1), "cand": cell(0)}},
    ]
    selection = SimpleNamespace(
        runs=[SimpleNamespace(id="base"), SimpleNamespace(id="cand")],
        baseline_run_id="base",
        plans={"base": [("flat", 1), ("up", 1), ("down", 1)]},
        bindings={"base": {"metric": binding}},
        items={},
        dataset=None,
    )
    return [row["example_id"] for row in regressed_first(rows, selection)]


EXPECTED_ORDER = {
    None: ["flat", "up", "down"],  # no direction: no movement counted
    True: ["down", "up", "flat"],
    False: ["up", "down", "flat"],
}


@pytest.mark.parametrize("binding,values,verdicts,direction", CASES)
def test_success_grid_and_catalog_read_one_rule(binding, values, verdicts, direction):
    # 1. The Experiments tab's Success source: grid score -> matrix try.
    scores = [_score(binding, value) for value in values]
    assert [score["passed"] for score in scores] == verdicts
    assert [_try(score) for score in scores] == verdicts
    # 2. Grid, matrix and analysis direction (results_analysis reads this rule).
    assert pinned_metric_direction(binding) is direction
    group = observed_failure_group(
        "run", "metric", [_record(value) for value in values], binding
    )
    known = [verdict for verdict in verdicts if verdict is not None]
    if known:
        assert group["assessed_trials"] == len(known)
        assert group["failed_trials"] == known.count(False)
    else:
        assert group is None
    # 3. Run catalog definition and Regressed-first movement.
    assert metric_shape(binding)["higher_is_better"] is direction
    assert _ranked(binding) == EXPECTED_ORDER[direction]


def test_the_walk_choice_eval_stays_unknown_by_design():
    # The walk's pin: choice map + threshold, no declared direction; its
    # stored score ref is the numeric choice score only (no engine verdict).
    walk = [_score(dict(CHOICE), 1) for _ in range(3)]
    assert [score["value"] for score in walk] == [1, 1, 1]
    assert [_try(score) for score in walk] == [None, None, None]


def test_rows_without_a_score_never_count_as_pass_or_fail():
    binding = {**NUMERIC, "higher_is_better": True}
    scored = _score(binding, 0.9)
    missing = _score(binding, refs=[])
    not_assessed = _score(binding, 0.9, status="not_assessed")
    invalid = _score(binding, float("nan"))
    assert [_try(s) for s in (scored, missing, not_assessed, invalid)] == [
        True,
        None,
        None,
        None,
    ]
    assert missing["status"] == "not_assessed" and missing["value"] is None
    group = observed_failure_group(
        "run",
        "metric",
        [_record(0.9), _record(None, "not_assessed"), _record(0.1)],
        binding,
    )
    assert group["assessed_trials"] == 2
    assert group["failed_trials"] == 1


def test_an_explicit_external_direction_wins_over_a_legacy_one():
    binding = {
        **NUMERIC,
        "higher_is_better": True,
        "config_snapshot": {"external": {"higher_is_better": False}},
    }
    assert pinned_metric_direction(binding) is False
    assert metric_shape(binding)["higher_is_better"] is False
    assert _score(binding, 0.1)["passed"] is True


PASS_FAIL = {
    "evaluator_version_id": "version",
    "output_type_normalized": "pass_fail",
    "pass_threshold": 0.5,
}


def _item(value):
    return SimpleNamespace(
        id="item",
        status="completed",
        error=None,
        metadata={},
        output="answer",
        cost=None,
        latency_ms=None,
        trace_id=None,
        score_refs=[
            {
                "metric_id": "metric",
                "evaluator_version_id": "version",
                "status": "assessed",
                "value": value,
            }
        ],
    )


def test_grid_verdicts_read_one_try_never_an_aggregate():
    # results_grid.trial_record scores ONE run item (one row's try) through
    # assessed_scores -> pinned_score_passes; that per-try passed is what the
    # matrix try (trial_value) and so Success count.
    run = SimpleNamespace(status="completed")
    bindings = {"metric": PASS_FAIL}
    tries = [
        trial_record(_item(v), n, run, bindings) for n, v in enumerate([1, 0, 0.7])
    ]
    assert [t["scores"]["metric"]["passed"] for t in tries] == [True, False, None]
    assert [trial_value(t) for t in tries] == [True, False, None]
    # The per-row aggregate (result_cell) averages values and carries no
    # verdict at all, so no aggregate ever reaches the pass/fail rule.
    cell = result_cell(tries)
    assert cell["scores"]["metric"]["value"] == pytest.approx((1 + 0 + 0.7) / 3)
    assert "passed" not in cell["scores"]["metric"]


def test_pass_fail_one_try_value_not_0_1_stays_unknown_without_a_declared_direction():
    assert _score(PASS_FAIL, 0.7)["passed"] is None
    assert _score(PASS_FAIL, 0.2)["passed"] is None
    assert observed_failure_group("run", "metric", [_record(0.7)], PASS_FAIL) is None
    # The type still gives the direction for ranking and analysis.
    assert pinned_metric_direction(PASS_FAIL) is True
    assert metric_shape(PASS_FAIL)["higher_is_better"] is True
    # A declared direction keeps its threshold verdict, as at base.
    declared = {**PASS_FAIL, "higher_is_better": True}
    assert _score(declared, 0.7)["passed"] is True
    assert _score(declared, 0.2)["passed"] is False
