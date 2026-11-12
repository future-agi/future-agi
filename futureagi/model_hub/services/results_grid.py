"""Grid and full-row projections, with explicit missing evidence and bounds."""
from math import isfinite

from rest_framework.renderers import JSONRenderer

from model_hub.services.experiment_run_read import _bounded
from model_hub.services.results_catalog import (
    ResultsReadError,
    metric_definitions,
    project_run,
)
from model_hub.services.results_root_causes import pinned_score_passes

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# A grid cell's stored failure reason; keeps a large failed run under the bound.
MAX_CELL_EXPLANATION = 2000


def assessed_scores(refs, bindings):
    result = {}
    if not isinstance(refs, list):
        raise ResultsReadError("Score evidence is malformed")
    for ref in refs:
        if not isinstance(ref, dict):
            raise ResultsReadError("Score evidence is malformed")
        key = str(ref.get("metric_id", ""))
        pin = bindings.get(key)
        version = ref.get("evaluator_version_id", ref.get("version_id"))
        if key in result or not pin or str(version) != pin["evaluator_version_id"]:
            raise ResultsReadError("Score evidence does not match the pinned evaluator")
        value = ref.get("value", ref.get("score"))
        status = ref.get("status", "not_assessed")
        finite = type(value) in (bool, int, float) and isfinite(value)
        if status == "assessed" and not finite:
            status, value = "invalid", None
        if status != "assessed":
            value = None
        passed = pinned_score_passes(value, pin) if status == "assessed" else None
        result[key] = {"value": value, "status": status, "passed": passed,
                       "evaluator_version_id": str(version),
                       "explanation": ref.get("explanation")}
    for key, pin in bindings.items():
        result.setdefault(key, {"value": None, "passed": None, "status": "not_assessed",
                                "evaluator_version_id": pin["evaluator_version_id"],
                                "explanation": None})
    return result


def trial_record(item, number, run, bindings):
    if item is None:
        active = run.status in {"draft", "queued", "running"}
        return {"trial_number": number, "status": "queued" if active else "not_reached",
                "output": None, "error": None, "scores": {}, "cost_usd": None,
                "wall_ms": None, "cost": None, "latency_ms": None,
                "trace_id": None, "result_id": None,
                "carried": False, "availability": "No recorded attempt"}
    meta = item.metadata if isinstance(item.metadata, dict) else {}
    err = item.error if isinstance(item.error, dict) else {}
    status = item.status
    if status == "pending":
        status = "queued"
    elif status == "failed" or (status == "completed" and item.error is not None):
        status = "infra" if err.get("kind") in {"infra", "infrastructure"} else "error"
    elif status == "skipped":
        status = "stopped" if err.get("kind") == "stopped_at_cap" else "not_reached"
    scores = assessed_scores(item.score_refs, bindings)
    # A failed execution cannot gain an assessed-success score from a stray ref.
    if status != "completed":
        scores = {key: {**score, "value": None, "passed": None, "status": "not_assessed"}
                  for key, score in scores.items()}
    return {"trial_number": number, "status": status, "output": item.output,
            "error": item.error, "scores": scores, "cost_usd": item.cost,
            "wall_ms": item.latency_ms, "cost": item.cost, "latency_ms": item.latency_ms,
            "trace_id": item.trace_id,
            "result_id": str(item.id), "carried": meta.get("carried") is True,
            "availability": None}


def mean(values):
    nums = [v for v in values if type(v) in (bool, int, float) and isfinite(v)]
    return sum(nums) / len(nums) if nums else None


