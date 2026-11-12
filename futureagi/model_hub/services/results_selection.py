"""Shared, fully scoped population for R3 variants and R5 analysis.

select_results serves R3; select_analysis_population is the P04 interface.
rows are in filtered/sorted order; examples and plans retain the original
population. No page is applied here. Unknown provenance fails closed.
"""
from dataclasses import dataclass, field
import hashlib
import json
import operator
from copy import deepcopy

from django.db.models import Count

from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.experiments import ExperimentRunItem
from model_hub.services.experiment_run_writes import _planned, _canonical, RunWriteError
from model_hub.services.persisted_run_comparison import _bindings
from model_hub.services.results_catalog import (
    ResultsReadError, bounded_list, checked_runs, scoped_dataset, pinned_baseline, metric_shape,
)


@dataclass
class ResultsSelection:
    dataset: object
    runs: list
    baseline_run_id: str | None
    plans: dict
    examples: list
    items: dict
    bindings: dict
    trial_numbers: dict
    rows: list = field(default_factory=list)
    selection_id: str = ""


OPS = {"eq": operator.eq, "=": operator.eq, "ne": operator.ne, "!=": operator.ne,
       "lt": operator.lt, "<": operator.lt, "lte": operator.le, "<=": operator.le,
       "gt": operator.gt, ">": operator.gt, "gte": operator.ge, ">=": operator.ge}


def _compare(value, op, expected):
    if value is None:
        return False
    try:
        return OPS[op](value, expected)
    except TypeError:
        return False


def _run_ids(pill, selection):
    ids = [str(r.id) for r in selection.runs]
    run = pill.get("run", "any_candidate")
    if run == "any_run":
        return ids
    if run == "any_candidate":
        return [rid for rid in ids if rid != selection.baseline_run_id]
    if run not in ids:
        raise ResultsReadError("Filter run not found in this selection", 404)
    return [run]


def _flaky(cell):
    outcomes = []
    for trial in cell["trials_detail"]:
        values = [score["passed"] for score in trial["scores"].values()]
        if values and all(v is not None for v in values):
            outcomes.append(all(values))
    return True in outcomes and False in outcomes


def _matches(row, pill, selection):
    kind = pill["type"]
    if kind == "rows":
        return row["example_id"] in pill["ids"]
    if kind == "meta":
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
        return _compare(metadata.get(pill["key"]), pill["op"], pill["value"])
    if kind == "split":
        meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
        names = meta.get("split_names", [])
        if isinstance(names, str):
            names = [names]
        if not names:
            names = [meta.get("split_name", "__none__")]
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            raise ResultsReadError("Persisted split metadata is malformed")
        return bool(set(names) & set(pill["values"]))
    for rid in _run_ids(pill, selection):
        cell = row["results"][rid]
        if kind == "error" and any(t["status"] in {"error", "infra"} for t in cell["trials_detail"]):
            return True
        if kind == "flaky" and _flaky(cell):
            return True
        if kind == "fails" and any(t["scores"].get(pill["metric"], {}).get("passed") is False
                                   for t in cell["trials_detail"]):
            return True
        if kind == "score":
            value = cell["scores"].get(pill["metric"], {}).get("value")
            if _compare(value, pill["op"], pill["value"]):
                return True
        if kind in {"latency", "cost"}:
            key = "latency_ms" if kind == "latency" else "cost_usd"
            if _compare(cell[key], pill["op"], pill["value"]):
                return True
    return False


def regressed_first(rows, selection):
    """Stable worse/better/rest groups over the complete assessed population.

    Unknown/missing values never move a row. Metric direction comes from its
    pinned definition. Trials are averaged first, as in the grid. System cost
    and latency use the shared 10% movement threshold (lower is better).
    """
    from model_hub.services.results_grid import result_cell, trial_record
    baseline = selection.baseline_run_id
    candidates = [str(run.id) for run in selection.runs if str(run.id) != baseline]
    if not baseline or not candidates:
        raise ResultsReadError("Regressed sort requires a baseline and a candidate", 400)
    baseline_run = next((run for run in selection.runs if str(run.id) == baseline), None)
    if baseline_run is None:
        baseline_run = checked_runs(selection.dataset, [baseline])[0]
    numbers = {}
    for eid, number in sorted(selection.plans[baseline]):
        numbers.setdefault(eid, []).append(number)
    definitions = {key: metric_shape(pin) for key, pin in selection.bindings[baseline].items()}

    def rank(row):
        eid = row["example_id"]
        base = row["results"].get(baseline)
        if base is None:
            base = result_cell([trial_record(selection.items.get((baseline, eid, n)), n,
                baseline_run, selection.bindings[baseline]) for n in numbers[eid]])
        movements = []
        for rid in candidates:
            candidate = row["results"][rid]
            for key, definition in definitions.items():
                bv = base["scores"].get(key, {}).get("value")
                cv = candidate["scores"].get(key, {}).get("value")
                direction = definition["higher_is_better"]
                if bv is None or cv is None or direction is None:
                    continue
                delta = cv - bv
                threshold = 0 if definition["output_type"] == "pass_fail" else 0.05
                if abs(delta) > threshold + 1e-12:
                    movements.append(delta if direction else -delta)
            for key in ("cost_usd", "latency_ms"):
                bv, cv = base[key], candidate[key]
                if bv is not None and cv is not None and cv != bv and abs(cv - bv) >= abs(bv) * 0.1:
                    movements.append(bv - cv)
        return 0 if any(v < 0 for v in movements) else 1 if any(v > 0 for v in movements) else 2

    return sorted(rows, key=rank)


