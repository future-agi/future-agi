import structlog

logger = structlog.get_logger(__name__)
from simulate.models import AgentOptimiserRun, AgentPromptOptimiserRun
from simulate.utils.agent_optimiser import execute_simulation_analysis
from simulate.utils.agent_prompt_optimiser import run_agent_prompt_optimiser
from tfc.temporal.drop_in import temporal_activity
from tfc.utils.error_codes import get_error_message


@temporal_activity(
    time_limit=3600,
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
        result = execute_simulation_analysis(run.input_data)
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