def result_cell(trials):
    states = {t["status"] for t in trials}
    state = next((s for s in ("running", "queued", "infra", "error", "stopped", "not_reached")
                  if s in states), "completed")
    keys = {key for t in trials for key in t["scores"]}
    scores = {}
    for key in sorted(keys):
        known = [t["scores"][key] for t in trials if key in t["scores"]
                 and t["scores"][key]["status"] == "assessed"]
        scores[key] = {"value": mean([v["value"] for v in known]), "n": len(known),
                       "status": "assessed" if known else "not_assessed",
                       "rule": "assessed_trial_mean", "total_trials": len(trials),
                       # Tries whose evaluator ran and failed, so the cell can say so.
                       "error_trials": sum(1 for t in trials
                                           if t["scores"].get(key, {}).get("status") == "error")}
        # A cell no try scored carries its first failed try's
        # stored reason, as stored (bounded here; the full row keeps it whole).
        stored = next((t["scores"][key].get("explanation") for t in trials
                       if t["scores"].get(key, {}).get("status") == "error"
                       and isinstance(t["scores"][key].get("explanation"), str)
                       and t["scores"][key]["explanation"].strip()), None)
        if stored and not known:
            scores[key]["explanation"] = stored[:MAX_CELL_EXPLANATION]
    completed = [t for t in trials if t["status"] == "completed"]
    first = completed[0] if completed else (trials[0] if trials else {})
    cost = sum(t["cost_usd"] for t in trials) if trials and all(
        t["cost_usd"] is not None for t in trials) else None
    return {"status": state, "output": first.get("output"), "scores": scores,
            "output_trial_number": first.get("trial_number"), "result_id": first.get("result_id"), "trials": len(trials),
            "trials_detail": trials, "trace_ids": [t["trace_id"] for t in trials],
            "trace_summary": [{"status": t["status"], "trial_number": t["trial_number"],
                               "carried": t["carried"]} for t in trials],
            "cost_usd": cost, "cost": cost,
            "latency_ms": mean([t["wall_ms"] for t in trials]),
            "carried": any(t["carried"] for t in trials)}


def native_example_view(selection):
    """Read a native experiment's examples by column name, with Expected.

    A native experiment stores each example as {"values": {<source column id>:
    value}} and no expected (native_run_binding.persist_snapshot_identity).
    The runner reads that stored shape, so it stays as it is. This read names
    each value by its captured column and shows the experiment's Expected
    answers column (its column_id) as Expected instead of as an input. The
    create and edit paths point column_id at the snapshot copy of the column,
    so it is read by the source column id it was copied from.
    Returns None when the selection is not one native capture.
    """
    from model_hub.models.dataset_versions import DatasetVersion
    from model_hub.models.develop_dataset import Column
    from model_hub.models.experiments import ExperimentsTable

    version_ids = {getattr(run, "dataset_version_id", None) for run in selection.runs}
    if len(version_ids) != 1 or None in version_ids:
        return None
    version = DatasetVersion.no_workspace_objects.filter(
        pk=next(iter(version_ids))).only("id", "metadata").first()
    meta = version.metadata if version is not None and isinstance(version.metadata, dict) else {}
    columns = meta.get("columns")
    if meta.get("capture") != "native-snapshot-v1" or not isinstance(columns, dict):
        return None
    names = {str(pk): name for pk, name in Column.no_workspace_objects.filter(
        id__in=[str(v) for v in columns.values()]).values_list("id", "name")}
    labels = {str(source): names.get(str(snapshot)) for source, snapshot in columns.items()}
    experiment_ids = {run.experiment_id for run in selection.runs
                      if getattr(run, "experiment_id", None)}
    picked = set(ExperimentsTable.no_workspace_objects.filter(id__in=experiment_ids)
                 .values_list("column_id", flat=True)) if experiment_ids else set()
    expected_id = str(next(iter(picked))) if len(picked) == 1 and None not in picked else None
    # A snapshot column reads as the source column the stored values use; a
    # source column id (older experiments) stays as it is.
    source_of = {str(snapshot): str(source) for source, snapshot in columns.items()}
    expected_id = source_of.get(expected_id, expected_id)
    no_column = picked == {None}
    order = {str(cid): i for i, cid in enumerate(
        getattr(selection.dataset, "column_order", None) or [])}

    def label(key, taken):
        name = labels.get(key) or key
        return f"{name} ({key})" if name in taken else name

    def view(example):
        stored = example.input
        if (example.expected is not None or not isinstance(stored, dict)
                or set(stored) != {"values"} or not isinstance(stored["values"], dict)):
            return example.input, {"value": example.expected}
        values = stored["values"]
        inputs = {}
        for key in sorted(values, key=lambda k: order.get(k, len(order))):
            if key != expected_id:
                inputs[label(key, inputs)] = values[key]
        if expected_id is not None and expected_id in labels:
            # "primary" names the expected column, as every reader expects
            # (the row drawer's Compare tab, the Output diff, export).
            name = label(expected_id, {})
            value = values.get(expected_id)
            # A blank or missing expected cell is no expected value, as for
            # any row; the experiment still has its expected column, so the
            # row drawer says "No expected value", not "no reference column".
            if value is None or (isinstance(value, str) and not value.strip()):
                return inputs, {"value": None, "primary": name}
            return inputs, {"value": {name: value}, "primary": name}
        if no_column:
            return inputs, {"value": None, "reason": "no_reference_column"}
        return inputs, {"value": None}

    return view


