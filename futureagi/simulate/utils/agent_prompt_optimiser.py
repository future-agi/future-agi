
import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Avg
from django.db.models.functions import Round

from model_hub.utils.dataset_optimization import calculate_percentage_point_change

logger = structlog.get_logger(__name__)
from simulate.constants.agent_prompt_optimiser import (
    AGENT_PROMPT_OPTIMISER_RUN_STEPS,
    TRIAL_TABLE_BASE_COLUMNS,
)
from simulate.models import (
    AgentOptimiserRun,
    AgentPromptOptimiserRun,
    AgentPromptOptimiserRunStep,
    CallExecution,
    ComponentEvaluation,
    PromptTrial,
    SimulateEvalConfig,
    TrialItemResult,
)
from simulate.serializers.agent_prompt_optimiser import (
    AgentPromptOptimiserRunStepSerializer,
)
from simulate.utils.agent_optimiser import get_full_test_execution_data
from simulate.utils.llm import get_api_key_for_model


def fetch_agent_level_issues_from_run(optimiser_run: AgentOptimiserRun) -> list[dict]:
    try:
        if not optimiser_run.result:
            return []

        agent_level = optimiser_run.result.get("agent_level", {})
        issues = agent_level.get("actionable_recommendations", [])
        return issues

    except Exception as e:
        logger.exception(f"Error fetching issues from run: {e}")
        return []


def _build_lookup_maps(
    prompt_optimiser_run: AgentPromptOptimiserRun,
) -> tuple[dict, dict]:
    """
    Build lookup maps for CallExecutions and EvalConfigs.

    Returns:
        tuple: (call_executions_map, eval_configs_map)
    """
    test_execution = prompt_optimiser_run.test_execution

    call_executions_map = {
        str(ce.id): ce
        for ce in CallExecution.objects.filter(test_execution=test_execution)
    }

    eval_configs_map = {
        str(ec.id): ec
        for ec in SimulateEvalConfig.objects.filter(run_test=test_execution.run_test)
    }

    return call_executions_map, eval_configs_map


def _create_prompt_trial(
    prompt_optimiser_run: AgentPromptOptimiserRun,
    trial_data: dict,
    trial_number: int,
    is_baseline: bool,
) -> PromptTrial:
    """Create a PromptTrial record for a single trial."""
    is_baseline = trial_data.get("is_baseline", is_baseline)
    return PromptTrial.objects.create(
        agent_prompt_optimiser_run=prompt_optimiser_run,
        trial_number=trial_number,
        is_baseline=is_baseline,
        prompt=trial_data.get("prompt", ""),
        average_score=trial_data.get("average_score", 0),
    )


def _create_trial_item_result(
    prompt_trial: PromptTrial,
    call_execution: CallExecution,
    result_data: dict,
) -> TrialItemResult:
    """Create a TrialItemResult record for a single CallExecution result."""
    metadata = result_data.get("metadata", {})

    return TrialItemResult.objects.create(
        prompt_trial=prompt_trial,
        call_execution=call_execution,
        score=result_data.get("score", 0),
        reason=result_data.get("reason", ""),
        input_text=metadata.get("input", "") if metadata else "",
        output_text=metadata.get("output", "") if metadata else "",
        metadata=metadata,
    )


def _create_component_evaluations(
    trial_item: TrialItemResult,
    metadata: dict,
    eval_configs_map: dict,
) -> None:
    """Create ComponentEvaluation records for a trial item."""
    component_evals = metadata.get("component_evals", {}) if metadata else {}

    if not isinstance(component_evals, dict):
        logger.warning(f"component_evals is not a dict: {component_evals}, skipping")
        return

    for eval_config_id, eval_data in component_evals.items():
        eval_config = eval_configs_map.get(str(eval_config_id))
        if not eval_config:
            logger.warning(f"SimulateEvalConfig not found: {eval_config_id}, skipping")
            continue

        ComponentEvaluation.objects.create(
            trial_item_result=trial_item,
            eval_config=eval_config,
            score=eval_data.get("score", 0) if isinstance(eval_data, dict) else 0,
            reason=eval_data.get("reason", "") if isinstance(eval_data, dict) else "",
        )


