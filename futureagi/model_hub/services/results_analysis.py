"""Read-only R5 projection over authorized runs and P03's full selection.

Selection is {example_ids: [str UUID], examples_total: int, complete: True}.
It must use the same filtered identity population as R3, never an R3 page.
"""
from math import fsum, isfinite

from django.db.models import Q
from rest_framework.exceptions import ValidationError

from model_hub.models.evals_metric import UserEvalMetric
from model_hub.services.dataset_runs import run_label
from model_hub.services.experiment_analysis import compare_run_items, summarize_run_items
from model_hub.services.experiment_run_writes import _canonical
from model_hub.services.persisted_run_comparison import (
    RunComparisonError, _MAX_ITEMS as MAX_RUN_ITEMS, _bindings, _items, _plan, _records,
)
from model_hub.services.results_root_causes import (
    observed_failure_group, pinned_metric_direction, root_cause_projection,
)

MAX_ITEMS = 40000
MAX_METRIC_ITEMS = 500000


class AnalysisUnavailable(Exception):
    """Complete evidence cannot be read within the S1 work contract (503)."""


def availability(reason=None, *, incompatible=False):
    return {"state": ("incompatible" if incompatible else "unavailable")
            if reason else "available", "reason": reason}


def _finite(value):
    try:
        return value if type(value) in (int, float) and isfinite(value) else None
    except OverflowError:
        return None


def _mean(values):
    scale = max((abs(value) for value in values), default=0.0)
    return fsum(value / scale for value in values) / len(values) * scale if scale else 0.0


def _incompatibility(before, after, metric):
    if before["run"].dataset_version_id != after["run"].dataset_version_id:
        return "Runs use different immutable dataset versions."
    if before["plan"] != after["plan"]:
        return "Runs do not share the exact example/trial plan."
    left, right = before["bindings"].get(metric), after["bindings"].get(metric)
    if left is None or right is None:
        return "The evaluator is not pinned in both runs."
    if _canonical(left) != _canonical(right):
        return "Evaluator version or configuration snapshots differ."
    return None


def _comparison(before, after, metric, records, selected):
    base_id, run_id = str(before["run"].id), str(after["run"].id)
    result = {
        "run_id": run_id, "baseline_run_id": base_id, "delta": None, "n": 0,
        "expected_n": sum(e in selected for e, _ in after["plan"]),
        "ci95": None, "p_value": None, "significant": None,
    }
    reason = _incompatibility(before, after, metric)
    if reason:
        result["availability"] = availability(reason, incompatible=True)
        return result
    compared = compare_run_items(records[base_id], records[run_id], metric=metric)
    deltas = [case.score_delta for case in compared.cases if case.score_delta is not None]
    result["n"] = len(deltas)
    result["delta"] = _mean(deltas) if deltas else None
    result["availability"] = availability(None if deltas else "No paired assessed trials in this scope.")
    return result


def _value(entry, metric, records, selected):
    binding = entry["bindings"].get(metric)
    summary = summarize_run_items(records, metric=metric)
    expected = sum(e in selected for e, _ in entry["plan"])
    return {
        "run_id": str(entry["run"].id), "value": summary.score_average,
        "n": summary.score_count, "expected_n": expected,
        "missing_n": expected - summary.item_count,
        "evaluator_version_id": binding["evaluator_version_id"] if binding else None,
        "ci95": None, "score_status_counts": dict(summary.score_status_counts),
        "unavailable_reason": (None if summary.score_count else
                               "No assessed trials in this scope."),
    }


def _bounded_plan(run):
    metadata = run.metadata
    envelope = metadata.get("_futureagi_run_write") if isinstance(metadata, dict) else None
    plan = envelope.get("plan") if isinstance(envelope, dict) else None
    identities = plan.get("identities") if isinstance(plan, dict) else None
    # The primitive uses one error for both a malformed plan and a large list.
    # Only a list that exceeds its actual bound is unavailable work; keep all
    # remaining shape/hash/identity validation in the immutable-plan primitive.
    if isinstance(identities, list) and len(identities) > MAX_RUN_ITEMS:
        raise AnalysisUnavailable(
            f"Run plan exceeds the S1 per-run bound of {MAX_RUN_ITEMS} identities."
        )
    return _plan(run)