def build_rows(selection):
    rows = []
    view = native_example_view(selection) if selection.runs else None
    for position, example in enumerate(selection.examples):
        eid = str(example.example_id)
        results = {}
        for run in selection.runs:
            rid = str(run.id)
            numbers = selection.trial_numbers[rid][eid]
            trials = [trial_record(selection.items.get((rid, eid, n)), n, run,
                                   selection.bindings[rid]) for n in numbers]
            results[rid] = result_cell(trials)
        inputs, expected = view(example) if view else (example.input, {"value": example.expected})
        rows.append({"example_id": eid, "row_order": position,
                     "row_label": example.external_id or str(position + 1),
                     "inputs": inputs, "expected": expected,
                     "values": inputs if isinstance(inputs, dict) else {"input": inputs},
                     "metadata": example.metadata, "results": results})
    return rows


def progress_by_run(runs, *, items=None):
    """Example counters from planned terminal/assessed trials, never claims.

    Raw trial counts stay in counts/recorded; the FE progress readers count
    examples. A row is done only after every planned trial is terminal, and
    scored only after every pinned evaluator assessed every successful trial.
    """
    from model_hub.models.experiments import ExperimentRunItem
    from model_hub.services.experiment_run_writes import RunWriteError, _planned
    from model_hub.services.persisted_run_comparison import _bindings
    from model_hub.services.results_catalog import bounded_list
    if items is None:
        items = bounded_list(ExperimentRunItem.no_workspace_objects.filter(
            run_id__in=[run.id for run in runs], deleted=False).only(
                "run_id", "example_id", "trial_number", "status", "error", "score_refs"),
            ("error", "score_refs"))
    grouped = {str(run.id): {} for run in runs}
    for item in items:
        rid = str(item.run_id)
        if rid in grouped:
            identity = (str(item.example_id), item.trial_number)
            if identity in grouped[rid]:
                raise ResultsReadError("Recorded progress duplicates the pinned plan")
            grouped[rid][identity] = item
    result = {}
    statuses = {"draft": "NotStarted", "queued": "Queued", "running": "Running",
                "completed": "Completed", "failed": "Failed", "cancelled": "Cancelled"}
    for run in runs:
        rid = str(run.id)
        evidence = grouped[rid]
        status = ("PartialCompleted" if run.status == "partial" and run.completed_at is not None
                  else statuses.get(run.status, run.status))
        progress = {"status": status, "unit": "examples",
                    "total": None, "done": None, "scored": None, "errors": None,
                    "recorded": len(evidence), "recorded_unit": "trials"}
        result[rid] = progress
        try:
            plan = _planned(run)
        except RunWriteError:
            progress["availability"] = "Immutable run plan unavailable"
            continue
        if not set(evidence) <= plan:
            raise ResultsReadError("Recorded progress is outside the pinned plan")
        pins = _bindings(run)
        examples = {}
        for eid, number in plan:
            examples.setdefault(eid, []).append(evidence.get((eid, number)))
        done = scored = errors = 0
        for trials in examples.values():
            terminal = all(t is not None and t.status in {"completed", "failed", "skipped"}
                           for t in trials)
            done += int(terminal)
            errors += int(any(t is not None and (t.status == "failed" or (
                t.status == "completed" and t.error is not None)) for t in trials))
            assessed = bool(pins) and all(t is not None and t.status == "completed"
                and t.error is None and all(s["status"] == "assessed" for s in
                    assessed_scores(t.score_refs, pins).values()) for t in trials)
            scored += int(assessed)
        progress.update(total=len(examples), done=done, scored=scored, errors=errors,
                        trial_total=len(plan), availability=None)
    return result