def _process_individual_results(
    prompt_trial: PromptTrial,
    individual_results: dict,
    call_executions_map: dict,
    eval_configs_map: dict,
) -> None:
    """Process individual results and create TrialItemResult and ComponentEvaluation records."""
    if not isinstance(individual_results, dict):
        logger.warning(
            f"individual_results is not a dict: {individual_results}, skipping"
        )
        return

    for call_execution_id, result_data in individual_results.items():
        call_execution = call_executions_map.get(str(call_execution_id))
        if not call_execution:
            logger.warning(f"CallExecution not found: {call_execution_id}, skipping")
            continue

        trial_item = _create_trial_item_result(
            prompt_trial, call_execution, result_data
        )

        metadata = result_data.get("metadata", {})
        _create_component_evaluations(trial_item, metadata, eval_configs_map)


@transaction.atomic
def store_single_trial(
    prompt_optimiser_run: AgentPromptOptimiserRun,
    trial_data: dict,
    trial_number: int,
    stepper_state: dict,
    is_baseline: bool = False,
) -> PromptTrial:
    """
    Store a single trial with optimizer state for resume capability.
    Called via callback after each trial completes.

    Idempotent: If a trial with the same trial_number already exists, it will be
    returned without creating a duplicate. This handles race conditions where
    multiple workers might try to persist the same trial.

    Args:
        prompt_optimiser_run: The optimization run instance
        trial_data: Dict with prompt, average_score, individual_results
        trial_number: The trial number (0 for baseline)
        stepper_state: Current optimizer stepper state for resume
        is_baseline: Whether this is the baseline trial

    Returns:
        The created or existing PromptTrial instance
    """
    # Store stepper_state in metadata for resume capability
    metadata = trial_data.get("metadata") or {}
    metadata["optimizer_state"] = stepper_state

    # Use get_or_create for idempotency - respects unique constraint
    # (agent_prompt_optimiser_run, trial_number)
    prompt_trial, created = PromptTrial.objects.get_or_create(
        agent_prompt_optimiser_run=prompt_optimiser_run,
        trial_number=trial_number,
        defaults={
            "is_baseline": is_baseline,
            "prompt": trial_data.get("prompt", ""),
            "average_score": trial_data.get("average_score", 0),
            "metadata": metadata,
        },
    )

    if not created:
        # Trial already exists - this is a retry or race condition
        logger.warning(
            "Trial already exists, skipping duplicate creation",
            run_id=str(prompt_optimiser_run.id),
            trial_number=trial_number,
            existing_trial_id=str(prompt_trial.id),
        )
        # Update metadata with latest optimizer state if needed
        if prompt_trial.metadata != metadata:
            prompt_trial.metadata = metadata
            prompt_trial.save(update_fields=["metadata", "updated_at"])
            logger.info(
                "Updated optimizer state in existing trial",
                trial_id=str(prompt_trial.id),
            )
        return prompt_trial

    # Only process individual results if this is a new trial
    call_executions_map, eval_configs_map = _build_lookup_maps(prompt_optimiser_run)

    _process_individual_results(
        prompt_trial=prompt_trial,
        individual_results=trial_data.get("individual_results", {}),
        call_executions_map=call_executions_map,
        eval_configs_map=eval_configs_map,
    )

    logger.info(
        "Stored new trial",
        run_id=str(prompt_optimiser_run.id),
        trial_number=trial_number,
        trial_id=str(prompt_trial.id),
        average_score=trial_data.get("average_score", 0),
    )

    return prompt_trial


