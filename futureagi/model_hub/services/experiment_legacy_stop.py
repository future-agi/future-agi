"""Keep whole-experiment cancellation available for pre-control executions."""

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from model_hub.models.choices import StatusType
from model_hub.models.experiments import ExperimentsTable
from model_hub.services.experiment_run_control import selected_runs


def legacy_stop_response(request, experiment_id):
    if "run_ids" in request.validated_data:
        return None
    # The classic action sends no selection and retains cancel-and-cleanup.
    # Only explicit run selections opt into cooperative, resumable control.
    experiment_id, runs = selected_runs(request, experiment_id)
    experiment = ExperimentsTable.objects.get(id=experiment_id, deleted=False)
    # rerun_cancelled is a count of cancelled rerun workflows, as the helper returns.
    cancelled = {"main_cancelled": False, "rerun_cancelled": 0}
    # A cooperative pause projects Cancelled while its parent is still open.
    # The legacy action must cancel that parent, including its base evaluations.
    paused = experiment.status == StatusType.CANCELLED.value and any(
        run["binding"] is not None and run["binding"].phase == "STOPPED" for run in runs
    )
    if paused or experiment.status in (
        StatusType.RUNNING.value,
        StatusType.QUEUED.value,
    ):
        from tfc.temporal.experiments import cancel_all_experiment_workflows

        cancelled = cancel_all_experiment_workflows(experiment_id)
        ExperimentsTable.objects.filter(
            id=experiment_id, status=experiment.status
        ).update(status=StatusType.CANCELLED.value)
    elif experiment.status != StatusType.CANCELLED.value:
        raise ValidationError(
            f"Experiment is not running. Current status: {experiment.status}"
        )
    return Response(
        {
            "status": True,
            "result": {
                "message": "Experiment stopped.",
                "experiment_id": experiment_id,
                "workflows_cancelled": {
                    "main": cancelled["main_cancelled"],
                    "reruns": cancelled["rerun_cancelled"],
                },
            },
        }
    )
