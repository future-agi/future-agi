"""Tenant-scoped run selection and immutable re-score children."""
import copy

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.experiment_run_writes import _RUN_KEY, _digest, _envelope

SETTLED = ("completed", "partial", "failed", "cancelled")


def scoped_runs(org_id, workspace_id):
    return ExperimentRun.no_workspace_objects.filter(
        deleted=False, dataset_version__deleted=False,
        dataset_version__dataset__deleted=False,
        dataset_version__dataset__organization_id=org_id,
        dataset_version__dataset__workspace_id=workspace_id,
    ).select_related("dataset_version__dataset", "experiment")


def selected_run(org_id, workspace_id, run_id, row_ids=None, *, count_only=False):
    run = scoped_runs(org_id, workspace_id).filter(id=run_id).first()
    if run is None:
        raise NotFound("Run not found.")
    if run.status not in SETTLED:
        raise ValidationError("Choose a settled run.")
    queryset = ExperimentRunItem.no_workspace_objects.filter(run=run, deleted=False).order_by("id")
    wanted = None
    if row_ids is not None:
        wanted = {str(row) for row in row_ids}
        if not wanted or len(wanted) > 1000:
            raise ValidationError("Choose between 1 and 1000 run rows.")
        queryset = queryset.filter(example_id__in=wanted)
    # Count item IDs rather than distinct examples: repeated trials are calls too.
    # The count endpoint never fetches input/output/metadata JSON.
    if count_only:
        items = list(queryset.values_list("example_id", flat=True)[:1001])
    else:
        items = list(queryset[:1001])
    if not items or len(items) > 1000:
        raise ValidationError("Choose between 1 and 1000 run rows.")
    found = {str(item if count_only else item.example_id) for item in items}
    if wanted is not None and not wanted <= found:
        raise ValidationError("Some selected rows do not belong to this run.")
    return run, len(items) if count_only else items


def run_label(run):
    return f"Run #{run.ordinal}" if run.ordinal else f"Run {str(run.id)[:8]}"


def options(org_id, workspace_id):
    grouped = {}
    for run in scoped_runs(org_id, workspace_id).filter(status__in=SETTLED).order_by("-created_at")[:100]:
        dataset = run.dataset_version.dataset
        group = grouped.setdefault(str(dataset.id), {
            "dataset_id": str(dataset.id), "dataset_name": dataset.name, "runs": [],
        })
        rows = list(ExperimentRunItem.no_workspace_objects.filter(run=run, deleted=False).values_list("example_id", flat=True).distinct()[:1001])
        if not rows or len(rows) > 1000:
            continue
        group["runs"].append({"run_id": str(run.id), "label": run_label(run),
                             "experiment_name": run.experiment.name if run.experiment else "",
                             "rows": [{"id": str(row), "label": f"Row {index + 1}"} for index, row in enumerate(rows)]})
    return list(grouped.values())


def persist_child(job, results):
    """Publish a complete child only after calls finish; never modify the parent."""
    template = job.template
    with transaction.atomic():
        source = scoped_runs(template.organization_id, template.workspace_id).select_for_update(of=("self",)).filter(id=job.run_id).first()
        if source is None or source.status not in SETTLED:
            raise NotFound("The source run is no longer available.")
        version_id = job.definition["version_id"]
        dataset = Dataset.no_workspace_objects.select_for_update().get(id=source.dataset_version.dataset_id)
        bindings = UserEvalMetric.no_workspace_objects.filter(
            dataset=dataset, template=template, organization=template.organization, workspace=template.workspace,
        )
        metric = bindings.first() or UserEvalMetric.no_workspace_objects.create(
            dataset=dataset, template=template, name=template.name,
            organization=template.organization, workspace=template.workspace,
            show_in_sidebar=False, model=None,
        )
        metric_id = str(metric.id)
        pins = [pin for pin in source.evaluator_versions if pin.get("metric_id") != metric_id]
        pins.append({"metric_id": metric_id, "evaluator_version_id": version_id,
                     "template_id": str(template.id), "binding_config": {"mapping": job.definition["remote"].get("mapping", {})}})
        child = ExperimentRun.no_workspace_objects.create(
            dataset_version=source.dataset_version, experiment=source.experiment,
            task_kind=source.task_kind, status=source.status,
            started_at=timezone.now(), completed_at=timezone.now(),
            model_config=source.model_config, prompt_version_id=source.prompt_version_id,
            agent_version_id=source.agent_version_id,
            evaluator_versions=pins,
            metadata={**source.metadata, "parent_run_id": str(source.id), "remote_job_id": str(job.id)},
        )
        by_item = {result["item_id"]: result for result in results}
        for item in ExperimentRunItem.no_workspace_objects.filter(run=source, deleted=False):
            # A child has one pinned version per metric. Unselected rows keep
            # that score only if it already belongs to this exact version.
            refs = [copy.deepcopy(ref) for ref in item.score_refs
                    if ref.get("metric_id") != metric_id or ref.get("evaluator_version_id") == version_id]
            result = by_item.get(str(item.id))
            if result:
                verdict = result["verdict"]
                refs = [ref for ref in refs if ref.get("metric_id") != metric_id]
                refs.append({"metric_id": metric_id, "evaluator_version_id": version_id,
                             "status": "error" if "error" in verdict else "assessed", **verdict})
            fields = {name: getattr(item, name) for name in (
                "example_id", "trial_number", "status", "input", "expected", "output", "error",
                "trace_id", "root_span_id", "latency_ms", "token_usage", "cost", "metadata",
            )}
            metadata = _envelope(copy.deepcopy(item.metadata))
            payload = {name: fields[name] for name in (
                "status", "output", "error", "trace_id", "root_span_id", "latency_ms", "token_usage", "cost",
            )}
            metadata[_RUN_KEY]["result_receipt"] = {
                "receipt_key": f"remote:{job.id}:{item.id}",
                "payload_hash": _digest({**payload, "score_refs": refs}),
            }
            fields["metadata"] = metadata
            ExperimentRunItem.no_workspace_objects.create(run=child, score_refs=refs, **fields)
        failed = sum("error" in row["verdict"] for row in results)
        return {"child_run_id": str(child.id), "child_label": run_label(child),
                "from_label": run_label(source), "dataset_id": str(source.dataset_version.dataset_id),
                "experiment_id": str(source.experiment_id) if source.experiment_id else None,
                "scored": len(results) - failed, "failed": failed, "new_eval": True,
                "unit": "row", "moved_cells": len(results) - failed,
                "rows": results, "error": next((row["verdict"]["error"] for row in results if "error" in row["verdict"]), None)}