def _bounded_items(run, plan):
    try:
        return _items(run, plan)
    except RunComparisonError as exc:
        # The pinned primitive has no typed work-bound error. Translate ONLY
        # its ceiling diagnostic; outside-plan and other integrity errors stay
        # unchanged. Boundary and corruption regressions pin this dependency.
        if str(exc) != f"run has more than {MAX_RUN_ITEMS} recorded items":
            raise
        raise AnalysisUnavailable(
            f"Run evidence exceeds the S1 per-run bound of {MAX_RUN_ITEMS} recorded items."
        ) from exc


def _metric_names(dataset, runs, metric_ids):
    """Evaluation names to show, the names the Data tab shows.

    The ids come from the authorized runs' own evaluator pins. A name is read
    only for a metric on this dataset or on one of these runs' experiments;
    any other pin keeps its key as its name.
    """
    if not metric_ids:
        return {}
    scope = Q(dataset_id=dataset.id)
    experiments = {str(run.experiment_id) for run in runs if run.experiment_id}
    if experiments:
        scope |= Q(source_id__in=experiments)
    rows = UserEvalMetric._base_manager.filter(scope, id__in=metric_ids).values_list("id", "name")
    return {str(pk): name.strip() for pk, name in rows
            if isinstance(name, str) and name.strip()}