def filter_rows(selection, query):
    rows = selection.rows
    all_ids = {row["example_id"] for row in rows}
    metric_ids = {key for pins in selection.bindings.values() for key in pins}
    pills = query.get("filters", [])
    # Validation precedes filtering, including when the population is empty.
    for pill in pills:
        if pill["type"] == "rows" and not set(pill["ids"]) <= all_ids:
            raise ResultsReadError("Example not found in the immutable plan", 404)
        if "metric" in pill and pill["metric"] not in metric_ids:
            raise ResultsReadError("Evaluator not found in this selection", 404)
        if "run" in pill:
            _run_ids(pill, selection)
    if query.get("example_id") and str(query["example_id"]) not in all_ids:
        raise ResultsReadError("Example not found in the immutable plan", 404)
    for pill in pills:
        rows = [row for row in rows if _matches(row, pill, selection)]
    show = query.get("show", "all")
    if show == "errors":
        rows = [row for row in rows if any(t["status"] in {"error", "infra"}
                for c in row["results"].values() for t in c["trials_detail"])]
    elif show == "failed":
        rows = [row for row in rows if any(score["passed"] is False
            for cell in row["results"].values() for t in cell["trials_detail"]
            for score in t["scores"].values())]
    elif show == "flaky":
        rows = [row for row in rows if any(_flaky(c) for c in row["results"].values())]
    search = query.get("search", "").casefold()
    if search:
        rows = [row for row in rows if search in json.dumps(
            {"inputs": row["inputs"], "expected": row["expected"], "label": row["row_label"]},
            ensure_ascii=False, sort_keys=True).casefold()]
    sort = query.get("sort", "order")
    if sort == "regressed":
        rows = regressed_first(rows, selection)
    elif sort.lstrip("-") == "input":
        rows = sorted(rows, key=lambda r: (_canonical(r["inputs"]), r["example_id"]),
                      reverse=sort.startswith("-"))
    elif sort == "-order":
        rows = list(reversed(rows))
    return rows


def select_results(request, dataset_id, query):
    dataset = scoped_dataset(request, dataset_id)
    baseline, _ = pinned_baseline(dataset, query.get("baseline"))
    return _select_population(dataset, [str(rid) for rid in query["runs"]], baseline, query)


