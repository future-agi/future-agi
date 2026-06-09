import uuid

from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.experiments import ExperimentsTable


def is_experiment_cancelled(experiment_id: uuid.UUID) -> bool:
    """Check if an experiment has been cancelled.

    Used as a guard before writing cell results, to prevent in-flight
    activities from overwriting the cleanup done by stop_experiment_cleanup_activity.
    """
    try:
        status = (
            ExperimentsTable.objects.filter(id=experiment_id, deleted=False)
            .values_list("status", flat=True)
            .first()
        )
        return status == StatusType.CANCELLED.value
    except Exception:
        return False


def maybe_complete_experiment_after_eval_stop(experiment_id) -> bool:
    """If the stopped eval was the last running one, flip experiment to COMPLETED.

    StopUserEvalView flips the individual UserEvalMetric to ERROR on a
    per-eval stop. The experiment itself stays RUNNING until either all
    cells finish or the user presses the whole-experiment Stop. When a
    single eval is the only thing keeping the experiment in RUNNING,
    flipping it stopped should move the experiment to COMPLETED so the
    UI reflects reality.

    Returns True if the experiment status was updated.
    """
    if not experiment_id:
        return False
    try:
        exp_uuid = (
            experiment_id
            if isinstance(experiment_id, uuid.UUID)
            else uuid.UUID(str(experiment_id))
        )
        experiment = ExperimentsTable.objects.filter(
            id=exp_uuid, deleted=False
        ).first()
        if not experiment or experiment.status != StatusType.RUNNING.value:
            return False

        still_running = UserEvalMetric.objects.filter(
            source_id=str(experiment.id),
            deleted=False,
            status__in=[
                StatusType.RUNNING.value,
                StatusType.NOT_STARTED.value,
                StatusType.EXPERIMENT_EVALUATION.value,
            ],
        ).exists()
        if still_running:
            return False

        experiment.status = StatusType.COMPLETED.value
        experiment.save(update_fields=["status", "updated_at"])
        return True
    except Exception:
        return False


def is_user_eval_stopped(user_eval_metric_id) -> bool:
    """Check if a UserEvalMetric has been stopped or deleted.

    StopUserEvalView sets status=ERROR and marks the current running
    cells with a "User stopped evaluation" reason. DeleteEvalsView sets
    deleted=True. This guard lets the Temporal activities and sync
    runners skip cell writes that would otherwise clobber the stop/delete
    marker once the in-flight worker finishes.
    """
    if not user_eval_metric_id:
        return False
    try:
        result = (
            UserEvalMetric.all_objects.filter(id=user_eval_metric_id)
            .values_list("status", "deleted")
            .first()
        )
        if result is None:
            return False
        status, deleted = result
        return deleted or status in (
            StatusType.CANCELLED.value,
            StatusType.ERROR.value,
        )
    except Exception:
        return False
