"""Baseline counts share the grid's assessed values and pinned verdicts."""

from types import SimpleNamespace

import pytest

from model_hub.serializers.dataset_results import DatasetResultsResultSerializer
from model_hub.services import evaluator_comparison_identity, results_catalog
from model_hub.services import results_grid as grid
from model_hub.services.results_matrix import matrix_results


@pytest.mark.parametrize("project", [grid.grid_results, matrix_results])
@pytest.mark.parametrize("baseline", [None, "base"])
@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize(
    "kind,higher,expected",
    [
        ("numeric", True, (3, 1, 1, 3)),
        ("numeric", False, (1, 3, 1, 3)),
        ("numeric", None, (0, 0, 0, 8)),
        ("choice", True, (2, 1, 2, 3)),
        ("choice", False, (1, 2, 2, 3)),
        ("boolean", False, (2, 1, 2, 3)),
    ],
)
def test_counts_use_shared_rows_and_pinned_rules(
    monkeypatch, project, baseline, visible, kind, higher, expected
):
    pin = {
        "evaluator_version_id": "v",
        "output_type_normalized": kind,
        "higher_is_better": higher,
        "pass_threshold": 0.5,
    }
    pins = dict.fromkeys(("quality", "accuracy"), pin)
    runs = [
        SimpleNamespace(id=rid, status="completed") for rid in ("base", "run", "other")
    ]
    selected = runs if visible else runs[1:]
    pairs = [
        (0, 1),
        (0, 1),
        (0.1, 0.4),
        (1, 0),
        (0.5, 0.5),
        (None, 1),
        (1, None),
        (None, None),
        (0, "absent"),
        ("absent", 1),
    ]
    if kind == "boolean":
        pairs[2], pairs[4] = (0, 0), (1, 1)
    rows, items = [], {}
    for index, pair in enumerate(pairs):
        eid, cells = str(index), {}
        for run in runs:
            value = pair[0 if run.id == "base" else 1]
            trials = []
            if value != "absent":
                for number in (1, 2):
                    trial = grid.trial_record(None, number, run, pins)
                    refs = [
                        {
                            "metric_id": key,
                            "evaluator_version_id": "v",
                            "value": 1 if value is None else value,
                            "status": "not_assessed" if value is None else "assessed",
                        }
                        for key in pins
                    ]
                    if index == 7:
                        refs = []
                    trial.update(
                        status="completed", scores=grid.assessed_scores(refs, pins)
                    )
                    items[run.id, eid, number] = trial
                    trials.append(trial)
            if run in selected:
                cells[run.id] = grid.result_cell(trials)
        rows.append(
            {"example_id": eid, "row_label": eid, "values": {}, "results": cells}
        )
    plans = {
        run.id: [(str(i), n) for i in range(len(pairs)) for n in (1, 2)] for run in runs
    }
    selection = SimpleNamespace(
        rows=rows,
        runs=selected,
        baseline_run_id=baseline,
        plans=plans,
        items=items,
        bindings={run.id: pins for run in runs},
        dataset=None,
        selection_id="selection",
        trial_numbers={
            rid: {str(i): [1, 2] for i in range(len(pairs))} for rid in plans
        },
    )
    monkeypatch.setattr(grid, "summary", lambda selection: {})
    monkeypatch.setattr(results_catalog, "checked_runs", lambda dataset, ids: [runs[0]])
    monkeypatch.setattr(
        evaluator_comparison_identity,
        "comparison_trial",
        lambda item, number, run, bindings: item,
    )
    names = ("improved", "regressed", "unchanged", "not_comparable")
    counts = dict(zip(names, expected, strict=False))
    wanted = {key: dict.fromkeys(("run", "other"), counts) for key in pins}
    payload = project(selection, {"page_size": 1})
    assert payload["vs_baseline"] == (wanted if baseline else None)
    assert len(payload["rows"]) == 1
    field = DatasetResultsResultSerializer().fields["vs_baseline"]
    assert (field.required, field.read_only, field.allow_null) == (False, True, True)
    serialized = DatasetResultsResultSerializer(payload).data
    assert serialized["vs_baseline"] == payload["vs_baseline"]
    selection.rows = []
    empty = {
        key: {rid: dict.fromkeys(names, 0) for rid in ("run", "other")} for key in pins
    }
    assert project(selection, {})["vs_baseline"] == (empty if baseline else None)
