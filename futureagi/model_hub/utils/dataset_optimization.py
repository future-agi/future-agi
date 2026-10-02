"""
Dataset Optimization Utilities

This module provides utility functions for dataset optimization runs,
following the same patterns as simulate.utils.agent_prompt_optimiser.
"""

from collections import defaultdict
from typing import Optional

import structlog
from django.db import transaction
from django.db.models import Avg
from django.db.models.functions import Round

from model_hub.models.dataset_optimization_step import DatasetOptimizationStep
from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
from model_hub.models.dataset_optimization_trial_item import (
    DatasetOptimizationItemEvaluation,
    DatasetOptimizationTrialItem,
)
from model_hub.models.optimize_dataset import OptimizeDataset

logger = structlog.get_logger(__name__)

# Step definitions for dataset optimization runs
DATASET_OPTIMIZATION_STEPS = [
    {
        "name": "Onboard/initializing",
        "description": "Initializing the optimization process and setting up the environment.",
    },
    {
        "name": "Running baseline eval",
        "description": "Evaluating the performance of the initial baseline prompt to establish a benchmark.",
    },
    {
        "name": "Starting trials/starting optimization",
        "description": "Beginning the iterative process of generating and testing new prompt variations.",
    },
    {
        "name": "Finalizing optimization",
        "description": "Selecting the best-performing prompt and completing the optimization run.",
    },
]

# Table column configurations
TRIAL_TABLE_BASE_COLUMNS = [
    {"id": "trial", "name": "Trial", "is_visible": True},
    {"id": "prompt", "name": "Prompt", "is_visible": True},
]

OPTIMIZATION_RUN_TABLE_CONFIG = [
    {"id": "name", "name": "Optimization Name", "is_visible": True},
    {"id": "created_at", "name": "Started At", "is_visible": True},
    {"id": "trial_count", "name": "No. of Trials", "is_visible": True},
    {"id": "optimizer_algorithm", "name": "Optimizer Type", "is_visible": True},
    {"id": "status", "name": "Status", "is_visible": True},
]

OPTIMIZER_PARAMETER_LABELS = {
    "num_variations": {
        "label": "Number of Variations",
        "description": "Number of prompt variations to generate",
    },
    "max_metric_calls": {
        "label": "Max Metric Calls",
        "description": "Maximum number of metric evaluations",
    },
    "beam_size": {
        "label": "Beam Size",
        "description": "Number of candidates to keep at each step",
    },
    "num_gradients": {
        "label": "Number of Gradients",
        "description": "Number of gradient samples",
    },
    "errors_per_gradient": {
        "label": "Errors per Gradient",
        "description": "Number of errors to sample per gradient",
    },
    "prompts_per_gradient": {
        "label": "Prompts per Gradient",
        "description": "Number of prompts per gradient",
    },
    "num_rounds": {
        "label": "Number of Rounds",
        "description": "Number of optimization rounds",
    },
    "min_examples": {
        "label": "Min Examples",
        "description": "Minimum number of examples to use",
    },
    "max_examples": {
        "label": "Max Examples",
        "description": "Maximum number of examples to use",
    },
    "n_trials": {
        "label": "Number of Trials",
        "description": "Number of trials to run",
    },
    "task_description": {
        "label": "Task Description",
        "description": "Description of the task",
    },
    "mutate_rounds": {
        "label": "Mutate Rounds",
        "description": "Number of mutation rounds",
    },
    "refine_iterations": {
        "label": "Refine Iterations",
        "description": "Number of refinement iterations",
    },
}


def build_parameters_array(optimizer_config, optimizer_algorithm=None):
    """Build the parameters array shown in the run-detail sidebar."""
    if not optimizer_config:
        return []

    parameters = []
    for key, value in optimizer_config.items():
        if key == "model_name":
            continue
        info = OPTIMIZER_PARAMETER_LABELS.get(key, {"label": key, "description": ""})
        parameters.append(
            {
                "key": key,
                "label": info["label"],
                "description": info.get("description", ""),
                "value": value,
            }
        )
    return parameters


