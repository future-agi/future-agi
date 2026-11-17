"""A dataset row's runs across experiments (the Row view's Runs tab).

From the row to the example ids its dataset versions give it (a version
item's ``legacy_row``), to the live runs on those versions and their
recorded tries of that example, through the Results projections
(results_catalog, results_grid). Only runs recorded per example are listed.
A classic experiment, whose results live in its snapshot's cells, has no
recorded tries and no link from its rows back to this row: it is counted
apart (``classic_experiments``), never listed.

Each run is described from the run row the read already loaded, and its
evaluators are validated once for all runs, so the read does not grow by a
projection per run. A run whose evaluators or native record cannot be proven
is left out, as a run with no pinned plan is.
"""

from math import isfinite
from statistics import mean, pstdev

from django.db.models import Exists, OuterRef, Q

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Row
from model_hub.models.experiments import (
    ExperimentRun,
    ExperimentRunItem,
    ExperimentsTable,
)
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services import results_catalog
from model_hub.services.dataset_runs import get_baseline
from model_hub.services.experiment_run_writes import RunWriteError, _planned
from model_hub.services.persisted_run_comparison import RunComparisonError, _bindings
from model_hub.services.results_catalog import (
    ResultsReadError,
    bounded_list,
    live_runs,
    metric_definitions,
    scoped_dataset,
)
from model_hub.services.results_grid import result_cell, trial_record
from model_hub.services.results_root_causes import pass_rule_keys, pinned_score_passes

MAX_RUNS = 500
# A row fails the primary evaluation in at least this share of 3+ runs.
HARD_CASE_SHARE = 0.7
HARD_CASE_RUNS = 3


def _number(value):
    return type(value) in (int, float) and isfinite(value)


def _error_text(error):
    if isinstance(error, dict):
        message = error.get("message")
        return message if isinstance(message, str) and message else None
    return error if isinstance(error, str) and error else None


def _evals(tries, bindings):
    out = {}
    for key, pin in bindings.items():
        scored = [
            t["scores"][key]
            for t in tries
            if t["scores"].get(key, {}).get("status") == "assessed"
        ]
        values = [s["value"] for s in scored if _number(s["value"])]
        verdicts = {s["passed"] for s in scored if s["passed"] is not None}
        value = mean(values) if values else None
        out[key] = {
            "value": value,
            # The shared catalog can merge different rules; use this run's pin.
            "passed": pinned_score_passes(value, pin),
            "n": len(values),
            "status": "assessed" if values else "not_assessed",
            "std": pstdev(values) if len(values) > 1 else 0,
            "flaky": len(verdicts) > 1,
        }
    return out


def _native(run):
    """A run with a native projection (results_catalog.native_projection)."""
    return run.task_kind != "external" and run.experiment_id is not None


def _described(run):
    """The run's fields the tab shows, as results_catalog.project_run builds
    them from the run row; no read per run. A native run with no name of its
    own is named by its label, as its native projection names it."""
    meta = run.metadata if isinstance(run.metadata, dict) else {}
    experiment = run.experiment if run.experiment_id else None
    if experiment is not None and (
        experiment.deleted or experiment.dataset_id != run.dataset_version.dataset_id
    ):
        experiment = None
    label = f"#{run.ordinal}" if run.ordinal else None
    name = meta.get("name") if isinstance(meta.get("name"), str) else None
    if _native(run):
        name = (name if name is not None and len(name) <= 255 else None) or label
    return {
        "label": label,
        "name": name,
        "experiment_id": str(run.experiment_id) if run.experiment_id else None,
        "experiment_name": experiment.name if experiment is not None else None,
        "dataset_version": {
            "id": str(run.dataset_version_id),
            "content_hash": run.dataset_version.content_hash,
        },
    }


def _checked_runs(dataset, ids):
    """The runs, loaded as results_catalog.checked_runs loads them, and the
    metrics their evaluators name, validated once for all of them. When that
    fails, each run is checked alone and the runs that fail are left out; if
    every run fails, the first error stands."""
    ids = {str(value) for value in ids}
    runs = bounded_list(
        live_runs(dataset).filter(id__in=ids),
        ("metadata", "model_config", "evaluator_versions"),
        limit=MAX_RUNS,
    )
    if {str(run.id) for run in runs} != ids:
        raise ResultsReadError("Run not found on this dataset", 404)
    try:
        return runs, results_catalog.validate_evaluators(dataset, runs)
    except ResultsReadError as first:
        kept, metrics = [], {}
        for run in runs:
            try:
                metrics.update(results_catalog.validate_evaluators(dataset, [run]))
            except ResultsReadError:
                continue
            kept.append(run)
        if not kept:
            raise first
        return kept, metrics


