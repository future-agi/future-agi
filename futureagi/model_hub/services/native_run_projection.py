"""Read-only canonical headers shared by detail, R1, R3 and analysis.

Never expose the raw model/evaluator/creation JSON: it can contain messages,
headers or tool credentials. Unknown history is not filled with form defaults.
"""
import math
from uuid import UUID

from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services.experiment_run_read import item_counts, plan_identities
from model_hub.services.experiment_run_writes import RunWriteError


PARAMETERS = frozenset({
    "temperature", "top_p", "topP", "max_tokens", "maxTokens",
    "max_output_tokens", "frequency_penalty", "presence_penalty", "seed",
})


def _text(value):
    return value if isinstance(value, str) and len(value) <= 255 else None


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def sanitize_config(value):
    """Allowlisted display projection, not a recursively redacted raw config."""
    value = value if isinstance(value, dict) else {}
    result = {}
    model = value.get("model")
    if isinstance(model, str):
        model = {"name": model}
    if isinstance(model, dict):
        clean = {key: model[key] for key in ("name", "provider", "version_id") if _text(model.get(key)) is not None}
        params = model.get("params", {})
        if isinstance(params, dict):
            clean["params"] = {key: item for key, item in params.items() if key in PARAMETERS and _number(item)}
        result["model"] = clean
    agent = value.get("agent")
    if isinstance(agent, dict):
        result["agent"] = {key: agent[key] for key in ("id", "version_id", "name") if _text(agent.get(key)) is not None}
    tools = value.get("tools")
    if isinstance(tools, list):
        # No URLs, arguments, function bodies, headers or configuration blobs.
        result["tools"] = [{key: tool[key] for key in ("name", "type") if _text(tool.get(key)) is not None}
                           for tool in tools[:100] if isinstance(tool, dict)]
    return result


def _eval_pins(value):
    pins = []
    for entry in value if isinstance(value, list) else []:
        if not isinstance(entry, dict):
            continue
        pin = {}
        for key in ("metric_id", "evaluator_version_id", "template_id"):
            try:
                pin[key] = str(UUID(str(entry[key])))
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
        for key in ("version_number", "pass_threshold"):
            if _number(entry.get(key)):
                pin[key] = entry[key]
        if _text(entry.get("output_type_normalized")) is not None:
            pin["output_type_normalized"] = entry["output_type_normalized"]
        pins.append(pin)
    return pins


def unbound_experiment(experiment):
    """Explicit classic-only status; NOT a fabricated canonical run."""
    return {
        "comparable": False, "reason": "native_identity_not_recorded",
        "run_id": None,
        "classic_url": f"/dashboard/develop/experiment/{experiment.id}/data",
    }