def _select_population(dataset, ids, baseline, query, *, max_items=20000, per_run_limit=None):
    from model_hub.services.results_grid import build_rows
    authorized_ids = ids + ([baseline] if baseline and baseline not in ids else [])
    runs = checked_runs(dataset, authorized_ids)
    plans, bindings = {}, {}
    try:
        for run in runs:
            if per_run_limit is not None:
                meta = run.metadata if isinstance(run.metadata, dict) else {}
                envelope = meta.get("_futureagi_run_write", {})
                raw_plan = envelope.get("plan", {}) if isinstance(envelope, dict) else {}
                identities = raw_plan.get("identities") if isinstance(raw_plan, dict) else None
                if isinstance(identities, list) and len(identities) > per_run_limit:
                    raise ResultsReadError("Run plan exceeds the analysis per-run work limit.", 503)
            plans[str(run.id)] = _planned(run)
            bindings[str(run.id)] = _bindings(run)
    except RunWriteError as exc:
        raise ResultsReadError("Immutable provenance unavailable; use classic results.") from exc
    reference = runs[0]
    plan = plans[str(reference.id)]
    for run in runs[1:]:
        if (run.dataset_version_id != reference.dataset_version_id or
                plans[str(run.id)] != plan or
                _canonical(bindings[str(run.id)]) != _canonical(bindings[str(reference.id)])):
            raise ResultsReadError("Runs require the same immutable version, trial plan and evaluator bindings; mixed comparison is not supported.")
    if sum(len(p) for p in plans.values()) > max_items:
        raise ResultsReadError(f"Selection exceeds {max_items} planned trials; select fewer runs.", 503)
    example_ids = {eid for eid, _ in plan}
    examples = bounded_list(DatasetVersionItem.no_workspace_objects.filter(
        dataset_version=reference.dataset_version, example_id__in=example_ids, deleted=False)
        .only("example_id", "input", "expected", "metadata", "external_id").order_by("example_id"),
        ("input", "expected", "metadata"), limit=min(10000, max_items))
    if {str(e.example_id) for e in examples} != example_ids:
        raise ResultsReadError("Example not found in the immutable version", 404)
    raw_query = ExperimentRunItem.no_workspace_objects.filter(run_id__in=authorized_ids, deleted=False)
    if per_run_limit is not None and raw_query.values("run_id").annotate(
            n=Count("id")).filter(n__gt=per_run_limit).exists():
        raise ResultsReadError("Run evidence exceeds the analysis per-run work limit.", 503)
    raw_items = bounded_list(raw_query.order_by("run_id", "example_id", "trial_number"),
        ("input", "expected", "output", "error", "score_refs", "metadata"), limit=max_items)
    items = {}
    for item in raw_items:
        rid, eid = str(item.run_id), str(item.example_id)
        key = (rid, eid, item.trial_number)
        if (eid, item.trial_number) not in plans[rid] or key in items:
            raise ResultsReadError("Recorded evidence is outside or duplicates the pinned plan")
        items[key] = item
    selected_runs = [run for run in runs if str(run.id) in ids]
    trial_numbers = {}
    for rid in ids:
        trial_numbers[rid] = {eid: [] for eid in example_ids}
        for eid, trial in sorted(plans[rid]):
            trial_numbers[rid][eid].append(trial)
    selection = ResultsSelection(dataset, selected_runs, baseline, plans, examples, items,
                                 bindings, trial_numbers)
    selection.rows = build_rows(selection)
    if query.get("measure", "pass_at_1") != "pass_at_1" and not any(
            query["measure"] in b for b in bindings.values()):
        raise ResultsReadError("Evaluator not found in this selection", 404)
    selection.rows = filter_rows(selection, query)
    identity = {"runs": ids, "baseline": baseline,
                "rows": [r["example_id"] for r in selection.rows],
                "version": str(reference.dataset_version_id),
                "updates": [[str(r.id), str(r.updated_at)] for r in runs],
                "item_updates": [[str(i.id), str(i.updated_at)] for i in raw_items]}
    selection.selection_id = hashlib.sha256(_canonical(identity).encode()).hexdigest()
    return selection


def select_analysis_population(*, dataset, runs, filters, baseline_id, max_items):
    """Full R3 population for an already request-scoped dataset, never a page.

    Re-resolve all runs rather than trusting stale caller objects. P04's caller
    resolves baseline defaults; None/none here means no baseline. The stricter
    R3 20000-trial/10000-example ceiling still applies even if P04 asks for more.
    ResultsReadError is a DRF APIException so the existing P04 view preserves
    404/409/503; semantic filter validation uses the same DRF 400 as R3.
    """
    from model_hub.models.develop_dataset import Dataset
    from model_hub.serializers.dataset_results import filters_value
    from model_hub.serializers.results_runs import identifier
    if type(max_items) is not int or max_items < 1:
        raise ResultsReadError("max_items must be a positive integer.", 400)
    if not isinstance(runs, (list, tuple)) or not 1 <= len(runs) <= 8:
        raise ResultsReadError("Select 1-8 distinct runs.", 400)
    ids = [identifier(run.id) for run in runs]
    if len(ids) != len(set(ids)):
        raise ResultsReadError("Select 1-8 distinct runs.", 400)
    live = Dataset.no_workspace_objects.filter(pk=dataset.pk, deleted=False,
        organization_id=dataset.organization_id, workspace_id=dataset.workspace_id).first()
    if live is None:
        raise ResultsReadError("Dataset not found", 404)
    baseline = None if baseline_id in (None, "none") else identifier(baseline_id)
    query = {"filters": filters_value(deepcopy(filters))}
    selected = _select_population(live, ids, baseline, query,
                                  max_items=min(max_items, 20000), per_run_limit=10000)
    return {"example_ids": [row["example_id"] for row in selected.rows],
            "examples_total": len(selected.examples), "complete": True}
