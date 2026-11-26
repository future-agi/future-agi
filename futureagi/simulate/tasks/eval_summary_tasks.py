import structlog
from django.contrib.postgres.aggregates import StringAgg
from django.db.models import CharField, F, Func, TextField, Value
from django.db.models.functions import Cast, Coalesce
from django.utils import timezone

from simulate.models import CallExecution, SimulateEvalConfig, TestExecution
from simulate.models.test_execution import EvalExplanationSummaryStatus
from simulate.utils.eval_explaination_summary import _get_cluster_dict_by_eval
from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)

Summary = EvalExplanationSummaryStatus


def _md5_of(*fields):
    """md5, in the database, of the rows' fields in id order: the rows are
    never read into the job. A row is written whole (``ROW(...)::text``), so
    a NULL field still counts."""
    row = Cast(Func(*(F(f) for f in fields), function="ROW"), TextField())
    return Func(
        Coalesce(StringAgg(row, delimiter=",", ordering="id"), Value("")),
        function="md5",
        output_field=CharField(),
    )


def _grading_digest(test_execution):
    """A digest of the grading the summary reads: the run's calls with their
    status and verdicts, and its eval configs. When it changes while the job
    runs, the summary was made without the change."""
    calls = CallExecution.objects.filter(test_execution_id=test_execution.id).aggregate(
        digest=_md5_of("id", "status", "eval_outputs")
    )["digest"]
    configs = SimulateEvalConfig.objects.filter(
        run_test_id=test_execution.run_test_id, deleted=False
    ).aggregate(digest=_md5_of("id"))["digest"]
    return f"{calls}:{configs}"


@temporal_activity(
    time_limit=1800,
    max_retries=0,
    queue="tasks_l",
)
def run_eval_summary_task(test_execution_id):
    """Make and store a run's evaluation explanation summary.

    The result is stored as current (completed) only when the run's grading
    did not change while it was made. Otherwise it is stored as out of date
    (pending), and nothing more is queued. When another summary job stored a
    current summary first, that one is kept, and this job's failure does not
    replace it.
    """
    try:
        logger.info(
            f"Starting eval explanation summary calculation for test_execution: {test_execution_id}"
        )

        test_execution = TestExecution.objects.get(id=test_execution_id)

        # Stamped at the start, so a job that waited in the queue is timed
        # from when it began (see SUMMARY_JOB_GIVE_UP).
        TestExecution.objects.filter(id=test_execution_id).update(
            eval_explanation_summary_status=Summary.RUNNING,
            eval_explanation_summary_last_updated=timezone.now(),
        )

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
        logger.exception(f"Error calculating eval explanation summary: {e}")
        try:
            # By id, not through the row read above (which may be what
            # failed). A current summary another job stored is left as it is.
            TestExecution.objects.filter(id=test_execution_id).exclude(
                eval_explanation_summary_status=Summary.COMPLETED
            ).update(
                eval_explanation_summary_status=Summary.FAILED,
                eval_explanation_summary_last_updated=timezone.now(),
            )
        except Exception:
            # The database itself is gone: the status stays running and is
            # read as failed once SUMMARY_JOB_GIVE_UP passes.
            logger.exception(
                "eval_summary_failure_not_recorded",
                test_execution_id=str(test_execution_id),
            )
        return