def summary(selection):
    defs = metric_definitions(selection.runs)
    aggregates = {}
    for definition in defs:
        key = definition["key"]
        aggregates[key] = {}
        for run in selection.runs:
            rid = str(run.id)
            vals = [row["results"][rid]["scores"].get(key, {}).get("value")
                    for row in selection.rows]
            known = [v for v in vals if v is not None]
            aggregates[key][rid] = {"value": mean(known), "n": len(known),
                                    "rule": "assessed_example_mean", "total": len(vals)}
    runs = []
    progress = progress_by_run(selection.runs, items=selection.items.values())
    for run in selection.runs:
        counts = {}
        for (rid, _, _), item in selection.items.items():
            if rid == str(run.id):
                counts[item.status] = counts.get(item.status, 0) + 1
        runs.append(project_run(run, counts=counts, progress=progress[str(run.id)]))
    return {"runs": runs, "metrics": defs, "human_metrics": [], "system_metrics": [],
            "aggregates": aggregates, "examples_total": len(selection.examples),
            "examples_in_view": len(selection.rows), "baseline_run_id": selection.baseline_run_id,
            "availability": {"human": "Not implemented in S1", "deltas": "Use analysis endpoint",
                             "aggregation": "Assessed values only; unknowns are excluded, never zero-filled"}}


def response_parts(selection, query):
    total = len(selection.rows)
    page, size = query.get("page", 0), query.get("page_size", 1000)
    start = page * size
    more = start + size < total
    return {"total": total, "summary": summary(selection),
            "pagination": {"page": page, "page_size": size, "total": total,
                           "has_more": more, "next": page + 1 if more else None},
            "scope": {"population": total, "complete": True,
                      "selection_id": selection.selection_id}}


def bound_response(payload):
    if len(JSONRenderer().render(payload)) > MAX_RESPONSE_BYTES:
        raise ResultsReadError("Response exceeds 8 MiB; reduce page_size or select fewer runs.", 503)
    return payload


def grid_results(selection, query):
    payload = response_parts(selection, query)
    rows = selection.rows
    if query.get("full"):
        wanted = str(query["example_id"])
        position = next((i for i, r in enumerate(rows) if r["example_id"] == wanted), None)
        if position is None:
            raise ResultsReadError("Example not found in this selection", 404)
        row = {**rows[position], "neighbors": {
            "prev": rows[position - 1]["example_id"] if position else None,
            "next": rows[position + 1]["example_id"] if position + 1 < len(rows) else None,
            "position": position + 1, "total": len(rows)}}
        payload["rows"] = [row]
        payload["pagination"].update(page=0, page_size=1, has_more=False, next=None)
    else:
        start = query.get("page", 0) * query.get("page_size", 1000)
        page_rows = rows[start:start + query.get("page_size", 1000)]
        if query.get("ids_only"):
            payload["example_ids"] = [r["example_id"] for r in page_rows]
        else:
            output_rows = []
            for row in page_rows:
                cells = {}
                for rid, original in row["results"].items():
                    cell = {k: v for k, v in original.items() if k != "trials_detail"}
                    cell["output"], cell["output_truncated"], cell["output_bytes"] = _bounded(cell["output"], 4096)
                    cells[rid] = cell
                output_rows.append({**row, "results": cells})
            payload["rows"] = output_rows
    return bound_response(payload)
