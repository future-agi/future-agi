"""Read-only, request-scoped FE catalog; never expose arbitrary config blobs."""
from math import isfinite

from rest_framework.exceptions import APIException

from django.db.models import F, Q, Sum, TextField
from django.db.models.functions import Cast, Length

from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem, ExperimentsTable
from model_hub.models.evals_metric import UserEvalMetric, EvalTemplateVersion
from model_hub.services.dataset_runs import get_baseline, baseline_payload, _counts_by_run
from model_hub.services.experiment_run_writes import _digest, _planned, RunWriteError
from model_hub.services.persisted_run_comparison import _bindings
from model_hub.services.results_root_causes import pinned_metric_direction
from model_hub.utils.workspace_scope import scoped_dataset_queryset
from tfc.middleware.workspace_context import workspace_scope_is_empty

MAX_ITEMS = 20000
MAX_BYTES = 16 * 1024 * 1024

class ResultsReadError(APIException):
    """Typed read failure; also preserves status through P04/DRF callers."""
    def __init__(self, message, status=409):
        self.status = self.status_code = status
        super().__init__(message)

def scoped_dataset(request, dataset_id):
    dataset = scoped_dataset_queryset(request).filter(pk=dataset_id).first()
    if dataset is None:
        raise ResultsReadError("Dataset not found", 404)
    return dataset

def live_runs(dataset):
    return ExperimentRun.no_workspace_objects.filter(
        Q(experiment__isnull=True) | Q(experiment__deleted=False,
                                      experiment__dataset_id=dataset.id),
        deleted=False, dataset_version__deleted=False,
        dataset_version__dataset_id=dataset.id,
        dataset_version__organization_id=dataset.organization_id,
        dataset_version__workspace_id=dataset.workspace_id,
    ).select_related("dataset_version", "experiment").only(
        "id", "experiment_id", "dataset_version_id", "task_kind", "status", "ordinal",
        "experiment__id", "experiment__dataset_id", "experiment__deleted",
        "experiment__name", "experiment__description",
        "metadata", "model_config", "prompt_version_id", "agent_version_id",
        "evaluator_versions", "started_at", "completed_at", "created_at", "updated_at",
        "dataset_version__id", "dataset_version__dataset_id", "dataset_version__content_hash",
    )

def bounded_list(queryset, fields, limit=MAX_ITEMS):
    """Check DB-side size before materializing JSON (UTF-8 <= four bytes/char)."""
    if queryset.count() > limit:
        raise ResultsReadError("Selected evidence exceeds the read work limit; narrow the run set.", 503)
    size = 0
    for field in fields:
        size += queryset.aggregate(n=Sum(Length(Cast(field, TextField()))))["n"] or 0
    if size * 4 > MAX_BYTES:
        raise ResultsReadError("Selected evidence exceeds the read byte limit; narrow the run set.", 503)
    return list(queryset)

def checked_runs(dataset, ids):
    ids = [str(value) for value in ids]
    runs = bounded_list(live_runs(dataset).filter(id__in=ids),
                        ("metadata", "model_config", "evaluator_versions"), limit=500)
    by_id = {str(run.id): run for run in runs}
    if set(by_id) != set(ids):
        raise ResultsReadError("Run not found on this dataset", 404)
    validate_evaluators(dataset, runs)
    return [by_id[value] for value in ids]