def create_dataset_optimization_steps(optimization_run_id: str) -> None:
    """
    Creates the initial steps for a dataset optimization run.
    """
    optimization_run = OptimizeDataset.objects.get(id=optimization_run_id)

    steps_to_create = []
    current_step_number = 1
    for step_data in DATASET_OPTIMIZATION_STEPS:
        steps_to_create.append(
            DatasetOptimizationStep(
                optimization_run=optimization_run,
                step_number=current_step_number,
                name=step_data["name"],
                description=step_data["description"],
            )
        )
        current_step_number += 1

    DatasetOptimizationStep.objects.bulk_create(steps_to_create)
    logger.info(
        "Created optimization steps",
        run_id=str(optimization_run_id),
        num_steps=len(steps_to_create),
    )


def get_dataset_optimization_steps(optimization_run_id: str) -> list[dict]:
    """
    Gets the steps for a dataset optimization run.
    """
    from model_hub.serializers.dataset_optimization import (
        DatasetOptimizationStepSerializer,
    )

    steps = DatasetOptimizationStep.objects.filter(
        optimization_run_id=optimization_run_id
    ).order_by("step_number")
    serializer = DatasetOptimizationStepSerializer(steps, many=True)

    return serializer.data


def update_dataset_optimization_step(
    steps: Optional[list[dict]],
    step_number: int,
    status: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """
    Helper to update dataset optimization step status.
    """
    if not steps:
        return

    step_data = next((s for s in steps if s.get("step_number") == step_number), None)
    if not step_data:
        return

    try:
        step = DatasetOptimizationStep.objects.get(id=step_data["id"])
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

    except DatasetOptimizationStep.DoesNotExist:
        logger.warning(
            f"Optimization step {step_number} (id: {step_data.get('id')}) not found"
        )
    except Exception as e:
        logger.exception(
            f"Failed to update optimization step {step_number} (id: {step_data.get('id')}): {e}"
        )


@transaction.atomic
def store_single_trial(
    optimization_run: OptimizeDataset,
    trial_data: dict,
    trial_number: int,
    stepper_state: dict,
    is_baseline: bool = False,
    user_eval_metrics: list = None,
) -> DatasetOptimizationTrial:
    """
    Store a single trial with optimizer state for resume capability.
    Called via callback after each trial completes.

    Idempotent: If a trial with the same trial_number already exists, it will be
    returned without creating a duplicate.

    Args:
        optimization_run: The optimization run instance
        trial_data: Dict with prompt, average_score, individual_results
        trial_number: The trial number (0 for baseline)
        stepper_state: Current optimizer stepper state for resume
        is_baseline: Whether this is the baseline trial
        user_eval_metrics: List of UserEvalMetric objects for creating evaluations

    Returns:
        The created or existing DatasetOptimizationTrial instance
    """
    # Store stepper_state in metadata (keep for resume capability)
    metadata = trial_data.get("metadata") or {}
    metadata["optimizer_state"] = stepper_state

    individual_results = trial_data.get("individual_results")

    # Use get_or_create for idempotency
    trial, created = DatasetOptimizationTrial.objects.get_or_create(
        optimization_run=optimization_run,
        trial_number=trial_number,
        defaults={
            "is_baseline": is_baseline,
            "prompt": trial_data.get("prompt", ""),
            "average_score": trial_data.get("average_score", 0),
            "metadata": metadata,
        },
    )

    if not created:
        trial.average_score = trial_data.get("average_score", trial.average_score)
        trial.metadata = metadata
        trial.save(update_fields=["average_score", "metadata", "updated_at"])

        if individual_results:
            existing_row_ids = set(trial.trial_items.values_list("row_id", flat=True))
            new_results = {
                k: v for k, v in individual_results.items() if k not in existing_row_ids
            }
            if new_results:
                _create_trial_items(trial, new_results, user_eval_metrics)
                logger.info(
                    "Added new trial items to existing trial",
                    trial_id=str(trial.id),
                    new_items=len(new_results),
                    total_items=len(existing_row_ids) + len(new_results),
                )

        return trial

    # Create TrialItem records for each row result
    if individual_results:
        _create_trial_items(trial, individual_results, user_eval_metrics)

    logger.info(
        "Stored new trial",
        run_id=str(optimization_run.id),
        trial_number=trial_number,
        trial_id=str(trial.id),
        average_score=trial_data.get("average_score", 0),
        prompt_len=len(trial_data.get("prompt", "")),
        prompt_preview=(
            trial_data.get("prompt", "")[:100]
            if trial_data.get("prompt")
            else "(empty)"
        ),
        num_individual_results=len(individual_results) if individual_results else 0,
    )

    return trial


def _create_trial_items(
    trial: DatasetOptimizationTrial,
    individual_results: dict,
    user_eval_metrics: list = None,
) -> None:
    """
    Create DatasetOptimizationTrialItem records for each row result.

    Args:
        trial: The trial instance
        individual_results: Dict[str, EvaluationResult] where key is row/call ID
        user_eval_metrics: List of UserEvalMetric objects for creating evaluations
    """
    import json

    trial_items_to_create = []
    item_evaluations_to_create = []

    # Build a mapping of eval metrics by index for evaluation creation
    eval_metrics_list = list(user_eval_metrics) if user_eval_metrics else []

    for row_id, eval_result in individual_results.items():
        # Extract data from EvaluationResult (can be dict or object)
        if isinstance(eval_result, dict):
            score = eval_result.get("score", 0)
            reason = eval_result.get("reason", "")
            eval_metadata = eval_result.get("metadata", {})
        else:
            score = getattr(eval_result, "score", 0)
            reason = getattr(eval_result, "reason", "")
            eval_metadata = getattr(eval_result, "metadata", {}) or {}

        # Extract input/output/filled_prompt from metadata
        input_data = eval_metadata.get("input", {})
        output_text = eval_metadata.get("output", "")
        filled_prompt = eval_metadata.get("filled_prompt", "")
        individual_scores = eval_metadata.get("individual_scores", [])

        # Serialize input if it's a dict
        input_text = (
            json.dumps(input_data) if isinstance(input_data, dict) else str(input_data)
        )

        # Create trial item
        trial_item = DatasetOptimizationTrialItem(
            trial=trial,
            row_id=str(row_id),
            score=score or 0,
            reason=reason,
            input_text=input_text,
            output_text=output_text,
            filled_prompt=filled_prompt,
            metadata=eval_metadata,
        )
        trial_items_to_create.append(trial_item)

    # Bulk create trial items
    created_items = DatasetOptimizationTrialItem.objects.bulk_create(
        trial_items_to_create,
        ignore_conflicts=True,  # Idempotency
    )

    # Now create evaluations for each item
    # We need to re-fetch items to get their IDs since bulk_create with ignore_conflicts
    # doesn't return IDs for existing items
    existing_items = {
        item.row_id: item
        for item in DatasetOptimizationTrialItem.objects.filter(trial=trial)
    }

    for row_id, eval_result in individual_results.items():
        trial_item = existing_items.get(str(row_id))
        if not trial_item:
            continue

        # Get individual scores from metadata
        if isinstance(eval_result, dict):
            eval_metadata = eval_result.get("metadata", {})
        else:
            eval_metadata = getattr(eval_result, "metadata", {}) or {}

        individual_scores = eval_metadata.get("individual_scores", [])

        # Create evaluation records for each eval metric score
        for idx, (score, reason) in enumerate(individual_scores):
            if idx < len(eval_metrics_list):
                eval_metric = eval_metrics_list[idx]
                item_evaluations_to_create.append(
                    DatasetOptimizationItemEvaluation(
                        trial_item=trial_item,
                        eval_metric=eval_metric,
                        score=score or 0,
                        reason=reason or "",
                    )
                )

    # Bulk create evaluations
    if item_evaluations_to_create:
        DatasetOptimizationItemEvaluation.objects.bulk_create(
            item_evaluations_to_create,
            ignore_conflicts=True,
        )

    logger.info(
        "Created trial items and evaluations",
        trial_id=str(trial.id),
        num_items=len(created_items),
        num_evaluations=len(item_evaluations_to_create),
    )


@transaction.atomic
def store_optimization_results(
    optimization_run: OptimizeDataset,
    result_dict: dict,
    *,
    starting_trial_number: int = 0,
) -> None:
    """
    Parse the optimization result and store structured data in the database.

    Creates DatasetOptimizationTrial records for each item in history[].
    """
    history = result_dict.get("history", [])
    if not history:
        logger.warning(
            f"No history data in optimization result for run {optimization_run.id}"
        )
        return

    for idx, trial_data in enumerate(history, start=starting_trial_number):
        DatasetOptimizationTrial.objects.create(
            optimization_run=optimization_run,
            trial_number=idx,
            is_baseline=(idx == 0),
            prompt=trial_data.get("prompt", ""),
            average_score=trial_data.get("average_score", 0),
            metadata=trial_data.get("metadata"),
        )

    logger.info(
        f"Stored {len(history)} trials for optimization run {optimization_run.id}"
    )


def calculate_percentage_point_change(value: float, baseline: float) -> Optional[float]:
    """Calculate absolute percentage point change from baseline.

    Scores are in [0.0, 1.0], so this returns the difference in percentage
    points (e.g. 0.30 → 0.33 = +3.0pp), NOT the relative percentage change.
    """
    if value is not None and baseline is not None:
        return round((value - baseline) * 100, 2)
    return None


def _get_trial_name(trial_number: int) -> str:
    """Get the name of a trial."""
    if trial_number == 0:
        return "Baseline"
    return f"Trial {trial_number}"


def _fetch_all_eval_data(optimization_run: OptimizeDataset):
    """
    Fetch all evaluation data for trials in an optimization run.

    Returns:
        QuerySet with trial_id, eval_metric_id, eval_metric_name, is_baseline, and avg_score
    """
    return (
        DatasetOptimizationItemEvaluation.objects.filter(
            trial_item__trial__optimization_run=optimization_run
        )
        .values(
            "trial_item__trial__id",
            "trial_item__trial__is_baseline",
            "eval_metric__id",
            "eval_metric__template__name",
        )
        .annotate(avg_score=Round(Avg("score"), 4))
    )


def _process_eval_data(all_evals):
    """
    Process raw eval data into structured dictionaries.

    Returns:
        tuple: (baseline_eval_scores, trial_eval_scores, eval_metric_ids)
    """
    baseline_eval_scores = {}
    trial_eval_scores = {}
    eval_metric_ids = set()

    for eval_data in all_evals:
        trial_id = eval_data["trial_item__trial__id"]
        is_baseline = eval_data["trial_item__trial__is_baseline"]
        eval_metric_id = eval_data["eval_metric__id"]
        eval_metric_name = eval_data["eval_metric__template__name"] or "Unknown Eval"
        avg_score = eval_data["avg_score"]

        eval_info = {"name": eval_metric_name, "score": avg_score}

        if is_baseline:
            baseline_eval_scores[eval_metric_id] = eval_info
        else:
            if trial_id not in trial_eval_scores:
                trial_eval_scores[trial_id] = {}
            trial_eval_scores[trial_id][eval_metric_id] = eval_info
            eval_metric_ids.add(eval_metric_id)

    return baseline_eval_scores, trial_eval_scores, eval_metric_ids


def _build_trial_row(
    trial, baseline_score, baseline_eval_scores, trial_eval_scores, best_trial_id
):
    """Build a single trial row for the table data."""
    score_percentage_change = calculate_percentage_point_change(
        trial.average_score, baseline_score
    )

    eval_scores = {}
    trial_evals = trial_eval_scores.get(trial.id, {})

    for eval_metric_id, eval_info in trial_evals.items():
        eval_score = eval_info.get("score")
        baseline_eval_score = baseline_eval_scores.get(eval_metric_id, {}).get("score")
        percentage_change = calculate_percentage_point_change(
            eval_score, baseline_eval_score
        )

        eval_scores[str(eval_metric_id)] = {
            "score": round(eval_score, 4) if eval_score is not None else None,
            "percentage_change": percentage_change,
        }

    return {
        "id": str(trial.id),
        "trial": f"Trial {trial.trial_number}",
        "score_percentage_change": score_percentage_change,
        "prompt": trial.prompt,
        "is_best": trial.id == best_trial_id,
        "eval_scores": eval_scores,
    }


def build_trial_table_data(optimization_run: OptimizeDataset) -> tuple[list, list]:
    """
    Build table data for trials with eval score comparisons against baseline.

    Returns:
        tuple: (table_data, column_config)
    """
    trials = list(optimization_run.trials.all().order_by("trial_number"))

    all_evals = _fetch_all_eval_data(optimization_run)
    baseline_eval_scores, trial_eval_scores, eval_metric_ids = _process_eval_data(
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
            "id": str(eval_metric_id),
            "name": baseline_eval_scores.get(eval_metric_id, {}).get(
                "name", "Unknown Eval"
            ),
            "is_visible": True,
        }
        for eval_metric_id in eval_metric_ids
    ]
    column_config.extend(eval_columns)

    return table_data, column_config


