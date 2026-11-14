"""Service layer for optimization operations — shared by views and ai_tools."""

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ServiceError:
    message: str
    code: str = "ERROR"


VALID_ALGORITHMS = {
    "random_search",
    "bayesian",
    "metaprompt",
    "protegi",
    "promptwizard",
    "gepa",
}


def start_optimization_run(opt_run):
    """Create run steps and start the existing dataset optimization workflow."""
    from model_hub.utils.dataset_optimization import create_dataset_optimization_steps
    from tfc.temporal.dataset_optimization.client import (
        start_dataset_optimization_workflow,
    )

    create_dataset_optimization_steps(str(opt_run.id))
    try:
        workflow_id = start_dataset_optimization_workflow(str(opt_run.id))
    except Exception as exc:
        logger.exception(
            "Failed to start dataset optimization workflow",
            optimization_id=str(opt_run.id),
        )
        opt_run.mark_as_failed(error_message=str(exc))
        return ServiceError(str(exc), "WORKFLOW_START_FAILED")

    return workflow_id


def create_optimization_run(
    *,
    name,
    column_id,
    algorithm,
    algorithm_config,
    organization,
    workspace,
    eval_template_ids=None,
):
    """Create a new optimization run.

    Returns:
        dict with optimization info or ServiceError
    """
    from model_hub.models.develop_dataset import Column
    from model_hub.models.evals_metric import UserEvalMetric
    from model_hub.models.optimize_dataset import OptimizeDataset

    if algorithm not in VALID_ALGORITHMS:
        return ServiceError(
            f"Invalid algorithm '{algorithm}'. Valid: {', '.join(sorted(VALID_ALGORITHMS))}",
            "VALIDATION_ERROR",
        )

    # Validate column
    try:
        column = Column.objects.get(id=column_id, deleted=False)
    except Column.DoesNotExist:
        return ServiceError(f"Column {column_id} not found.", "NOT_FOUND")

    dataset = column.dataset

    # Create optimization run
    opt_run = OptimizeDataset.objects.create(
        name=name,
        optimize_type="PromptTemplate",
        environment="Training",
        version="v1",
        status=OptimizeDataset.StatusType.PENDING,
        optimizer_algorithm=algorithm,
        optimizer_config=algorithm_config,
        column=column,
        organization=organization,
        workspace=workspace,
    )

    if eval_template_ids:
        opt_run.user_eval_template_ids.set(
            UserEvalMetric.objects.filter(
                dataset=dataset,
                id__in=eval_template_ids,
                deleted=False,
            )
        )

    # Start workflow
    workflow_result = start_optimization_run(opt_run)
    workflow_started = not isinstance(workflow_result, ServiceError)

    return {
        "optimization_id": str(opt_run.id),
        "name": opt_run.name,
        "algorithm": algorithm,
        "dataset_name": dataset.name,
        "column": column,
        "status": opt_run.status,
        "workflow_started": workflow_started,
    }
