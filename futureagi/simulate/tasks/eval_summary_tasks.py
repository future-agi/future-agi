import hashlib
import json

import structlog
from django.utils import timezone

from simulate.models import CallExecution, SimulateEvalConfig, TestExecution
from simulate.models.test_execution import EvalExplanationSummaryStatus
from simulate.utils.eval_explaination_summary import _get_cluster_dict_by_eval
from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)

Summary = EvalExplanationSummaryStatus


def _grading_digest(test_execution):
    """A digest of the grading the summary reads: the run's calls with their
    status and verdicts, and its eval configs. When it changes while the job
    runs, the summary was made without the change."""
    calls = list(
        CallExecution.objects.filter(test_execution_id=test_execution.id)
        .order_by("id")
        .values_list("id", "status", "eval_outputs")
    )
    configs = list(
        SimulateEvalConfig.objects.filter(
            run_test_id=test_execution.run_test_id, deleted=False
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    grading = {
        "calls": [[str(id_), status, outputs] for id_, status, outputs in calls],
        "configs": [str(id_) for id_ in configs],
    }
    # Verdicts may hold values strict JSON refuses (NaN), so not canonical_json.
    text = json.dumps(grading, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@temporal_activity(
    time_limit=1800,
    max_retries=0,
    queue="tasks_l",
)
def run_eval_summary_task(test_execution_id):
    """Make and store a run's evaluation explanation summary.

    The result is stored as current (completed) only when the run's grading
    did not change while it was made. Otherwise it is stored as out of date
    (pending): the page shows it with a note to press Refresh, and nothing
    more is queued. When another summary job stored a current summary first,
    that one is kept.
    """
    try:
        logger.info(
            f"Starting eval explanation summary calculation for test_execution: {test_execution_id}"
        )

        test_execution = TestExecution.objects.get(id=test_execution_id)

        test_execution.eval_explanation_summary_status = Summary.RUNNING
        test_execution.save(update_fields=["eval_explanation_summary_status"])

        before = _grading_digest(test_execution)
        summary = _get_cluster_dict_by_eval(
            test_execution.run_test, execution_id=test_execution_id
        )
        changed = _grading_digest(test_execution) != before
        stored_at = timezone.now()
        # Running, or moved off it by another summary job that failed or by
        # a refresh that could not be queued. A summary another job stored
        # as current is left as it is: a later verdict would have marked it
        # out of date.
        written = (
            TestExecution.objects.filter(id=test_execution_id)
            .exclude(eval_explanation_summary_status=Summary.COMPLETED)
            .update(
                eval_explanation_summary=summary,
                eval_explanation_summary_last_updated=stored_at,
                eval_explanation_summary_status=(
                    Summary.PENDING if changed else Summary.COMPLETED
                ),
            )
        )
        if written and not changed and _grading_digest(test_execution) != before:
            # A verdict committed between the last look and the write.
            TestExecution.objects.filter(
                id=test_execution_id,
                eval_explanation_summary_status=Summary.COMPLETED,
                eval_explanation_summary_last_updated=stored_at,
            ).update(eval_explanation_summary_status=Summary.PENDING)

        logger.info(
            "eval_summary_stored",
            test_execution_id=str(test_execution_id),
            out_of_date=changed,
            kept_another_jobs_summary=not written,
        )

    except TestExecution.DoesNotExist:
        logger.error(f"TestExecution {test_execution_id} not found")
        return
    except Exception as e:
        test_execution.eval_explanation_summary_status = Summary.FAILED
        test_execution.eval_explanation_summary_last_updated = timezone.now()
        test_execution.save(
            update_fields=[
                "eval_explanation_summary_status",
                "eval_explanation_summary_last_updated",
            ]
        )
        logger.exception(f"Error calculating eval explanation summary: {e}")
        return