def project_run(run, *, dataset):
    """Dataset must already be request-scoped. A foreign/deleted run is an error.

    P03 adds catalog pagination and assessed metrics; this function does not
    turn a metric absence into zero or assert pairwise statistical equivalence.
    """
    from model_hub.services.native_run_binding import live_dataset, validate_snapshot, validate_run_references

    dataset = live_dataset(dataset)
    run = ExperimentRun.no_workspace_objects.select_related("dataset_version", "experiment").filter(
        pk=run.pk, dataset_version__dataset=dataset, dataset_version__deleted=False,
        deleted=False,
    ).first()
    if run is None:
        raise RunWriteError("run was not found in the authorized dataset")
    version = run.dataset_version
    if (version.organization_id != dataset.organization_id or version.workspace_id != dataset.workspace_id
            or (run.experiment_id and (run.experiment.deleted or run.experiment.dataset_id != dataset.id))):
        raise RunWriteError("run provenance is not in the authorized dataset")
    binding = NativeRunBinding.objects.select_related(
        "snapshot__dataset_version", "snapshot__snapshot_dataset", "variant", "output_column",
    ).filter(run=run).first()
    if binding:
        if (run.experiment_id is None or binding.snapshot.experiment_id != run.experiment_id
                or binding.snapshot.dataset_version_id != run.dataset_version_id):
            raise RunWriteError("native experiment identity is inconsistent")
        validate_snapshot(binding.snapshot, dataset, run.experiment)
        if (binding.variant.deleted or binding.variant.experiment_id != run.experiment_id
                or binding.output_column.deleted
                or binding.output_column.dataset_id != binding.snapshot.snapshot_dataset_id):
            raise RunWriteError("native variant is unavailable")
        validate_run_references(binding, run, dataset, retired_ok=True)
    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    effective = binding.effective_settings if binding else {}
    requested = binding.requested_settings if binding else {}
    plan = plan_identities(run)
    examples = {item.get("example_id") for item in plan}
    known_examples = {str(value) for value in version.items.filter(deleted=False).values_list("example_id", flat=True)}
    if examples - known_examples:
        raise RunWriteError("run plan contains an unavailable version example")
    counts = item_counts(run)
    items = list(ExperimentRunItem.no_workspace_objects.filter(run=run, deleted=False).values("cost", "latency_ms"))
    complete_cost = bool(plan) and len(items) == len(plan) and all(item["cost"] is not None for item in items)
    complete_latency = bool(plan) and len(items) == len(plan) and all(item["latency_ms"] is not None for item in items)
    native_proven = binding is not None
    external_proven = run.task_kind == "external" and bool(plan)
    provenance_complete = bool(plan) and (native_proven or external_proven)
    label = f"#{run.ordinal}" if run.ordinal else None
    identity = metadata.get("native_identity", {}) if binding else {}
    evaluation_sources = identity.get("evaluation_sources", {})
    parents = sorted({ref["run_id"] for ref in evaluation_sources.values()})
    if evaluation_sources:
        from model_hub.services.native_run_binding import native_output_sources, snapshot_examples
        links = [link for link in snapshot_examples(binding.snapshot)
                 if str(link.snapshot_row_id) in evaluation_sources]
        native_output_sources(binding.snapshot, binding.variant, binding.output_column, links, pinned=evaluation_sources)
    return {
        "run_id": str(run.id), "dataset_id": str(dataset.id),
        "experiment_id": str(run.experiment_id) if run.experiment_id else None,
        "task_kind": run.task_kind, "label": label,
        "name": _text(metadata.get("name")) or label, "status": run.status,
        "ordinal": run.ordinal,
        "dataset_version": {"id": str(version.id), "content_hash": version.content_hash},
        "dataset_version_id": str(version.id),
        "source": "native" if binding else "external" if external_proven else "unknown",
        "native_variant_id": str(binding.variant_id) if binding else None,
        "native_config_id": str(binding.native_config_id) if binding else None,
        "output_column_id": str(binding.output_column_id) if binding else None,
        "execution_id": str(binding.execution_id) if binding else None,
        "prompt_version_id": str(run.prompt_version_id) if run.prompt_version_id else None,
        "agent_version_id": str(run.agent_version_id) if run.agent_version_id else None,
        "trials": effective.get("trials"), "concurrency": effective.get("concurrency"),
        "trial_reducer": None, "error_policy": None,
        "requested_settings": dict(requested), "effective_settings": dict(effective),
        "config": sanitize_config(binding.config if binding else run.model_config),
        "eval_versions": _eval_pins(run.evaluator_versions),
        "counts": counts, "examples_total": len(examples), "attempts": len(plan),
        "progress": {"completed": counts["completed"] + counts["failed"] + counts["skipped"], "total": len(plan)},
        "cost_usd": sum(item["cost"] for item in items) if complete_cost else None,
        "latency_ms": sum(item["latency_ms"] for item in items) / len(items) if complete_latency else None,
        "parent_run_id": parents[0] if len(parents) == 1 else None,
        "parent_run_ids": parents, "iteration": None,
        "kind": "evaluation" if identity.get("execution_mode") == "eval_only" else "execution",
        "evaluation_sources": {row: {key: ref[key] for key in ("run_id", "item_id", "status")}
                               for row, ref in evaluation_sources.items()},
        "listed": True, "non_final": metadata.get("non_final") is True,
        "comparable": provenance_complete,
        "availability": {
            "provenance": None if provenance_complete else "native_identity_not_recorded" if run.task_kind != "external" else "plan_not_recorded",
            "settings": None if binding else "not_recorded",
            "trial_reducer": "not_recorded", "error_policy": "not_recorded",
            "lineage": None if parents else "not_recorded", "metrics": "provided_by_results_analysis",
            "cost_usd": None if complete_cost else "not_recorded_for_every_attempt",
            "latency_ms": None if complete_latency else "not_recorded_for_every_attempt",
        },
        "classic_url": f"/dashboard/develop/experiment/{run.experiment_id}/data" if run.experiment_id else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }
