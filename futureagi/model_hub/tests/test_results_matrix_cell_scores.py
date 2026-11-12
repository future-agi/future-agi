"""W28: a matrix cell carries the row's assessed scores next to its tries.

A try scored by an eval with no pass/fail rule has no binary outcome (the
matrix never fabricates a fail), so the Matrix shows the row's score instead.
Pure projection tests: the selection is built in memory, no database.
"""

from types import SimpleNamespace

from model_hub.services import results_matrix
from model_hub.services.results_grid import result_cell

RUN = "22222222-2222-4222-8222-222222222222"
METRIC = "33333333-3333-4333-8333-333333333333"


def trial(value=1, passed=None, status="completed"):
    assessed = value is not None
    return {
        "trial_number": 1,
        "status": status,
        "output": "echo",
        "error": None,
        "scores": {
            METRIC: {
                "value": value,
                "status": "assessed" if assessed else "not_assessed",
                "passed": passed,
                "evaluator_version_id": "version-1",
                "explanation": None,
            }
        },
        "cost_usd": None,
        "wall_ms": 12,
        "cost": None,
        "latency_ms": 12,
        "trace_id": None,
        "result_id": "result-1",
        "carried": False,
        "availability": None,
    }


def project(monkeypatch, trials):
    monkeypatch.setattr(
        results_matrix, "response_parts", lambda selection, query: {"summary": {}}
    )
    row = {
        "example_id": "example-1",
        "row_label": "1",
        "values": {"question": "What is 2 + 2?"},
        "results": {RUN: result_cell(trials)},
    }
    selection = SimpleNamespace(
        rows=[row],
        runs=[SimpleNamespace(id=RUN, status="completed")],
        trial_numbers={RUN: {"example-1": [t["trial_number"] for t in trials]}},
    )
    data = results_matrix.matrix_results(selection, {"page": 0, "page_size": 500})
    return data["rows"][0]["cells"][RUN]


def test_score_without_pass_rule_stays_unknown_and_is_carried(monkeypatch):
    cell = project(monkeypatch, [trial(value=1, passed=None)])
    assert cell["trials"] == [None]
    assert [t["status"] for t in cell["trace_summary"]] == ["completed"]
    assert cell["scores"] == {METRIC: {"value": 1, "status": "assessed", "n": 1}}


def test_unassessed_score_is_not_given_a_value(monkeypatch):
    cell = project(monkeypatch, [trial(value=None)])
    assert cell["trials"] == [None]
    assert cell["scores"] == {
        METRIC: {"value": None, "status": "not_assessed", "n": 0}
    }


def test_binary_outcome_is_unchanged_next_to_the_score(monkeypatch):
    cell = project(monkeypatch, [trial(value=1, passed=True)])
    assert cell["trials"] == [True]
    assert cell["scores"][METRIC]["value"] == 1
