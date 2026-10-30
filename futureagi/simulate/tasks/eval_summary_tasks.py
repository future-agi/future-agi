from datetime import datetime, timedelta

import structlog
from django.utils import timezone

from simulate.models import TestExecution
from simulate.models.test_execution import EvalExplanationSummaryStatus
from simulate.utils.eval_explaination_summary import _get_cluster_dict_by_eval
from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)

EVAL_SUMMARY_TIME_LIMIT = 1800
# A summary attempt still pending or running this long after it was queued or
# started has outlived the activity's time limit: its worker is gone.
EVAL_SUMMARY_STALE_AFTER = timedelta(seconds=EVAL_SUMMARY_TIME_LIMIT + 300)
EVAL_SUMMARY_IN_FLIGHT = (
    EvalExplanationSummaryStatus.PENDING,
    EvalExplanationSummaryStatus.RUNNING,
)


def eval_summary_in_flight(test_execution):
    """Whether a live attempt is building the run's summary.

    The summary's last-updated time is when its current attempt was queued or
    started, and an attempt older than EVAL_SUMMARY_STALE_AFTER is stale.
    """
    marked_at = test_execution.eval_explanation_summary_last_updated
    return (
        test_execution.eval_explanation_summary_status in EVAL_SUMMARY_IN_FLIGHT
        and marked_at is not None
        and timezone.now() - marked_at < EVAL_SUMMARY_STALE_AFTER
    )


def dispatch_eval_summary(test_execution, *, if_unchanged=False):
    """Queue a summary attempt for the run, identified by its queueing time.

    The summary is marked pending at that time and the attempt carries it, so
    the attempt starts only if no later one was queued or started since. With
    ``if_unchanged`` the summary is marked only if it still holds the status
    and time ``test_execution`` was read with, so two requests that both found
    no live attempt queue one between them. Returns whether one was queued.

    If queueing fails, the time is cleared so that the next request finds no
    live attempt and queues one, and the error is raised.
    """
    queued_at = timezone.now()
    marked = TestExecution.objects.filter(id=test_execution.id)
    if if_unchanged:
        read_status = test_execution.eval_explanation_summary_status
        read_at = test_execution.eval_explanation_summary_last_updated
        marked = marked.filter(
            eval_explanation_summary_status=read_status,
            eval_explanation_summary_last_updated=read_at,
        )
    if not marked.update(
        eval_explanation_summary_status=EvalExplanationSummaryStatus.PENDING,
        eval_explanation_summary_last_updated=queued_at,
    ):
        return False
    test_execution.eval_explanation_summary_status = (
        EvalExplanationSummaryStatus.PENDING
    )
    test_execution.eval_explanation_summary_last_updated = queued_at
    try:
        run_eval_summary_task.apply_async(
            args=(str(test_execution.id), queued_at.isoformat())
        )
    except Exception:
        TestExecution.objects.filter(
            id=test_execution.id,
            eval_explanation_summary_status=EvalExplanationSummaryStatus.PENDING,
            eval_explanation_summary_last_updated=queued_at,
        ).update(eval_explanation_summary_last_updated=None)
        test_execution.eval_explanation_summary_last_updated = None
        raise
    return True


@temporal_activity(
    time_limit=EVAL_SUMMARY_TIME_LIMIT,
    max_retries=0,
    queue="tasks_l",
)
def run_eval_summary_task(test_execution_id, queued_at=None):
    """
    Temporal activity to calculate and store evaluation explanation summary for a test execution.

    The attempt claims the summary when it starts and writes its result or its
    failure only while that claim holds; a later queueing or start moves the
    summary's status or last-updated time, and this attempt's writes are dropped.

    Args:
        test_execution_id: UUID of the TestExecution to calculate summary for
        queued_at: ISO time ``dispatch_eval_summary`` queued this attempt at.
            Such an attempt starts only if no later one was queued or started.
            Without it (run completion, late verdicts, the optimiser) the
            attempt takes the summary over when it starts.

    Returns:
        dict: Summary result or error information
    """
    claimed = None
    try:
        logger.info(
            f"Starting eval explanation summary calculation for test_execution: {test_execution_id}"
        )

        test_execution = TestExecution.objects.get(id=test_execution_id)

        started_at = timezone.now()
        claim = TestExecution.objects.filter(id=test_execution_id)
        if queued_at is not None:
            claim = claim.filter(
                eval_explanation_summary_status=EvalExplanationSummaryStatus.PENDING,
                eval_explanation_summary_last_updated=datetime.fromisoformat(queued_at),
            )
        if not claim.update(
            eval_explanation_summary_status=EvalExplanationSummaryStatus.RUNNING,
            eval_explanation_summary_last_updated=started_at,
        ):
            logger.info(
                f"Eval explanation summary attempt replaced before it started for test_execution: {test_execution_id}"
            )
            return
        claimed = TestExecution.objects.filter(
            id=test_execution_id,
            eval_explanation_summary_status=EvalExplanationSummaryStatus.RUNNING,
            eval_explanation_summary_last_updated=started_at,
        )

        summary = _get_cluster_dict_by_eval(
            test_execution.run_test, execution_id=test_execution_id
        )

        if not claimed.update(
            eval_explanation_summary=summary,
            eval_explanation_summary_last_updated=timezone.now(),
            eval_explanation_summary_status=EvalExplanationSummaryStatus.COMPLETED,
        ):
            logger.info(
                f"Eval explanation summary attempt replaced while running, result dropped for test_execution: {test_execution_id}"
            )
            return

        logger.info(
            f"Successfully calculated and stored eval explanation summary for test_execution: {test_execution_id}"
        )

    except TestExecution.DoesNotExist:
        logger.error(f"TestExecution {test_execution_id} not found")
        return
    except Exception as e:
        if claimed is not None:
            claimed.update(
                eval_explanation_summary_status=EvalExplanationSummaryStatus.FAILED,
                eval_explanation_summary_last_updated=timezone.now(),
            )
        logger.exception(f"Error calculating eval explanation summary: {e}")
        return