@transaction.atomic
def store_optimization_results(
    prompt_optimiser_run: AgentPromptOptimiserRun,
    result_dict: dict,
    *,
    starting_trial_number: int = 0,
) -> None:
    """
    Parse the optimization result and store structured data in the database.

    Creates:
    - PromptTrial records for each item in history[] (each prompt variation tested)
    - TrialItemResult records for each CallExecution result in individual_results{}
    - ComponentEvaluation records for each eval score in component_evals{}

    """
    history = result_dict.get("history", [])
    if not history:
        logger.warning(
            f"No history data in optimization result for run {prompt_optimiser_run.id}"
        )
        return

    call_executions_map, eval_configs_map = _build_lookup_maps(prompt_optimiser_run)

    for idx, trial_data in enumerate(history, start=starting_trial_number):
        prompt_trial = _create_prompt_trial(
            prompt_optimiser_run=prompt_optimiser_run,
            trial_data=trial_data,
            trial_number=idx,
            is_baseline=(idx == 0),
        )

        _process_individual_results(
            prompt_trial=prompt_trial,
            individual_results=trial_data.get("individual_results", {}),
            call_executions_map=call_executions_map,
            eval_configs_map=eval_configs_map,
        )

    logger.info(
        f"Stored {len(history)} trials for optimization run {prompt_optimiser_run.id}"
    )


def run_agent_prompt_optimiser(prompt_optimiser_run_id: str) -> None:
    """
    Runs the agent prompt optimiser and stores the results.
    """
    try:
        from ee.agenthub.fix_your_agent.fix_your_agent import FixYourAgent
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.fix_your_agent.fix_your_agent", exc_info=True)
        return

    prompt_optimiser_run = AgentPromptOptimiserRun.objects.get(
        id=prompt_optimiser_run_id
    )
    optimiser_run = prompt_optimiser_run.agent_optimiser_run
    test_execution = prompt_optimiser_run.test_execution

    # Get the User's API key for the optimization model
    organization = test_execution.run_test.organization
    workspace = test_execution.run_test.workspace
    workspace_id = workspace.id if workspace else None

    api_key = get_api_key_for_model(
        model_name=prompt_optimiser_run.model,
        organization_id=organization.id,
        workspace_id=workspace_id,
    )

    issues = fetch_agent_level_issues_from_run(optimiser_run)
    execution_data = get_full_test_execution_data(test_execution.id)

    agent = FixYourAgent()
    results = agent.optimize_from_execution(
        execution_data,
        optimizer_type=prompt_optimiser_run.optimiser_type,
        optimization_model=prompt_optimiser_run.model,
        issues=issues,
        use_evals=True,
        optimizer_config=prompt_optimiser_run.configuration,
        agent_optimiser_run_steps=get_agent_prompt_optimiser_run_steps(
            prompt_optimiser_run_id
        ),
        api_key=api_key,
    )

    # Convert the Pydantic model to a dictionary
    result_dict = results.dict()

    # Store structured data in normalized tables
    store_optimization_results(
        prompt_optimiser_run, result_dict, starting_trial_number=0
    )

    prompt_optimiser_run.result = result_dict
    prompt_optimiser_run.status = AgentPromptOptimiserRun.Status.COMPLETED
    prompt_optimiser_run.save(update_fields=["result", "status", "updated_at"])

    logger.info(f"Agent prompt optimiser run {prompt_optimiser_run_id} completed.")


def create_agent_prompt_optimiser_run_steps(prompt_optimiser_run_id: str) -> None:
    """
    Creates the initial steps for an agent prompt optimiser run.
    """
    prompt_optimiser_run = AgentPromptOptimiserRun.objects.get(
        id=prompt_optimiser_run_id
    )

    steps_to_create = []
    current_step_number = 1
    for step_data in AGENT_PROMPT_OPTIMISER_RUN_STEPS:
        steps_to_create.append(
            AgentPromptOptimiserRunStep(
                agent_prompt_optimiser_run=prompt_optimiser_run,
                step_number=current_step_number,
                name=step_data["name"],
                description=step_data["description"],
            )
        )
        current_step_number += 1

    AgentPromptOptimiserRunStep.objects.bulk_create(steps_to_create)


