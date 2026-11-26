import structlog

from simulate.models import AgentOptimiserRun, AgentPromptOptimiserRun, TestExecution
from simulate.models.test_execution import EvalExplanationSummaryStatus
from simulate.utils.agent_optimiser import (
    OPTIMISER_RUN_TIME_LIMIT_SECONDS,
    execute_simulation_analysis,
)
from simulate.utils.agent_prompt_optimiser import run_agent_prompt_optimiser
from tfc.temporal.drop_in import temporal_activity
from tfc.utils.error_codes import get_error_message

logger = structlog.get_logger(__name__)


def _current_summary(test_execution_id):
    """The test run's summary if it is current (completed), else None."""
    return (
        TestExecution.objects.filter(
            id=test_execution_id,
            eval_explanation_summary_status=EvalExplanationSummaryStatus.COMPLETED,
        )
        .values_list("eval_explanation_summary", flat=True)
        .first()
    )


def _with_run_summary(run: AgentOptimiserRun) -> dict:
    """The run's input, with the test run's current summary in it.

    The analysis reads the summary. Whether it is current is read here, when
    the job starts, never from what was queued: a current one is used as it
    is, and a missing or out-of-date one is made first, inside the job the
    person started, never inside a web request. When making it fails, the
    analysis gets no summary rather than the old one.
    """
    input_data = dict(run.input_data or {})
    test_execution_id = input_data.get("test_execution_id")
    if not test_execution_id:
        return input_data
    current = _current_summary(test_execution_id)
    if current is not None:
        input_data["cluster_dict_by_eval"] = current
        return input_data

    from simulate.tasks.eval_summary_tasks import run_eval_summary_task

    logger.info(
        "optimiser_run_making_missing_summary",
        run_id=str(run.id),
        test_execution_id=str(test_execution_id),
    )
    run_eval_summary_task(str(test_execution_id))
    made = (
        TestExecution.objects.filter(id=test_execution_id)
        .values_list("eval_explanation_summary", "eval_explanation_summary_status")
        .first()
    )
    summary, summary_status = made if made is not None else (None, None)
    if summary_status not in (
        EvalExplanationSummaryStatus.COMPLETED,
        # Made by this job; a verdict landed while it ran. Still the newest.
        EvalExplanationSummaryStatus.PENDING,
    ):
        # The remake failed: what is stored is the old summary, which is not
        # analysed as if it were current. The analysis runs without one.
        logger.warning(
            "optimiser_run_summary_not_made",
            run_id=str(run.id),
            test_execution_id=str(test_execution_id),
            summary_status=summary_status,
        )
        summary = None
    input_data["cluster_dict_by_eval"] = summary
    run.input_data = input_data
    run.save(update_fields=["input_data", "updated_at"])
    return input_data


@temporal_activity(
    time_limit=OPTIMISER_RUN_TIME_LIMIT_SECONDS,
    max_retries=0,
    queue="tasks_l",
)
def execute_optimiser_run(run_id: str):
    """
    Execute a single agent optimiser run and save the results.

    Args:
        run_id: UUID of the AgentOptimiserRun

    Returns:
        dict: Result with status and data
    """

    run = AgentOptimiserRun.objects.get(id=run_id)

    try:
        logger.info(f"Executing optimiser run {run_id} for {run.agent_optimiser.name}")

        run.mark_as_running()
        result = execute_simulation_analysis(_with_run_summary(run))
        run.mark_as_completed(result=result)

        logger.info(f"Optimiser run {run_id} completed successfully")

        return {
            "status": "success",
            "run_id": str(run_id),
            "result": result,
        }

    except Exception as e:
        run.mark_as_failed(error_info=str(e))
        logger.exception(f"Optimiser run {run_id} execution failed: {str(e)}")
        raise


@temporal_activity(time_limit=3600, queue="tasks_l")
def run_agent_prompt_optimiser_task(prompt_optimiser_run_id: str):
    """
    Temporal activity to run the agent prompt optimiser.
    """

    prompt_optimiser_run = AgentPromptOptimiserRun.objects.get(
        id=prompt_optimiser_run_id
    )
    prompt_optimiser_run.mark_as_running()

    try:
        run_agent_prompt_optimiser(prompt_optimiser_run_id)
    except ValueError as e:
        prompt_optimiser_run.mark_as_failed(error_message=str(e))
    except Exception as e:
        logger.exception(
            f"Agent prompt optimiser task failed for run {prompt_optimiser_run_id}: {str(e)}"
        )
        prompt_optimiser_run.mark_as_failed(
            error_message=get_error_message("FAILED_TO_OPTIMISE_PROMPT")
        )

        raise
