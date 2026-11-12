"""Independent, creation-time provenance for prompt optimization workers.

Historical runs have no immutable tenant anchor. Never backfill one from their
mutable links: they fail closed until an independently verified migration exists.
"""

from django.db import router, transaction

SCOPE_FAILURE_MESSAGE = (
    "Prompt optimization refused because its tenant scope could not be verified. "
    "Create a new run from an authorized execution."
)


class PromptOptimiserScopeError(ValueError):
    """Worker lineage does not match its independent tenant scope."""


def creation_lineage(run, using):
    from simulate.models import AgentOptimiserRun, TestExecution

    owner = (
        TestExecution.no_workspace_objects.using(using)
        .filter(
            pk=run.test_execution_id,
            run_test__deleted=False,
            agent_optimiser_id=run.agent_optimiser_id,
        )
        .values("run_test_id", "run_test__organization_id", "run_test__workspace_id")
        .first()
    )
    analysis_exists = (
        AgentOptimiserRun.no_workspace_objects.using(using)
        .filter(
            pk=run.agent_optimiser_run_id,
            agent_optimiser_id=run.agent_optimiser_id,
            agent_optimiser__deleted=False,
        )
        .exists()
    )
    if not owner or not owner["run_test__organization_id"] or not analysis_exists:
        raise PromptOptimiserScopeError("Cannot establish prompt optimizer scope.")
    return {
        "organization_id": str(owner["run_test__organization_id"]),
        "workspace_id": str(owner["run_test__workspace_id"])
        if owner["run_test__workspace_id"]
        else None,
        "run_test_id": str(owner["run_test_id"]),
        "test_execution_id": str(run.test_execution_id),
        "agent_optimiser_id": str(run.agent_optimiser_id),
        "agent_optimiser_run_id": str(run.agent_optimiser_run_id),
    }


def load_scoped_prompt_run(run_id):
    """Refuse unverified scope and terminalize active runs without following FKs."""
    from simulate.models import AgentPromptOptimiserRun

    using = router.db_for_write(AgentPromptOptimiserRun)
    refusal = None
    with transaction.atomic(using=using):
        runs = AgentPromptOptimiserRun.no_workspace_objects.using(using)
        run = runs.select_for_update().get(pk=run_id)
        try:
            _validate_prompt_run(run, using)
        except PromptOptimiserScopeError as exc:
            # Never save a caller/replica-loaded instance or invoke lineage capture.
            # Preserve every field except active status/error, including old results.
            runs.filter(
                pk=run_id,
                status__in=[
                    AgentPromptOptimiserRun.Status.PENDING,
                    AgentPromptOptimiserRun.Status.RUNNING,
                ],
            ).update(
                status=AgentPromptOptimiserRun.Status.FAILED,
                error_message=SCOPE_FAILURE_MESSAGE,
            )
            refusal = exc
    # Raise only after the rejection-only update has committed.
    if refusal is not None:
        raise refusal
    return run


def _validate_prompt_run(run, using):
    """Validate scalar links before reading execution data or analysis results."""
    from simulate.models import AgentOptimiser, AgentOptimiserRun, TestExecution

    anchor = run.tenant_lineage
    required = {
        "organization_id",
        "workspace_id",
        "run_test_id",
        "test_execution_id",
        "agent_optimiser_id",
        "agent_optimiser_run_id",
    }
    if (
        not isinstance(anchor, dict)
        or not required.issubset(anchor)
        or not anchor["organization_id"]
    ):
        raise PromptOptimiserScopeError(
            "Historical prompt optimizer has no independent tenant scope."
        )
    for key in ("test_execution_id", "agent_optimiser_id", "agent_optimiser_run_id"):
        if str(getattr(run, key)) != anchor[key]:
            raise PromptOptimiserScopeError(
                "Prompt optimizer lineage is outside its creation scope."
            )
    execution = (
        TestExecution.no_workspace_objects.using(using)
        .filter(
            pk=anchor["test_execution_id"],
            run_test_id=anchor["run_test_id"],
            run_test__organization_id=anchor["organization_id"],
            run_test__workspace_id=anchor["workspace_id"],
            run_test__deleted=False,
            agent_optimiser_id=anchor["agent_optimiser_id"],
        )
        .select_related("run_test__organization", "run_test__workspace")
        .first()
    )
    if execution is None:
        raise PromptOptimiserScopeError(
            "Prompt optimizer execution is outside its tenant scope."
        )
    optimiser = (
        AgentOptimiser.no_workspace_objects.using(using)
        .filter(pk=anchor["agent_optimiser_id"])
        .first()
    )
    analysis = (
        AgentOptimiserRun.no_workspace_objects.using(using)
        .filter(
            pk=anchor["agent_optimiser_run_id"],
            agent_optimiser_id=anchor["agent_optimiser_id"],
        )
        .first()
    )
    if execution is None or optimiser is None or analysis is None:
        raise PromptOptimiserScopeError(
            "Prompt optimizer dependencies are outside its tenant scope."
        )
    # Cache only these validated instances; do not lazily follow the mutable FKs.
    run.test_execution = execution
    run.agent_optimiser = optimiser
    run.agent_optimiser_run = analysis
    return run
