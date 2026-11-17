"""Matrix keeps trial identities and errors distinct; it never fabricates a fail."""
from model_hub.services.results_grid import response_parts, bound_response
from model_hub.services.results_root_causes import pass_rule_keys


def trial_value(trial, measure="pass_at_1", rules=None):
    """One try's outcome on the matrix.

    pass@1 counts a completed try by the scores of the evaluations that have
    a pass/fail rule (`rules`: their metric ids, pass_rule_keys). A known
    fail decides: the try fails when one of them failed, even if another was
    not assessed or gave no verdict, so it agrees with the Fails filter. It
    passes when every one of them gave a verdict and all passed. It is left
    out (None) when it has none, or when none failed and one of them was not
    assessed or gave no verdict. A score with no rule neither passes nor
    fails it. With no `rules` (a read without pins), every score is graded.
    """
    status = trial.get("status")
    if status in {"error", "infra"}:
        return status
    if status != "completed":
        return None
    scores = trial.get("scores", {})
    if measure != "pass_at_1":
        score = scores.get(measure, {})
        value = score.get("value")
        # Matrix's binary channel cannot reinterpret a continuous score as false.
        return value if score.get("status") == "assessed" and value in (0, 1) else None
    graded = [s for key, s in scores.items() if rules is None or key in rules]
    if not graded:
        return None
    if any(s.get("status") == "assessed" and s.get("passed") is False for s in graded):
        return False
    if any(s.get("status") != "assessed" or s.get("passed") is None for s in graded):
        return None
    return all(s["passed"] for s in graded)


def failing_examples(run, example_ids):
    """Which of `example_ids` one run recorded, failed, or saw error.

    Only the run's own items and pinned checks count, read as trial_value reads
    them: an example fails when one of its tries failed a check with a pass/fail
    rule, and errored when a try errored or hit infrastructure trouble
    (FAILED_TRIES) or was cancelled. Returns (recorded, failing, errored), sets
    of example id strings.
    """
    from model_hub.models.experiments import ExperimentRunItem
    from model_hub.services.persisted_run_comparison import _bindings
    from model_hub.services.results_grid import FAILED_TRIES, trial_record

    bindings = _bindings(run)
    rules = pass_rule_keys(bindings)
    recorded, failing, errored = set(), set(), set()
    items = ExperimentRunItem.no_workspace_objects.filter(
        run=run, example_id__in=example_ids
    )
    for item in items:
        recorded.add(str(item.example_id))
        trial = trial_record(item, item.trial_number, run, bindings)
        if trial_value(trial, rules=rules) is False:
            failing.add(str(item.example_id))
        error = item.error if isinstance(item.error, dict) else {}
        if trial["status"] in FAILED_TRIES or (
            item.status == "skipped"
            and error.get("type") == "ExecutionCancelled"
            and error.get("code") == "agent_cancelled"
        ):
            errored.add(str(item.example_id))
    return recorded, failing, errored


def matrix_results(selection, query):
    payload = response_parts(selection, query)
    measure = query.get("measure", "pass_at_1")
    # The pass/fail rule of each run's pinned evaluations. A selection built
    # without pins grades every score.
    bindings = getattr(selection, "bindings", None)
    rules = {str(run.id): pass_rule_keys(bindings[str(run.id)])
             for run in selection.runs} if bindings is not None else {}
    start = query.get("page", 0) * query.get("page_size", 500)
    rows = []
    for row in selection.rows[start:start + query.get("page_size", 500)]:
        cells = {}
        for rid, cell in row["results"].items():
            trials = cell["trials_detail"]
            cells[rid] = {
                "trials": [trial_value(t, measure, rules.get(rid)) for t in trials],
                "trial_numbers": [t["trial_number"] for t in trials],
                "result_ids": [t["result_id"] for t in trials],
                "cost_usd": [t["cost_usd"] for t in trials],
                "wall_ms": [t["wall_ms"] for t in trials],
                "trace_ids": [t["trace_id"] for t in trials],
                "trace_summary": cell["trace_summary"],
                "failed_evals": [[key for key, score in t["scores"].items()
                                  if score.get("passed") is False] for t in trials],
                "carried": [t["carried"] for t in trials],
                # The row's assessed scores (the grid cell's own `scores`): a
                # try scored by an eval with no pass/fail rule has no outcome
                # above, so the Matrix shows its score instead of a blank.
                "scores": {key: {"value": score["value"], "status": score["status"],
                                 "n": score["n"]}
                           for key, score in cell["scores"].items()},
            }
            if measure == "pass_at_1":
                # The evaluations each try passed: a try left out while one of
                # them passed is missing a Pass/fail result, not without one.
                cells[rid]["passed_evals"] = [
                    [key for key, score in t["scores"].items()
                     if score.get("passed") is True]
                    for t in trials
                ]
        rows.append(
            {
                "row_id": row["example_id"],
                "label": row["row_label"],
                "expected": row.get("expected"),
                "metadata": row.get("metadata"),
                "values": row["values"],
                "source": None,
                "cells": cells,
            }
        )
    payload["rows"] = rows
    payload["columns"] = {}
    for run in selection.runs:
        rid = str(run.id)
        sizes = {len(v) for v in selection.trial_numbers[rid].values()}
        payload["columns"][rid] = {"trials": next(iter(sizes)) if len(sizes) == 1 else None,
                                   "status": run.status, "trial_count_source": "immutable_plan"}
        # How many of the run's evaluations have a pass/fail rule: with none,
        # Success has no figure.
        if measure == "pass_at_1" and rid in rules:
            payload["columns"][rid]["pass_fail_evals"] = len(rules[rid])
    return bound_response(payload)