def get_optimization_graph_data(optimization_run: OptimizeDataset) -> dict:
    """
    Get graph data for a dataset optimization run.

    Returns dict with series for each user eval metric with per-metric scores,
    keyed by eval_metric UUID.

    Response format:
    {
        "eval_metric_uuid_1": {
            "name": "no_invalid_links",
            "eval_id": "eval_metric_uuid_1",
            "evaluations": [
                {
                    "trial_id": "...",
                    "trial_number": 0,
                    "trial_name": "Baseline",
                    "score": 0.95
                },
                ...
            ]
        },
        "eval_metric_uuid_2": {...}
    }
    """
    # Get per-metric scores aggregated by trial in a single query
    # Query: for each (eval_metric, trial), get average score across all rows
    per_metric_scores = (
        DatasetOptimizationItemEvaluation.objects.filter(
            trial_item__trial__optimization_run=optimization_run
        )
        .values(
            "eval_metric__id",
            "eval_metric__template__name",
            "trial_item__trial__id",
            "trial_item__trial__trial_number",
        )
        .annotate(avg_score=Round(Avg("score"), 2))
        .order_by("eval_metric__id", "trial_item__trial__trial_number")
    )

    # Build result dict keyed by eval_metric UUID using defaultdict
    result = defaultdict(lambda: {"name": None, "eval_id": None, "evaluations": []})

    for row in per_metric_scores:
        eval_id = str(row["eval_metric__id"])

        # Set name and eval_id only once per eval_id
        if result[eval_id]["name"] is None:
            result[eval_id]["name"] = (
                row["eval_metric__template__name"] or f"Eval {eval_id[:8]}"
            )
            result[eval_id]["eval_id"] = eval_id

        # Append evaluation data
        result[eval_id]["evaluations"].append(
            {
                "trial_id": str(row["trial_item__trial__id"]),
                "trial_number": row["trial_item__trial__trial_number"],
                "trial_name": _get_trial_name(row["trial_item__trial__trial_number"]),
                "score": row["avg_score"],
            }
        )

    return dict(result)


