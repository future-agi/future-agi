"""Matrix keeps trial identities and errors distinct; it never fabricates a fail."""
from model_hub.services.results_grid import response_parts, bound_response


def trial_value(trial, measure="pass_at_1"):
    status = trial["status"]
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
    known = [s.get("passed") for s in scores.values()]
    if not known or any(value is None for value in known):
        return None
    return all(known)


def matrix_results(selection, query):
    payload = response_parts(selection, query)
    start = query.get("page", 0) * query.get("page_size", 500)
    rows = []
    for row in selection.rows[start:start + query.get("page_size", 500)]:
        cells = {}
        for rid, cell in row["results"].items():
            trials = cell["trials_detail"]
            cells[rid] = {
                "trials": [trial_value(t, query.get("measure", "pass_at_1")) for t in trials],
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
        rows.append({"row_id": row["example_id"], "label": row["row_label"],
                     "values": row["values"], "source": None, "cells": cells})
    payload["rows"] = rows
    payload["columns"] = {}
    for run in selection.runs:
        rid = str(run.id)
        sizes = {len(v) for v in selection.trial_numbers[rid].values()}
        payload["columns"][rid] = {"trials": next(iter(sizes)) if len(sizes) == 1 else None,
                                   "status": run.status, "trial_count_source": "immutable_plan"}
    return bound_response(payload)