def get_agent_prompt_optimiser_run_steps(prompt_optimiser_run_id: str) -> list[dict]:
    """
    Gets the steps for an agent prompt optimiser run.
    """
    steps = AgentPromptOptimiserRunStep.objects.filter(
        agent_prompt_optimiser_run_id=prompt_optimiser_run_id
    ).order_by("step_number")
    serializer = AgentPromptOptimiserRunStepSerializer(steps, many=True)

    return serializer.data


def update_agent_optimiser_run_step(
    steps: list[dict] | None,
    step_number: int,
    status: str | None = None,
    name: str | None = None,
    description: str | None = None,
    error: str | None = None,
) -> None:
    """
    Helper to update agent prompt optimiser run step status.
    """
    if not steps:
        return

    step_data = next((s for s in steps if s.get("step_number") == step_number), None)
    if not step_data:
        return

    try:
        step = AgentPromptOptimiserRunStep.objects.get(id=step_data["id"])
        update_fields = []

        if name is not None:
            step.name = name
            update_fields.append("name")

        if description is not None:
            step.description = description
            update_fields.append("description")

        if error:
            current_desc = step.description or ""
            step.description = f"{current_desc}\nError: {error}".strip()
            if "description" not in update_fields:
                update_fields.append("description")

        if status:
            step.status = status
            update_fields.append("status")

        if update_fields:
            update_fields.append("updated_at")
            step.save(update_fields=update_fields)

    except AgentPromptOptimiserRunStep.DoesNotExist:
        logger.warning(f"Run step {step_number} (id: {step_data.get('id')}) not found")
    except Exception as e:
        logger.exception(
            f"Failed to update run step {step_number} (id: {step_data.get('id')}): {e}"
        )


def _fetch_all_eval_data(optimiser_run):
    """
    Fetch all eval data for an optimiser run in a single query.

    Returns:
        QuerySet with trial_id, eval_config_id, eval_config_name, is_baseline, and avg_score
    """
    return (
        ComponentEvaluation.objects.filter(
            trial_item_result__prompt_trial__agent_prompt_optimiser_run=optimiser_run
        )
        .values(
            "trial_item_result__prompt_trial__id",
            "trial_item_result__prompt_trial__is_baseline",
            "eval_config__id",
            "eval_config__name",
        )
        .annotate(avg_score=Avg("score"))
    )


def _process_eval_data(all_evals):
    """
    Process raw eval data into structured dictionaries.

    Returns:
        tuple: (baseline_eval_scores, trial_eval_scores, eval_config_ids)
    """
    baseline_eval_scores = {}
    trial_eval_scores = {}
    eval_config_ids = set()

    for eval_data in all_evals:
        trial_id = eval_data["trial_item_result__prompt_trial__id"]
        is_baseline = eval_data["trial_item_result__prompt_trial__is_baseline"]
        eval_config_id = eval_data["eval_config__id"]
        eval_config_name = eval_data["eval_config__name"]
        avg_score = eval_data["avg_score"]

        eval_info = {"name": eval_config_name, "score": avg_score}

        if is_baseline:
            baseline_eval_scores[eval_config_id] = eval_info
        else:
            if trial_id not in trial_eval_scores:
                trial_eval_scores[trial_id] = {}
            trial_eval_scores[trial_id][eval_config_id] = eval_info
            eval_config_ids.add(eval_config_id)

    return baseline_eval_scores, trial_eval_scores, eval_config_ids


# Backward-compat alias — canonical implementation lives in
# model_hub.utils.dataset_optimization.calculate_percentage_point_change
_calculate_percentage_change = calculate_percentage_point_change