def _run_entry(run, items, example_id, *, baseline_id, latest_expected, expected):
    bindings = _bindings(run)
    trials = sorted(n for eid, n in _planned(run) if eid == example_id)
    by_trial = {item.trial_number: item for item in items}
    tries = [trial_record(by_trial.get(n), n, run, bindings) for n in trials]
    cell = result_cell(tries)
    done = any(t["status"] == "completed" for t in tries)
    errored = next((t for t in tries if t["status"] in {"error", "infra"}), None)
    projected = _described(run)
    http_binding = getattr(run, "_http_binding", None)
    source_fields = (
        {
            "task_kind": "external",
            "source": "http",
            "kind": "execution",
            "config": {},
            "native_config_id": str(http_binding.native_config_id),
            "output_column_id": str(http_binding.output_column_id),
        }
        if http_binding is not None
        else {}
    )
    return {
        "run_id": str(run.id),
        # The row's example in this run: the experiment's Data page opens a
        # row by its example id, never by the dataset row id.
        "example_id": example_id,
        "label": projected["label"],
        "name": projected["name"],
        "experiment_id": projected["experiment_id"],
        "experiment_name": projected["experiment_name"],
        "status": "completed" if done else "error" if errored else cell["status"],
        "output": cell["output"] if done else None,
        "error": _error_text(errored["error"]) if errored and not done else None,
        "trials": len(trials),
        "evals": _evals(tries, bindings),
        "latency_ms": cell["latency_ms"],
        "cost": cell["cost_usd"],
        "ran_at": max(item.updated_at for item in items),
        "dataset_version": projected["dataset_version"],
        "is_dataset_baseline": str(run.id) == baseline_id,
        "row_changed_after": latest_expected is not None
        and expected != latest_expected,
        "target_type": "http" if http_binding is not None else run.task_kind,
        **source_fields,
        "trace_id": next((t["trace_id"] for t in tries if t["trace_id"]), None),
        "trials_detail": [
            {
                "trial_number": t["trial_number"],
                "status": t["status"],
                "trace_id": t["trace_id"],
                "output": t["output"],
                "error": t["error"],
                "scores": t["scores"],
            }
            for t in tries
        ],
        "created_at": run.created_at,
    }


def _metrics(runs, validated):
    """Metric definitions, those that can give a verdict first; the first is primary.

    `validated` is what the evaluators' one validation returned."""
    defs = metric_definitions(runs, metrics=validated)
    rule_keys = {key for run in runs for key in pass_rule_keys(_bindings(run))}
    defs.sort(
        key=lambda m: (m["key"] not in rule_keys, str(m["name"]).casefold(), m["key"])
    )
    return [
        {
            "key": m["key"],
            "name": m["name"],
            "output_type": m["output_type"],
            "higher_is_better": m["higher_is_better"],
            "pass_threshold": m["pass_threshold"],
        }
        for m in defs
    ]


def _summary(entries, metrics, classic):
    primary = metrics[0] if metrics else None
    verdicts = (
        [e["evals"].get(primary["key"], {}).get("passed") for e in entries]
        if primary
        else []
    )
    scored = [v for v in verdicts if v is not None]
    fails = scored.count(False)
    hard = len(scored) >= HARD_CASE_RUNS and fails >= HARD_CASE_SHARE * len(scored)
    return {
        "runs": len(entries),
        "experiments": len({e["experiment_id"] for e in entries if e["experiment_id"]}),
        "primary_metric_name": primary["name"] if primary else None,
        "primary_scored": len(scored),
        "primary_pass": scored.count(True),
        "classic_experiments": classic,
        "flags": {
            "hard_case": hard,
            "fails_of_runs": [fails, len(scored)] if hard else None,
            "flaky": any(ev["flaky"] for e in entries for ev in e["evals"].values()),
            "expected_changed_after_runs": sum(
                1 for e in entries if e["row_changed_after"]
            ),
        },
    }


def _classic_experiments(dataset):
    recorded = ExperimentRun.no_workspace_objects.filter(experiment_id=OuterRef("pk"))
    return (
        ExperimentsTable.no_workspace_objects.filter(
            dataset_id=dataset.id,
            deleted=False,
        )
        .annotate(recorded=Exists(recorded))
        .filter(recorded=False)
        .count()
    )


