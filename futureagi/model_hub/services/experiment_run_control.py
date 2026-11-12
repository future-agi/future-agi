"""Tenant-scoped run selection and exact-execution Temporal commands."""

from datetime import timedelta

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import (
    APIException,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from temporalio.client import WorkflowExecutionStatus

from model_hub.models.experiment_run_control import ExperimentRunControl
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentRun,
    ExperimentsTable,
)
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services import experiment_target_admission as target_admission
from tfc.temporal.common.client import _run_async_in_sync_context, get_client
from tfc.utils.tenant_scope import caller_workspace, scope_to_caller


class RunControlUnavailable(APIException):
    status_code = 409
    default_detail = "This run cannot be controlled. Refresh its status and try again."


def _latest_bindings(variant_ids):
    """A variant's current execution is its newest control binding."""
    bindings = ExperimentRunControl.objects.filter(
        experiment_dataset_id__in=variant_ids
    ).order_by("-created_at")
    latest = {}
    for binding in bindings:
        latest.setdefault(binding.experiment_dataset_id, binding)
    return latest


def _variant_entry(variant, latest):
    return {
        "run_id": str(variant.id),
        "variant_id": str(variant.id),
        "name": variant.name,
        "binding": latest.get(variant.id),
    }


def _run_name(run):
    meta = run.metadata if isinstance(run.metadata, dict) else {}
    name = meta.get("name")
    if isinstance(name, str) and name:
        return name
    return f"#{run.ordinal}" if run.ordinal else "Run"


def _named_runs(experiment, run_ids):
    """Resolve the results catalog's run IDs (ExperimentRun) in this experiment.

    A run reaches a variant only through its native binding, and that variant
    must pass the same membership filter as a variant named directly. The run
    owns the variant's current execution only while it is the variant's newest
    run, the execution was bound after the run was created, and it executes the
    run's own config. Otherwise the run is returned without an execution, so a
    command on it is refused and never reaches another run's workflow.
    """
    runs = list(ExperimentRun.objects.filter(id__in=run_ids, experiment=experiment))
    links = {
        link.run_id: link
        for link in NativeRunBinding.objects.filter(run_id__in=[run.id for run in runs])
    }
    # The same variant filter as a variant named directly.
    variants = {
        variant.id: variant
        for variant in ExperimentDatasetTable.objects.filter(
            experiment=experiment,
            deleted=False,
            id__in=[link.variant_id for link in links.values()],
        )
    }
    selected = []
    for run in runs:
        link = links.get(run.id)
        if link is not None and link.variant_id not in variants:
            continue  # Refused below exactly like an unknown ID.
        selected.append((run, link))
    variant_ids = {link.variant_id for _, link in selected if link is not None}
    latest = _latest_bindings(list(variant_ids))
    # Deleted runs count: a newer run owns every later execution of its variant.
    siblings = {}
    for variant_id, other_id, created_at in NativeRunBinding.objects.filter(
        variant_id__in=list(variant_ids)
    ).values_list("variant_id", "run_id", "run__created_at"):
        siblings.setdefault(variant_id, []).append((other_id, created_at))
    entries = {}
    for run, link in selected:
        binding = None
        if link is not None:
            superseded = any(
                other_id != run.id and created_at >= run.created_at
                for other_id, created_at in siblings.get(link.variant_id, ())
            )
            current = latest.get(link.variant_id)
            if (
                not superseded
                and current is not None
                and current.config_id == link.native_config_id
                and current.created_at >= run.created_at
            ):
                binding = current
        entries[str(run.id)] = {
            "run_id": str(run.id),
            "variant_id": str(link.variant_id) if link is not None else None,
            "name": (
                variants[link.variant_id].name if link is not None else _run_name(run)
            ),
            "binding": binding,
        }
    return entries


def selected_runs(request, experiment_id, run_ids=None):
    """Select variants by variant ID or by the results catalog's run ID.

    Every entry keeps the ID the caller sent as ``run_id`` and names the
    variant it controls as ``variant_id`` (None when the run has no variant).
    """
    if caller_workspace(request) is None:
        raise PermissionDenied("A workspace is required.")
    experiment = get_object_or_404(
        scope_to_caller(
            ExperimentsTable.objects.filter(deleted=False, dataset__deleted=False),
            request,
            organization_field="dataset__organization",
            workspace_field="dataset__workspace",
        ),
        id=experiment_id,
    )
    variants = ExperimentDatasetTable.objects.filter(
        experiment=experiment, deleted=False
    )
    if run_ids is None:
        variants = list(variants.order_by("id"))
        latest = _latest_bindings([variant.id for variant in variants])
        return str(experiment.id), [
            _variant_entry(variant, latest) for variant in variants
        ]
    if not run_ids:
        raise ValidationError("Select at least one run.")
    wanted = {str(x) for x in run_ids}
    variants = list(variants.filter(id__in=wanted).order_by("id"))
    latest = _latest_bindings([variant.id for variant in variants])
    entries = {str(variant.id): _variant_entry(variant, latest) for variant in variants}
    if wanted - set(entries):
        entries.update(_named_runs(experiment, wanted - set(entries)))
    if set(entries) != wanted:
        # Validate the entire selection before any Temporal traffic or mutation.
        raise NotFound("Run not found.")
    return str(experiment.id), [entries[key] for key in sorted(entries)]


async def _execute(experiment_id, runs, stopped):
    client = await get_client()
    prepared = []
    for run in runs:
        binding = run["binding"]
        state = {"run_id": run["run_id"], "name": run["name"], "status": "UNAVAILABLE"}
        handle = None
        if binding is not None:
            handle = client.get_workflow_handle(
                binding.workflow_id, run_id=str(binding.workflow_run_id)
            )
            description = await handle.describe(rpc_timeout=timedelta(seconds=10))
            if description.status == WorkflowExecutionStatus.RUNNING:
                control = await handle.query(
                    "run_control", rpc_timeout=timedelta(seconds=10)
                )
                if (
                    control.get("experiment_id") != experiment_id
                    or control.get("config_id") != str(binding.config_id)
                    or control.get("workflow_run_id") != str(binding.workflow_run_id)
                ):
                    raise RunControlUnavailable()
                state["status"] = control["status"]
            else:
                state["status"] = description.status.name
                handle = None
        if stopped is not None and state["status"] not in (
            "RUNNING",
            "STOPPING",
            "STOPPED",
            "COMPLETED",
        ):
            raise RunControlUnavailable()
        prepared.append((state, handle))
    # All selected runs have passed authorization and capability checks.
    for state, handle in prepared:
        if (
            stopped is not None
            and handle is not None
            and state["status"] != "COMPLETED"
        ):
            await handle.signal(
                "set_run_stopped", stopped, rpc_timeout=timedelta(seconds=10)
            )
            state["status"] = (
                await handle.query("run_control", rpc_timeout=timedelta(seconds=10))
            )["status"]
    return {"experiment_id": experiment_id, "runs": [state for state, _ in prepared]}


def run_control(request, experiment_id, run_ids=None, *, stopped=None):
    experiment_id, runs = selected_runs(request, experiment_id, run_ids)
    if stopped is False:
        # Resume rechecks the selected targets; stop and reads stay open.
        # Admission checks variants, whichever ID named them.
        target_admission.ensure_runs_resumable(
            [
                {**run, "run_id": run["variant_id"]}
                for run in runs
                if run["variant_id"] is not None
            ],
            experiment_id=experiment_id,
            request=request,
        )
    return _run_async_in_sync_context(lambda: _execute(experiment_id, runs, stopped))
