"""Grid and drawer badges share the ordered verdicts, not their mean."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_hub.services import results_grid

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "frontend/src/sections/experiment-detail/results/__tests__"
    / "trialOutcome.fixture.json"
)
CASES = json.loads(FIXTURE.read_text())
RUN = "run-verdicts"
METRIC = "verdict"
VERSION = "version-verdicts"
BINDINGS = {
    METRIC: {
        "evaluator_version_id": VERSION,
        "output_type_normalized": "pass_fail",
    }
}


def a_try(number, value):
    item = SimpleNamespace(
        id=f"item-{number}",
        status="completed",
        metadata={},
        error=None,
        output="answer",
        trace_id=None,
        cost=None,
        latency_ms=10,
        score_refs=[
            {
                "metric_id": METRIC,
                "evaluator_version_id": VERSION,
                "status": "assessed" if value is not None else "not_assessed",
                "value": value,
            }
        ],
    )
    return results_grid.trial_record(
        item, number, SimpleNamespace(status="completed"), BINDINGS
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
@pytest.mark.parametrize("full", [False, True], ids=["compact", "full"])
def test_grid_retains_each_trial_verdict(monkeypatch, case, full):
    trials = [a_try(i, value) for i, value in enumerate(case["values"], 1)]
    cell = results_grid.result_cell(trials)
    assert cell["scores"][METRIC] == case["score"]
    selection = SimpleNamespace(
        rows=[{"example_id": "example-verdicts", "results": {RUN: cell}}]
    )
    monkeypatch.setattr(
        results_grid, "response_parts", lambda selection, query: {"pagination": {}}
    )
    payload = results_grid.grid_results(
        selection,
        {"full": full, "example_id": "example-verdicts", "page": 0, "page_size": 1},
    )
    wire_cell = payload["rows"][0]["results"][RUN]
    assert wire_cell["scores"][METRIC] == case["score"]
    if full:
        assert wire_cell["trials_detail"] == trials
    else:
        assert wire_cell["trials_detail"] == [
            {"trial_number": i, "status": "completed", "trace_id": None}
            for i in range(1, len(trials) + 1)
        ]


def test_an_unrecorded_try_keeps_its_slot_without_a_verdict():
    unrecorded = results_grid.trial_record(
        None, 2, SimpleNamespace(status="completed"), BINDINGS
    )
    cell = results_grid.result_cell([a_try(1, 1), unrecorded, a_try(3, 0)])
    assert cell["scores"][METRIC] == {
        "value": 0.5,
        "n": 2,
        "status": "assessed",
        "rule": "assessed_trial_mean",
        "total_trials": 3,
        "error_trials": 0,
        "trials": [True, None, False],
    }


def test_nonbinary_assessed_values_do_not_become_trial_verdicts():
    cell = results_grid.result_cell([a_try(1, 0.5), a_try(2, 1)])
    assert cell["scores"][METRIC] == {
        "value": 0.75,
        "n": 2,
        "status": "assessed",
        "rule": "assessed_trial_mean",
        "total_trials": 2,
        "error_trials": 0,
        "trials": [None, True],
    }