def build_analysis(dataset, runs, selection, query):
    """Caller must authorize all runs first. No writes or read-side backfill."""
    if not 1 <= len(runs) <= 8:
        raise ValidationError("Select 1-8 distinct runs.")
    loaded = {}
    for run in runs:
        if run.dataset_version.dataset_id != dataset.id:
            raise RunComparisonError("Run is not on the selected dataset")
        if not isinstance(run.evaluator_versions, list) or len(run.evaluator_versions) > 64:
            raise AnalysisUnavailable("Evaluator bindings exceed the S1 read bound.")
        plan = _bounded_plan(run)
        loaded[str(run.id)] = {"run": run, "plan": plan, "bindings": _bindings(run)}
    if len(loaded) != len(runs):
        raise ValidationError("Run IDs must be unique.")
    union = {example for entry in loaded.values() for example, _ in entry["plan"]}
    if selection.get("complete") is not True:
        raise AnalysisUnavailable("The shared selector did not provide the full filtered population.")
    raw_ids = selection.get("example_ids")
    if not isinstance(raw_ids, (list, tuple)) or len(raw_ids) > MAX_ITEMS:
        raise AnalysisUnavailable("Invalid or oversized selected population.")
    if any(not isinstance(value, str) for value in raw_ids):
        raise AnalysisUnavailable("Selected example identities must be strings.")
    selected = set(raw_ids)
    if len(selected) != len(raw_ids) or not selected <= union:
        raise AnalysisUnavailable("Selected identities are duplicated or outside the immutable plans.")
    if type(selection.get("examples_total")) is not int or selection["examples_total"] != len(union):
        raise AnalysisUnavailable("Shared population count does not match the immutable plan union.")
    metric_ids = sorted({key for entry in loaded.values() for key in entry["bindings"]})
    plan_count = sum(len(entry["plan"]) for entry in loaded.values())
    if len(metric_ids) > 64 or plan_count > MAX_ITEMS or plan_count * len(metric_ids) > MAX_METRIC_ITEMS:
        raise AnalysisUnavailable("Complete analysis exceeds the S1 work bound; narrow the run set.")
    for field in ("bmetric", "wmetric"):
        if query.get(field) is not None and query[field] not in metric_ids:
            raise ValidationError({field: "Metric is not pinned in the selected runs."})
    baseline = query.get("baseline")
    if baseline == "none" or len(runs) < 2:
        baseline = None
    elif baseline is None:
        baseline = str(runs[0].id)
    if baseline is not None and baseline not in loaded:
        raise ValidationError({"baseline": "Baseline must be a selected run."})
    candidate = str(query["cand"]) if query.get("cand") else None
    if candidate is not None and (candidate not in loaded or candidate == baseline or baseline is None):
        raise ValidationError({"cand": "Candidate must be a selected non-baseline run with a baseline."})
    if baseline and candidate is None:
        candidate = next(key for key in loaded if key != baseline)
    for entry in loaded.values():
        entry["items"] = [item for item in _bounded_items(entry["run"], entry["plan"])
                          if str(item.example_id) in selected]
    descriptors = []
    for run_id, entry in loaded.items():
        run = entry["run"]
        planned = sum(e in selected for e, _ in entry["plan"])
        name = run.metadata.get("name") if isinstance(run.metadata, dict) else None
        descriptors.append({
            "run_id": run_id, "dataset_id": str(dataset.id),
            "experiment_id": str(run.experiment_id) if run.experiment_id else None,
            "dataset_version_id": str(run.dataset_version_id), "task_kind": run.task_kind,
            "label": run_label(run.ordinal) or run_id,
            "name": name if isinstance(name, str) else None, "status": run.status,
            "planned_trials": planned, "recorded_trials": len(entry["items"]),
            "missing_trials": planned - len(entry["items"]),
        })
    names = _metric_names(dataset, runs, metric_ids)
    metrics, verdicts, groups = [], [], []
    for metric in metric_ids:
        records = {key: _records(entry["items"], entry["bindings"], metric)
                   for key, entry in loaded.items()}
        pins = [entry["bindings"][metric] for entry in loaded.values() if metric in entry["bindings"]]
        directions = [pinned_metric_direction(pin) for pin in pins]
        thresholds = [_finite(pin.get("pass_threshold")) for pin in pins]
        direction = directions[0] if all(type(d) is bool and d == directions[0] for d in directions) else None
        threshold = thresholds[0] if all(t == thresholds[0] for t in thresholds) else None
        comparisons = ([] if baseline is None else [
            _comparison(loaded[baseline], entry, metric, records, selected)
            for key, entry in loaded.items() if key != baseline
        ])
        metrics.append({
            "key": metric, "name": names.get(metric, metric), "family": "eval",
            "aggregation": "assessed_trial_mean", "denominator_unit": "example_trial",
            "higher_is_better": direction, "pass_threshold": threshold,
            "per_run": [_value(entry, metric, records[key], selected) for key, entry in loaded.items()],
            "vs_baseline": comparisons,
        })
        for pair in comparisons:
            verdicts.append({
                "run_id": pair["run_id"], "baseline_run_id": baseline, "metric": metric,
                "status": "observed_difference" if pair["delta"] is not None else "unavailable",
                "delta": pair["delta"], "n": pair["n"], "significant": None,
                "reason": pair["availability"]["reason"] or
                "Observed mean candidate-minus-baseline delta on paired assessed trials; no significance claim.",
            })
        if query.get("include") == "root_causes":
            for key, entry in loaded.items():
                group = observed_failure_group(key, metric, records[key], entry["bindings"].get(metric, {}))
                if group is not None:
                    groups.append(group)
    root = root_cause_projection(groups, requested=query.get("include") == "root_causes")
    unavailable = {
        "uncertainty": availability("Confidence intervals, p-values and significance are not estimated in S1."),
        "candidate": availability("Candidate is explicit or the first selected non-baseline run, not a ranked winner."),
        "root_causes": root["availability"],
        "ops": availability("Cost and latency analysis are not projected by this S1 adapter."),
        "actions": availability("Choose winner, rescore and download need verified canonical-to-native mappings; use classic pages."),
    }
    for field in ("preference", "human", "agent", "worklist", "breakdown"):
        unavailable[field] = availability(f"{field.replace('_', ' ').capitalize()} evidence is not available in this S1 adapter.")
    return {
        "dataset_id": str(dataset.id),
        "running": any(run.status in {"draft", "queued", "running"} for run in runs),
        "runs": descriptors, "metrics": metrics, "verdicts": verdicts,
        "baseline_run_id": baseline,
        "baseline_label": next((r["label"] for r in descriptors if r["run_id"] == baseline), None),
        "candidate_run_id": candidate, "examples_in_view": len(selected),
        "examples_total": len(union), "scope": {"population": "full_filtered_plan", "complete": True},
        "default_evals": [], "preference": None, "human": None, "agent": None,
        "worklist": None, "breakdown": None, "breakdown_options": [],
        "pattern": root["pattern"], "root_causes": root["root_causes"],
        "observed_failures": root["observed_failures"], "availability": unavailable,
    }
