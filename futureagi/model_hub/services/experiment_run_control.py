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
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.services import experiment_target_admission as target_admission
from tfc.temporal.common.client import _run_async_in_sync_context, get_client
from tfc.utils.tenant_scope import caller_workspace, scope_to_caller


class RunControlUnavailable(APIException):
    status_code = 409
    default_detail = "This run cannot be controlled. Refresh its status and try again."


def selected_runs(request, experiment_id, run_ids=None):
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
    runs = ExperimentDatasetTable.objects.filter(experiment=experiment, deleted=False)
    if run_ids is not None:
        if not run_ids:
            raise ValidationError("Select at least one run.")
        runs = runs.filter(id__in=run_ids)
    runs = list(runs.order_by("id"))
    if run_ids is not None and {str(run.id) for run in runs} != {
        str(x) for x in run_ids
    }:
        # Validate the entire selection before any Temporal traffic or mutation.
        raise NotFound("Run not found.")
    bindings = ExperimentRunControl.objects.filter(
        experiment_dataset_id__in=[run.id for run in runs]
    ).order_by("-created_at")
    latest = {}
    for binding in bindings:
        latest.setdefault(binding.experiment_dataset_id, binding)
    return str(experiment.id), [
        {"run_id": str(run.id), "name": run.name, "binding": latest.get(run.id)}
        for run in runs
    ]


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
        target_admission.ensure_runs_resumable(
            runs, experiment_id=experiment_id, request=request
        )
    return _run_async_in_sync_context(lambda: _execute(experiment_id, runs, stopped))