def get_best_trial(
    optimization_run: OptimizeDataset,
) -> Optional[DatasetOptimizationTrial]:
    """
    Get the best performing trial for an optimization run.
    """
    trials = optimization_run.trials.filter(is_baseline=False)
    if not trials.exists():
        return None

    return trials.order_by("-average_score").first()


def finalize_optimization_run(optimization_run: OptimizeDataset) -> None:
    """
    Finalize an optimization run by setting the best score and marking as completed.
    """
    best_trial = get_best_trial(optimization_run)
    baseline_trial = optimization_run.trials.filter(is_baseline=True).first()

    update_fields = ["status"]

    if best_trial:
        optimization_run.best_score = best_trial.average_score
        update_fields.append("best_score")

        # Store best prompt in optimized_k_prompts
        if best_trial.prompt:
            optimization_run.optimized_k_prompts = [best_trial.prompt]
            update_fields.append("optimized_k_prompts")

    if baseline_trial:
        optimization_run.baseline_score = baseline_trial.average_score
        update_fields.append("baseline_score")

    optimization_run.status = OptimizeDataset.StatusType.COMPLETED
    # Note: Don't include updated_at in update_fields - it's auto_now and Django updates it automatically
    optimization_run.save(update_fields=update_fields)

    logger.info(
        "Finalized optimization run",
        run_id=str(optimization_run.id),
        best_score=optimization_run.best_score,
        baseline_score=optimization_run.baseline_score,
    )