def native_evaluator_metrics(dataset, run, pins):
    """Authorize this binding only; a snapshot is never a global metric scope."""
    from model_hub.models.native_experiment_runs import NativeRunBinding
    from model_hub.services.native_run_binding import (
        live_dataset, validate_snapshot, validate_run_references,
    )
    try:
        dataset = live_dataset(dataset)
        binding = NativeRunBinding.objects.select_related(
            "snapshot__dataset_version", "snapshot__snapshot_dataset", "variant",
            "output_column", "run__experiment",
        ).filter(run_id=run.id, run__deleted=False, run__experiment__deleted=False,
                 run__experiment__dataset=dataset).first()
        if (binding is None or binding.snapshot.experiment_id != run.experiment_id
                or binding.snapshot.dataset_version_id != run.dataset_version_id
                or binding.variant.deleted or binding.variant.experiment_id != run.experiment_id
                or binding.output_column.deleted
                or binding.output_column.dataset_id != binding.snapshot.snapshot_dataset_id):
            raise RunWriteError("native binding is unavailable")
        validate_snapshot(binding.snapshot, dataset, binding.run.experiment)
        validate_run_references(binding, run, dataset, retired_ok=True)
        # Same rule as external runs: a historical pin may name a soft-deleted
        # evaluator (shown as retired); the scope and membership checks stay.
        metrics = {str(m.id): m for m in UserEvalMetric._base_manager.select_related("template").filter(
            Q(dataset_id=dataset.id) | Q(dataset_id=binding.snapshot.snapshot_dataset_id,
                                        source_id=str(run.experiment_id)),
            id__in=pins)}
        if set(metrics) != set(pins):
            raise RunWriteError("native evaluator membership is unavailable")
        return metrics
    except RunWriteError as exc:
        raise ResultsReadError("Native run provenance is unavailable", 404) from exc

def validate_evaluators(dataset, runs):
    if workspace_scope_is_empty():
        raise ResultsReadError("Evaluator not found on this dataset", 404)
    pairs, native_metrics, native_pins = [], {}, []
    for run in runs:
        if (run.deleted or run.dataset_version.deleted
                or run.dataset_version.dataset_id != dataset.id
                or run.dataset_version.organization_id != dataset.organization_id
                or run.dataset_version.workspace_id != dataset.workspace_id):
            raise ResultsReadError("Run not found on this dataset", 404)
        try:
            pins = _bindings(run)
        except RunWriteError as exc:
            raise ResultsReadError("Evaluator provenance is incomplete") from exc
        if run.task_kind != "external" and run.experiment_id is not None:
            native_metrics.update(native_evaluator_metrics(dataset, run, pins))
            native_pins.extend((key, pin["evaluator_version_id"]) for key, pin in pins.items())
        else:
            pairs.extend(pins.values())
    # A native pin is retired when its metric, template or pinned version was
    # soft-deleted, as for external pins below; its scope was checked above.
    native_versions = {str(v.id): v for v in EvalTemplateVersion._base_manager.filter(
        id__in={version for _, version in native_pins})} if native_pins else {}
    native_retired = set()
    for key, version in native_pins:
        pinned = native_versions.get(str(version))
        if (native_metrics[key].deleted or native_metrics[key].template.deleted
                or pinned is None or pinned.deleted):
            native_retired.add((key, version))
    metric_ids = {p["metric_id"] for p in pairs}
    # Historical pins may reference archived definitions. The base managers
    # avoid implicit soft-delete/workspace filters; explicit scope checks below
    # still apply, including legacy null scopes. Never resolve a replacement pin.
    metrics = {str(m.id): m for m in UserEvalMetric._base_manager.filter(
        id__in=metric_ids, dataset_id=dataset.id).select_related("template")}
    versions = {str(v.id): v for v in EvalTemplateVersion._base_manager.filter(
        id__in={p["evaluator_version_id"] for p in pairs})}
    retired_pairs = set(native_retired)
    for pair in pairs:
        m, v = metrics.get(pair["metric_id"]), versions.get(pair["evaluator_version_id"])
        if (m is None or v is None or v.eval_template_id != m.template_id
                or pair.get("template_id", str(m.template_id)) != str(m.template_id)):
            raise ResultsReadError("Evaluator not found on this dataset", 404)
        template = m.template
        system = template.owner == "system" and template.organization_id is None and template.workspace_id is None
        if (m.organization_id not in (None, dataset.organization_id) or
                m.workspace_id not in (None, dataset.workspace_id) or
                v.organization_id not in (None, dataset.organization_id) or
                v.workspace_id not in (None, dataset.workspace_id) or
                (not system and (template.organization_id != dataset.organization_id or
                                 template.workspace_id not in (None, dataset.workspace_id)))):
            raise ResultsReadError("Evaluator not found on this dataset", 404)
        if m.deleted or v.deleted or template.deleted:
            retired_pairs.add((pair["metric_id"], pair["evaluator_version_id"]))
    # Request-local display metadata only; no persisted state or execution grant.
    for run in runs:
        run._results_retired_evaluators = {key for key, pin in _bindings(run).items()
            if (key, pin["evaluator_version_id"]) in retired_pairs}
    return {**metrics, **native_metrics}