def _build_trial_row(
    trial, baseline_score, baseline_eval_scores, trial_eval_scores, best_trial_id
):
    """Build a single trial row for the table data."""
    score_percentage_change = _calculate_percentage_change(
        trial.average_score, baseline_score
    )

    eval_scores = {}
    trial_evals = trial_eval_scores.get(trial.id, {})

    for eval_config_id, eval_info in trial_evals.items():
        eval_score = eval_info.get("score")
        baseline_eval_score = baseline_eval_scores.get(eval_config_id, {}).get("score")
        percentage_change = _calculate_percentage_change(
            eval_score, baseline_eval_score
        )

        eval_scores[str(eval_config_id)] = {
            "score": round(eval_score, 4) if eval_score is not None else None,
            "percentage_change": percentage_change,
        }

    return {
        "id": str(trial.id),
        "trial": f"Trial {trial.trial_number}",
        "score": (
            round(trial.average_score, 4) if trial.average_score is not None else None
        ),
        "score_percentage_change": score_percentage_change,
        "prompt": trial.prompt,
        "is_best": trial.id == best_trial_id,
        **eval_scores,
    }


def build_trial_table_data(optimiser_run):
    """
    Build table data for trials with eval score comparisons against baseline.

    Returns:
        tuple: (table_data, column_config)
    """
    trials = list(optimiser_run.trials.all())

    all_evals = _fetch_all_eval_data(optimiser_run)
    baseline_eval_scores, trial_eval_scores, eval_config_ids = _process_eval_data(
        all_evals
    )

    baseline_trial = next((t for t in trials if t.is_baseline), None)
    baseline_score = baseline_trial.average_score if baseline_trial else None

    non_baseline_trials = sorted(
        [t for t in trials if not t.is_baseline], key=lambda t: t.trial_number
    )

    trials_with_scores = [t for t in non_baseline_trials if t.average_score is not None]
    best_trial = (
        max(trials_with_scores, key=lambda t: t.average_score)
        if trials_with_scores
        else None
    )
    best_trial_id = best_trial.id if best_trial else None

    # Build table data
    table_data = [
        _build_trial_row(
            trial,
            baseline_score,
            baseline_eval_scores,
            trial_eval_scores,
            best_trial_id,
        )
        for trial in non_baseline_trials
    ]

    # Build column config
    column_config = list(TRIAL_TABLE_BASE_COLUMNS)
    eval_columns = [
        {
            "id": str(eval_config_id),
            "name": baseline_eval_scores.get(eval_config_id, {}).get(
                "name", "Unknown Eval"
            ),
            "is_visible": True,
        }
        for eval_config_id in eval_config_ids
    ]
    column_config.extend(eval_columns)

    return table_data, column_config


def _get_trial_name(trial_number: int) -> str:
    """Get the name of a trial."""
    if trial_number == 0:
        return "Baseline"
    return f"Trial {trial_number}"


def get_agent_prompt_optimiser_run_graph_data(
    optimiser_run: AgentPromptOptimiserRun,
) -> dict[str, dict]:
    """
    Get graph data for an agent prompt optimiser run.

    Returns dict keyed by eval_config_id with evaluations sorted by trial_number.
    """
    trials = list(optimiser_run.trials.all().order_by("trial_number"))

    eval_data = (
        ComponentEvaluation.objects.filter(trial_item_result__prompt_trial__in=trials)
        .values(
            "trial_item_result__prompt_trial__id",
            "trial_item_result__prompt_trial__trial_number",
            "eval_config__id",
            "eval_config__name",
        )
        .annotate(avg_score=Round(Avg("score"), 2))
    )

    graph_data: dict[str, dict] = {}

    for eval in eval_data:
        eval_config_id = str(eval["eval_config__id"])
        eval_config_name = eval["eval_config__name"]
        trial_number = eval["trial_item_result__prompt_trial__trial_number"]
        trial_id = str(eval["trial_item_result__prompt_trial__id"])
        trial_name = _get_trial_name(trial_number)
        score = eval["avg_score"]

        if eval_config_id not in graph_data:
            graph_data[eval_config_id] = {
                "name": eval_config_name,
                "evaluations": [],
            }

        graph_data[eval_config_id]["evaluations"].append(
            {
                "trial_id": trial_id,
                "trial_number": trial_number,
                "trial_name": trial_name,
                "score": score,
            }
        )

    for config in graph_data.values():
        config["evaluations"].sort(key=lambda x: x["trial_number"])

    return graph_data
