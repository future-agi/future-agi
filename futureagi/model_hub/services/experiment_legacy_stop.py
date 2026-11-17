"""Keep whole-experiment cancellation available for pre-control executions."""

import structlog
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from model_hub.models.choices import StatusType
from model_hub.models.experiments import ExperimentsTable
from model_hub.services.experiment_run_control import selected_runs

logger = structlog.get_logger(__name__)


def _cancel_confirmed(cancelled):
    """The service answered for every workflow of the experiment: each is now
    cancelled, already ended or absent. Older reports without the flags are
    not confirmed."""
    return bool(
        cancelled.get("main_confirmed", False)
        and cancelled.get("reruns_confirmed", False)
    )


def _end_runs_now(experiment_id):
    """Give every unfinished run its terminal seal now, with the cleanup's
    own step.

    The cancelled workflows' cleanup does this from inside the cancelled
    workflow (workflows.py, the CancelledError handler), which swallows its
    own failure, so a failed cleanup left the runs unfinished and the
    experiment refused edits and reruns for good (ensure_native_idle). The
    same fenced step (_cleanup_native_execution_sync, stopped=True: it ends the
    current admission's native runs, settling graph runs first, and marks the
    experiment's running cells stopped) runs here once every workflow is known
    cancelled; the workflow's cleanup then finds the runs ended and changes
    nothing. If this attempt fails, the workflow's cleanup still runs, as
    before.
    """
    from model_hub.services.experiment_submit import state_of
    from tfc.temporal.experiments.activities import _cleanup_native_execution_sync

    experiment = ExperimentsTable.no_workspace_objects.get(id=experiment_id)
    state = state_of(experiment)
    contexts = (state.get("workflow_args") or {}).get("native_runs") or {}
    if state.get("state") != "accepted" or not contexts:
        return
    try:
        _cleanup_native_execution_sync(str(experiment_id), contexts, stopped=True)
    except Exception:
        logger.warning(
            "Stop could not end the runs now; the workflow cleanup will",
            experiment_id=str(experiment_id),
            exc_info=True,
        )


def _rows_in_flight(experiment_id):
    """Rows whose call was sent and whose result is not in yet. They finish,
    and their results are saved and counted."""
    from model_hub.models.experiments import ExperimentRunItem

    return (
        ExperimentRunItem.no_workspace_objects.filter(
            run__experiment_id=experiment_id, run__deleted=False, deleted=False
        )
        .exclude(status__in=("completed", "failed", "skipped"))
        .count()
    )


def _has_unfinished_runs(experiment):
    """Runs of this experiment not yet ended: what ensure_native_idle refuses
    edits and reruns over."""
    from model_hub.models.native_experiment_runs import NativeRunBinding

    return NativeRunBinding.objects.filter(
        snapshot__experiment=experiment,
        run__deleted=False,
        run__completed_at__isnull=True,
    ).exists()


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

        def write_cancelled():
            # Also when a workflow's first step turned it Queued -> Running
            # since it was read: the Stop wins. A run that finished meanwhile
            # keeps its status.
            ExperimentsTable.objects.filter(
                id=experiment_id,
                status__in={
                    experiment.status,
                    StatusType.RUNNING.value,
                    StatusType.QUEUED.value,
                },
            ).update(status=StatusType.CANCELLED.value)
            from model_hub.services.http_run_recording import stop_http_runs

            stop_http_runs(experiment_id)

        # Cancelled first: a workflow reached by the cancel below (its
        # settlement of a row in flight, a row about to be sent) already reads
        # the experiment stopped. Then again after, over a first step that
        # turned it Running meanwhile.
        write_cancelled()
        cancelled = cancel_all_experiment_workflows(experiment_id)
        write_cancelled()
        if _cancel_confirmed(cancelled):
            _end_runs_now(experiment_id)
    elif experiment.status != StatusType.CANCELLED.value:
        raise ValidationError(
            f"Experiment is not running. Current status: {experiment.status}"
        )
    elif _has_unfinished_runs(experiment):
        from model_hub.services.http_run_recording import stop_http_runs

        stop_http_runs(experiment_id)
        # A repeat Stop repairs a stop that could not end its runs (it could
        # not confirm every workflow stopped, and the cancelled workflow's own
        # cleanup failed too): edits and reruns stay refused until they end.
        # It asks again and, under the same rule, ends them.
        from tfc.temporal.experiments import cancel_all_experiment_workflows

        cancelled = cancel_all_experiment_workflows(experiment_id)
        if _cancel_confirmed(cancelled):
            _end_runs_now(experiment_id)
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
                # The page says no count is final while any are in flight.
                "rows_in_flight": _rows_in_flight(experiment_id),
            },
        }
    )
