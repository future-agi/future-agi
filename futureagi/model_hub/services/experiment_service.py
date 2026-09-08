"""Service layer for experiment operations shared by API and internal AI workflows."""

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ServiceError:
    message: str
    code: str = "ERROR"


def create_experiment(
    *, name, dataset_id, column_id, prompt_config, user, user_eval_template_ids=None
):
    """Create an experiment and optionally start a Temporal workflow.

    Returns:
        dict with experiment info or ServiceError
    """
    from model_hub.models.develop_dataset import Column, Dataset
    from model_hub.models.experiments import ExperimentsTable

    # Validate dataset
    try:
        dataset = Dataset.objects.get(id=dataset_id, deleted=False)
    except Dataset.DoesNotExist:
        return ServiceError(f"Dataset {dataset_id} not found.", "NOT_FOUND")

    # Validate column
    try:
        column = Column.objects.get(id=column_id, dataset=dataset, deleted=False)
    except Column.DoesNotExist:
        return ServiceError(f"Column {column_id} not found in dataset.", "NOT_FOUND")

    # Check duplicate name
    if ExperimentsTable.objects.filter(
        name=name,
        dataset=dataset,
        deleted=False,
    ).exists():
        return ServiceError(
            f"An experiment named '{name}' already exists for this dataset.",
            "DUPLICATE_NAME",
        )

    # Create experiment
    experiment = ExperimentsTable.objects.create(
        name=name,
        dataset=dataset,
        column=column,
        prompt_config=prompt_config,
        user=user,
    )

    if user_eval_template_ids:
        from model_hub.models.evals_metric import UserEvalMetric

        templates = UserEvalMetric.objects.filter(id__in=user_eval_template_ids)
        experiment.user_eval_template_ids.set(templates)

    # Start Temporal workflow
    workflow_started = False
    try:
        from tfc.temporal.experiments import start_experiment_workflow

        workflow_id = start_experiment_workflow(
            experiment_id=str(experiment.id),
            max_concurrent_rows=10,
        )
        workflow_started = True
        logger.info(
            f"Started Temporal workflow {workflow_id} for experiment {experiment.id}"
        )
    except Exception as e:
        logger.warning(
            f"Failed to start Temporal workflow for experiment {experiment.id}: {e}. "
            "Will be picked up by periodic task."
        )

    return {
        "id": str(experiment.id),
        "name": experiment.name,
        "dataset_name": dataset.name,
        "column_name": column.name,
        "variant_count": len(prompt_config),
        "status": experiment.status if hasattr(experiment, "status") else "created",
        "workflow_started": workflow_started,
    }
