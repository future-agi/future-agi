import structlog
from django.utils import timezone

logger = structlog.get_logger(__name__)
from simulate.models import TestExecution
from simulate.models.test_execution import EvalExplanationSummaryStatus
from simulate.utils.eval_explaination_summary import _get_cluster_dict_by_eval
from tfc.temporal.drop_in import temporal_activity


@temporal_activity(
    time_limit=1800,
    max_retries=0,
    queue="tasks_l",
)
def run_eval_summary_task(test_execution_id):
    """
    Temporal activity to calculate and store evaluation explanation summary for a test execution.

    Args:
        test_execution_id: UUID of the TestExecution to calculate summary for

    Returns:
        dict: Summary result or error information
    """
    test_execution = None
    try:
        logger.info(
            f"Starting eval explanation summary calculation for test_execution: {test_execution_id}"
        )

        test_execution = TestExecution.objects.get(id=test_execution_id)

        test_execution.eval_explanation_summary_status = (
            EvalExplanationSummaryStatus.RUNNING
        )
        test_execution.save(update_fields=["eval_explanation_summary_status"])

        summary = _get_cluster_dict_by_eval(
            test_execution.run_test, execution_id=test_execution_id
        )

        test_execution.eval_explanation_summary = summary
        test_execution.eval_explanation_summary_last_updated = timezone.now()
        test_execution.eval_explanation_summary_status = (
            EvalExplanationSummaryStatus.COMPLETED
        )
        test_execution.save(
            update_fields=[
                "eval_explanation_summary",
                "eval_explanation_summary_last_updated",
                "eval_explanation_summary_status",
            ]
        )

        logger.info(
            f"Successfully calculated and stored eval explanation summary for test_execution: {test_execution_id}"
        )

    except TestExecution.DoesNotExist:
        logger.error(f"TestExecution {test_execution_id} not found")
        return
    except Exception as e:
        logger.exception(f"Error calculating eval explanation summary: {e}")
    finally:
        # Ensure status is never left as RUNNING if something went wrong.
        # Re-fetch to get current state; only update if still stuck in RUNNING.
        if test_execution is not None:
            test_execution.refresh_from_db(fields=["eval_explanation_summary_status"])
            if (
                test_execution.eval_explanation_summary_status
                == EvalExplanationSummaryStatus.RUNNING
            ):
                test_execution.eval_explanation_summary_status = (
                    EvalExplanationSummaryStatus.FAILED
                )
                test_execution.eval_explanation_summary_last_updated = timezone.now()
                test_execution.save(
                    update_fields=[
                        "eval_explanation_summary_status",
                        "eval_explanation_summary_last_updated",
                    ]
                )
