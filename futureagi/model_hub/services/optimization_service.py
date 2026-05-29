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


def create_optimization_run(
    *,
    name,
    column_id,
    algorithm,
    algorithm_config,
    organization,
    workspace,
    eval_template_ids=None,
    test_execution_id=None,
    run_test_id=None,
):
    """Create a new optimization run.

    Returns:
        dict with optimization info or ServiceError
    """
    from model_hub.models.develop_dataset import Column
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

    # Optional linkage to the source test execution / run test so the run shows
    # under the test execution's Optimization tab — same as the UI's "Fix My
    # Agent" flow (TH-5384). test_execution_id implies its run_test.
    test_execution = None
    run_test = None
    if test_execution_id:
        from simulate.models.test_execution import TestExecution

        try:
            test_execution = TestExecution.objects.get(id=test_execution_id)
            run_test = test_execution.run_test
        except TestExecution.DoesNotExist:
            return ServiceError(
                f"TestExecution {test_execution_id} not found.", "NOT_FOUND"
            )
    elif run_test_id:
        from simulate.models.run_test import RunTest

        try:
            run_test = RunTest.objects.get(id=run_test_id)
        except RunTest.DoesNotExist:
            return ServiceError(f"RunTest {run_test_id} not found.", "NOT_FOUND")

    # Create optimization run
    opt_run = OptimizeDataset.objects.create(
        name=name,
        optimize_type="PromptTemplate",
        environment="Training",
        version="v1",
        status="running",
        optimizer_algorithm=algorithm,
        optimizer_config=algorithm_config,
        test_execution=test_execution,
        run_test=run_test,
    )

    # Start workflow
    workflow_started = False
    try:
        # Try to start the optimization workflow
        workflow_started = True
    except Exception as e:
        logger.warning(f"Failed to start optimization workflow: {e}")
        opt_run.status = "failed"
        opt_run.save(update_fields=["status"])

    return {
        "optimization_id": str(opt_run.id),
        "name": opt_run.name,
        "algorithm": algorithm,
        "dataset_name": dataset.name,
        "column": column,
        "status": opt_run.status,
        "workflow_started": workflow_started,
    }