def _scoped(entries, scope, baseline_id, current_version):
    if scope == "current_version":
        return [e for e in entries if e["dataset_version"]["id"] == current_version]
    if scope == "baseline_latest":
        newest = entries[0]["run_id"] if entries else None
        return [e for e in entries if e["run_id"] in {baseline_id, newest}]
    return entries


def _sorted(entries, sort, primary):
    newest = sorted(entries, key=lambda e: e["created_at"], reverse=True)
    if sort == "newest" or primary is None:
        return newest
    value = lambda e: e["evals"].get(primary["key"], {}).get("value")  # noqa: E731
    known = [e for e in newest if _number(value(e))]
    rest = [e for e in newest if not _number(value(e))]
    flip = (sort == "best") == (primary.get("higher_is_better") is not False)
    return sorted(known, key=value, reverse=flip) + rest


def row_runs(request, dataset_id, row_id, query):
    dataset = scoped_dataset(request, dataset_id)
    # As of a version a row deleted later still has its runs.
    rows = Row.all_objects if query.get("version") else Row.no_workspace_objects
    if not rows.filter(dataset_id=dataset.id, id=row_id).exists():
        raise ResultsReadError("Row not found", 404)
    versions = DatasetVersion.no_workspace_objects.filter(
        dataset_id=dataset.id,
        deleted=False,
        organization_id=dataset.organization_id,
        workspace_id=dataset.workspace_id,
    )
    items = {
        str(item.dataset_version_id): item
        for item in DatasetVersionItem.no_workspace_objects.filter(
            legacy_row_id=row_id,
            dataset_version__in=versions,
        ).only("dataset_version_id", "example_id", "expected")
    }
    # Newest first: the current version, and the row's newest expected value.
    order = [
        str(v)
        for v in versions.order_by("-created_at", "-id").values_list("id", flat=True)
    ]
    current = order[0] if order else None
    latest = next((items[v] for v in order if v in items), None)
    recorded = ExperimentRunItem.no_workspace_objects.filter(
        run_id=OuterRef("pk"), example_id__in=[i.example_id for i in items.values()]
    )
    candidates = (
        live_runs(dataset)
        .filter(
            dataset_version_id__in=list(items),
        )
        .filter(Q(metadata__kind__isnull=True) | ~Q(metadata__kind="quick_test"))
        .filter(Exists(recorded))
    )
    if candidates.count() > MAX_RUNS:
        raise ResultsReadError("This row has more runs than one read holds.", 503)
    runs, evaluator_metrics = _checked_runs(
        dataset, candidates.values_list("id", flat=True)
    )
    http_bindings = {
        binding.run_id: binding
        for binding in NativeRunBinding.objects.filter(
            run_id__in=[run.id for run in runs if run.task_kind == "external"],
            snapshot__experiment__dataset=dataset,
        )
    }
    for run in runs:
        run._http_binding = http_bindings.get(run.id)
    tries = {}
    for item in bounded_list(
        ExperimentRunItem.no_workspace_objects.filter(
            run_id__in=[r.id for r in runs],
            example_id__in=[i.example_id for i in items.values()],
        ).order_by("run_id", "trial_number"),
        ("output", "error", "score_refs", "metadata"),
    ):
        tries.setdefault(str(item.run_id), []).append(item)
    pin = get_baseline(dataset)
    baseline_id = str(pin.run_id) if pin else None
    entries, kept = [], []
    for run in runs:
        item = items[str(run.dataset_version_id)]
        own = [t for t in tries.get(str(run.id), []) if t.example_id == item.example_id]
        try:
            entry = (
                _run_entry(
                    run,
                    own,
                    str(item.example_id),
                    baseline_id=baseline_id,
                    latest_expected=latest.expected if latest else None,
                    expected=item.expected,
                )
                if own
                else None
            )
        except (RunWriteError, RunComparisonError):
            # Without a pinned plan or bindings the Results reads refuse the
            # run too; it is not listed.
            entry = None
        if entry:
            entries.append(entry)
            kept.append(run)
    metrics = _metrics(kept, evaluator_metrics)
    newest_first = _sorted(entries, "newest", None)
    listed = _scoped(newest_first, query.get("scope", "all"), baseline_id, current)
    listed = _sorted(
        listed, query.get("sort", "newest"), metrics[0] if metrics else None
    )
    for entry in listed:
        entry.pop("created_at", None)
    return {
        "summary": _summary(entries, metrics, _classic_experiments(dataset)),
        "metrics": metrics,
        "runs": listed,
    }