def pinned_baseline(dataset, requested=None):
    pin = get_baseline(dataset)
    pin_id = str(pin.run_id) if pin else None
    if pin_id:
        checked_runs(dataset, [pin_id])
    chosen = None if requested == "none" else requested or pin_id
    if chosen:
        checked_runs(dataset, [chosen])
    return str(chosen) if chosen else None, pin

def known_number(value):
    return value if type(value) in (int, float) and isfinite(value) else None

def settings_snapshot(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("trials", "concurrency", "max_concurrency"):
        n = value.get(key)
        if type(n) is int and n > 0:
            result[key] = n
    if value.get("error_policy") in ("zero", "exclude"):
        result["error_policy"] = value["error_policy"]
    reducer = value.get("trial_reducer")
    kinds = {"mean", "median", "mode", "max", "pass_at_k", "pass_hat_k", "at_least"}
    if isinstance(reducer, dict) and reducer.get("kind") in kinds:
        if reducer["kind"] != "at_least":
            result["trial_reducer"] = {"kind": reducer["kind"]}
        elif type(reducer.get("n")) is int and reducer["n"] > 0:
            result["trial_reducer"] = {"kind": "at_least", "n": reducer["n"]}
    elif reducer is None and "trial_reducer" in value:
        result["trial_reducer"] = None
    for key in ("limits", "row_limits"):
        if isinstance(value.get(key), dict):
            result[key] = {k: v for k, v in value[key].items()
                           if k in {"tokens", "messages", "cost_usd", "timeout_s", "max_cost_usd"}
                           and known_number(v) is not None and v >= 0}
    return result or None

def safe_config(run):
    """Allowlist public scalar settings; never pass through provider/tool config."""
    raw = run.model_config if isinstance(run.model_config, dict) else {}
    model = raw.get("model")
    public = {}
    if isinstance(model, str):
        public["model"] = {"name": model[:200]}
    elif isinstance(model, dict):
        public["model"] = {k: model[k][:200] for k in ("name", "provider")
                           if isinstance(model.get(k), str)}
    for k in ("temperature", "top_p", "max_tokens"):
        if known_number(raw.get(k)) is not None:
            public[k] = raw[k]
    if run.agent_version_id:
        public["agent"] = {"version_id": str(run.agent_version_id)}
    if run.prompt_version_id:
        public["prompt_version_id"] = str(run.prompt_version_id)
    return public or None

def metric_shape(pin):
    """Project only typed display metadata from the immutable evaluator pin."""
    config = pin.get("config_snapshot")
    external = config.get("external") if isinstance(config, dict) else None
    external = external if isinstance(external, dict) else {}
    raw = external.get("output_type") or pin.get("output_type_normalized")
    output_type = {"boolean": "pass_fail", "bool": "pass_fail", "binary": "pass_fail",
                   "pass_fail": "pass_fail", "percentage": "score", "numeric": "score",
                   "score": "score", "choice": "choice"}.get(raw)
    # A deterministic eval with a choice map is a choice eval: its cells read
    # the chosen label. Without a map it stays untyped, as before.
    choices = pin.get("choice_scores")
    if raw in ("deterministic", "choices") and isinstance(choices, dict) and choices:
        output_type = "choice"
    # The one direction rule of the grid, matrix and analysis (WD-REST3).
    higher = pinned_metric_direction(pin)
    return {"kind": "eval", "output_type": output_type, "higher_is_better": higher}

def pinned_choice_scores(pin, metric=None):
    """The {label: score} map a choice pin scored with, for labels only, or None.

    The pin's own map with the binding's choice-score edit laid over it, as
    the evaluator applies them. A native pin keeps only a digest of its
    binding config, so the live binding counts only while it is the one that
    ran; a pin with no binding record proves no map. A multi-choice score is
    the mean of the picked labels, so it never names one label. A label
    never gives a verdict or a direction.
    """
    from evaluations.engine.instance import choice_score_override

    scores = pin.get("choice_scores")
    if not isinstance(scores, dict) or not scores or "binding_config" not in pin:
        return None
    snapshot = pin.get("config_snapshot")
    if ((isinstance(snapshot, dict) and snapshot.get("multi_choice"))
            or getattr(getattr(metric, "template", None), "multi_choice", False)):
        return None
    binding = pin["binding_config"]
    if isinstance(binding, dict) and set(binding) == {"sha256"}:
        config = getattr(metric, "config", None)
        try:
            if metric is None or _digest(config) != binding["sha256"]:
                return None
        except RunWriteError:
            return None
    else:
        config = binding
    merged = {**scores, **(choice_score_override(config) or {})}
    if len(merged) > 100 or any(
            not isinstance(label, str) or not label.strip() or len(label) > 200
            or known_number(value) is None for label, value in merged.items()):
        return None
    return merged

def metric_definitions(runs):
    metrics = validate_evaluators(runs[0].dataset_version.dataset, runs) if runs else {}
    defs = {}
    for run in runs:
        for key, pin in _bindings(run).items():
            shape = metric_shape(pin)
            shape["choice_scores"] = (pinned_choice_scores(pin, metrics.get(key))
                                      if shape["output_type"] == "choice" else None)
            entry = defs.setdefault(key, {"key": key, "name": key, "runs": [],
                "versions": {}, "type": pin.get("output_type_normalized"), **shape,
                "pass_threshold": pin.get("pass_threshold"), "direction": shape["higher_is_better"]})
            # A catalog can contain different pins; never advertise the first pin's
            # type/direction/threshold/choice map as universal when they disagree.
            for field in ("output_type", "higher_is_better", "pass_threshold", "choice_scores"):
                value = pin.get(field) if field == "pass_threshold" else shape[field]
                if entry[field] != value:
                    entry[field] = None
            entry["direction"] = entry["higher_is_better"]
            entry["runs"].append(str(run.id))
            entry["versions"][str(run.id)] = pin["evaluator_version_id"]
    for key, entry in defs.items():
        entry["name"] = metrics[key].name
        entry["versions_differ"] = len(set(entry["versions"].values())) > 1
    return list(defs.values())

def native_projection(run):
    if run.task_kind == "external" or run.experiment_id is None:
        return {}
    try:
        from model_hub.services.native_run_projection import project_run as project_native_run
    except ModuleNotFoundError as exc:
        if exc.name != "model_hub.services.native_run_projection":
            raise
        raise ResultsReadError("Native run projection is unavailable; use classic results.", 503) from exc
    try:
        data = project_native_run(run, dataset=run.dataset_version.dataset)
    except RunWriteError as exc:
        raise ResultsReadError("Native run provenance is unavailable", 404) from exc
    if (not isinstance(data, dict) or str(data.get("run_id")) != str(run.id) or
            str(data.get("dataset_id")) != str(run.dataset_version.dataset_id)):
        raise ResultsReadError("Native projection does not match the authorized run")
    lineage = data.get("iteration") or {}
    if not isinstance(lineage, dict):
        raise ResultsReadError("Native lineage projection is invalid")
    refs = [value for value in (data.get("parent_run_id"), lineage.get("root_run_id")) if value]
    if refs:
        from uuid import UUID
        try:
            refs = [UUID(str(value)) for value in refs]
        except (ValueError, TypeError) as exc:
            raise ResultsReadError("Native lineage identity is invalid") from exc
        checked_runs(run.dataset_version.dataset, refs)
    return data

def project_run(run, *, include=(), counts=None, metrics=None, progress=None):
    meta = run.metadata if isinstance(run.metadata, dict) else {}
    experiment = run.experiment if run.experiment_id else None
    if experiment is not None and (
        experiment.deleted or experiment.dataset_id != run.dataset_version.dataset_id
    ):
        experiment = None
    retired = getattr(run, "_results_retired_evaluators", None)
    native = native_projection(run)
    effective = settings_snapshot(native.get("effective_settings"))
    requested = settings_snapshot(native.get("requested_settings"))
    try:
        plan = _planned(run)
        planned = len(plan)
        # Mirror _select_population's exact compatibility contract, not row
        # counts or mutable execution settings. Never expose plan identities.
        comparison_group = _digest({
            "dataset_version_id": str(run.dataset_version_id),
            "plan": [list(identity) for identity in sorted(plan)],
            "bindings": _bindings(run),
        })
        provenance = None
    except RunWriteError:
        planned, provenance = None, "Immutable run plan unavailable; open classic results."
        comparison_group = None
    if progress is None:
        from model_hub.services.results_grid import progress_by_run
        progress = progress_by_run([run])[str(run.id)]
    counts = dict(_counts_by_run([run.id])[run.id] if counts is None else counts)
    recorded = sum(counts.values())
    counts["not_started"] = max(planned - recorded, 0) if planned is not None else None
    result = {
        "run_id": str(run.id), "dataset_id": str(run.dataset_version.dataset_id),
        "experiment_id": str(run.experiment_id) if run.experiment_id else None,
        "experiment_name": experiment.name if experiment is not None else None,
        "experiment_description": experiment.description if experiment is not None else None,
        "dataset_version_id": str(run.dataset_version_id),
        "comparison_group": comparison_group,
        "dataset_version": {"id": str(run.dataset_version_id), "content_hash": run.dataset_version.content_hash},
        "ordinal": run.ordinal, "label": f"#{run.ordinal}" if run.ordinal else None,
        "name": meta.get("name") if isinstance(meta.get("name"), str) else None,
        "status": run.status, "task_kind": run.task_kind,
        "kind": "quick_test" if meta.get("kind") == "quick_test" else "run",
        "final": meta.get("non_final") is not True and meta.get("kind") != "quick_test",
        "listed": meta.get("kind") != "quick_test",
        "trials": (effective or {}).get("trials"),
        "concurrency": (effective or {}).get("concurrency", (effective or {}).get("max_concurrency")),
        "trial_reducer": (effective or {}).get("trial_reducer"),
        "error_policy": (effective or {}).get("error_policy"),
        "requested_settings": requested, "effective_settings": effective,
        "availability": {"settings": None if effective else "Persisted effective settings unavailable",
                         "provenance": provenance, "metrics": None if metrics is not None else "Not requested",
                         "evaluators": ("Evaluator availability not checked" if retired is None else
                             "Pinned evaluators retired; historical scores remain readable" if retired else None)},
        "counts": counts, "progress": progress,
        "metrics": metrics or {},
        "eval_versions": [{"metric_id": k, "evaluator_version_id": v["evaluator_version_id"],
                           "retired": k in retired if retired is not None else None}
                          for k, v in _bindings(run).items()],
        "started_at": run.started_at, "completed_at": run.completed_at, "created_at": run.created_at,
    }
    for key in ("source", "output_column_id", "prompt_config_id", "agent_config_id",
                "kind", "listed", "final", "label", "name", "splits", "sample",
                "trials", "concurrency", "trial_reducer", "error_policy"):
        if key in native:
            result[key] = native[key]
    if native.get("availability"):
        result["availability"].update(native["availability"])
    if "config" in include:
        result["config"] = native.get("config") if native else safe_config(run)
    if "lineage" in include:
        result.update(parent_run_id=native.get("parent_run_id"), iteration=native.get("iteration"),
                      parent_run_ids=native.get("parent_run_ids", []),
                      evaluation_sources=native.get("evaluation_sources", {}))
        if not native:
            result["availability"]["lineage"] = "No proven canonical lineage"
    return result

def catalog_metrics(runs):
    from model_hub.services.results_grid import assessed_scores
    items = bounded_list(ExperimentRunItem.no_workspace_objects.filter(
        run_id__in=[r.id for r in runs], deleted=False).only("run_id", "status", "score_refs", "error"),
        ("score_refs", "error"))
    bindings = {str(r.id): _bindings(r) for r in runs}
    values = {str(r.id): {} for r in runs}
    for item in items:
        if item.status != "completed" or item.error is not None:
            continue
        for key, score in assessed_scores(item.score_refs, bindings[str(item.run_id)]).items():
            if score["status"] == "assessed":
                values[str(item.run_id)].setdefault(key, []).append(score["value"])
    return {rid: {key: {"value": sum(v) / len(v), "n": len(v), "rule": "assessed_trial_mean"}
                  for key, v in groups.items()} for rid, groups in values.items()}

def list_results_runs(request, dataset_id, query):
    dataset = scoped_dataset(request, dataset_id)
    _, pin = pinned_baseline(dataset, query.get("baseline"))
    qs = live_runs(dataset)
    version_id = query.get("dataset_version_id", "all")
    if version_id != "all":
        if not DatasetVersion.no_workspace_objects.filter(
                id=version_id, dataset=dataset, deleted=False,
                organization_id=dataset.organization_id, workspace_id=dataset.workspace_id).exists():
            raise ResultsReadError("Dataset version not found", 404)
        qs = qs.filter(dataset_version_id=version_id)
    if query.get("experiment_id"):
        if not ExperimentsTable.no_workspace_objects.filter(
                id=query["experiment_id"], dataset=dataset, deleted=False).exists():
            raise ResultsReadError("Experiment not found", 404)
        qs = qs.filter(experiment_id=query["experiment_id"])
    if query.get("same_target_as"):
        target = checked_runs(dataset, [query["same_target_as"]])[0]
        if not (target.prompt_version_id or target.agent_version_id):
            raise ResultsReadError("Same-target identity is unavailable for this run")
        qs = qs.filter(task_kind=target.task_kind, prompt_version_id=target.prompt_version_id,
                       agent_version_id=target.agent_version_id)
    if query.get("status"):
        qs = qs.filter(status__in=query["status"])
    if query.get("search"):
        qs = qs.filter(
            Q(metadata__name__icontains=query["search"])
            | Q(experiment__name__icontains=query["search"])
        )
    include = query.get("include", [])
    if "quick_tests" not in include:
        qs = qs.filter(Q(metadata__kind__isnull=True) | ~Q(metadata__kind="quick_test"))
    count = qs.count()
    limit, offset = query.get("limit", 500), query.get("offset", 0)
    page_ids = list(qs.order_by(F("ordinal").desc(nulls_last=True), "-created_at", "-id")
                    .values_list("id", flat=True)[offset:offset + limit])
    runs = checked_runs(dataset, page_ids)
    from model_hub.services.results_grid import progress_by_run
    progress = progress_by_run(runs)
    counts = _counts_by_run([r.id for r in runs])
    metrics = catalog_metrics(runs) if "metrics" in include else {}
    baseline = baseline_payload(pin)
    baseline["run_id"] = str(pin.run_id) if pin else None
    more = offset + len(runs) < count
    return {"count": count, "results": [project_run(r, include=include,
                counts=counts[r.id], metrics=metrics.get(str(r.id)),
                progress=progress[str(r.id)]) for r in runs],
            "baseline": baseline, "pinned_run_id": baseline["run_id"],
            "baseline_run_id": baseline["run_id"], "metric_defs": metric_definitions(runs),
            "limit": limit, "offset": offset, "has_more": more,
            "next": offset + len(runs) if more else None,
            "scope": {"population": count, "complete": not more}}

def experiment_runs_detail(request, experiment, *, limit=500, offset=0):
    page = list_results_runs(request, experiment.dataset_id, {
        "experiment_id": experiment.id, "include": ["config", "lineage"],
        "limit": min(max(limit, 1), 500), "offset": max(offset, 0)})
    return {"runs": page["results"], "runs_total": page["count"],
            "runs_has_more": page["has_more"], "runs_next": page["next"]}

def detail_results_projection(request, experiment):
    """Keep GET errors; admitted mutations get null optional Results, not failure."""
    from tfc.routers import force_primary
    if request is None:
        raise ResultsReadError("Authenticated Results request context is required", 503)
    try:
        with force_primary():
            return {**experiment_runs_detail(request, experiment),
                    "runs_availability": {"available": True, "status": 200, "reason": None}}
    except ResultsReadError as exc:
        if request.method not in {"POST", "PUT", "PATCH"}:
            raise
        return {"runs": None, "runs_total": None, "runs_has_more": None, "runs_next": None,
                "runs_availability": {"available": False, "status": exc.status, "reason": str(exc)}}
