"""Start, stop and close "Fix my agent" runs, and make a candidate the default."""

from typing import Any

import structlog
from django.db import IntegrityError, transaction
from django.utils import timezone

from simulate.models import (
    AgentFixCandidate,
    AgentFixRun,
    HostedHarnessJob,
)
from simulate.pydantic_schemas.agent_fix import FixRequest, Surface
from simulate.selectors.agent_fix import failing_sub_goals
from simulate.services.agent_fix_agents import agent_kind
from simulate.services.harness_agent_versions import discard_agent_version
from simulate.services.hosted_harness import (
    HostedHarnessError,
    request_cancellation,
)
from simulate.temporal.client import (
    cancel_agent_fix_workflow,
    cancel_hosted_harness_gateway_workflow,
    start_agent_fix_workflow,
)

logger = structlog.get_logger(__name__)

_TERMINAL_RUN_STATES = {
    HostedHarnessJob.State.COMPLETED,
    HostedHarnessJob.State.FAILED,
    HostedHarnessJob.State.CANCELED,
}


def launch_fix_run(
    source_run: HostedHarnessJob,
    *,
    target_scenario_keys: list[str],
    surfaces: list[Surface],
    locked_paths: list[str],
    budget_usd: float,
    max_minutes: int,
    user: Any,
) -> AgentFixRun:
    """Start fixing the agent of a finished run on the scenarios the user chose."""
    environment = source_run.environment
    if environment is None:
        raise HostedHarnessError(
            "fix_source_not_a_run", "Fix my agent starts from a run", status_code=422
        )
    if source_run.state not in _TERMINAL_RUN_STATES:
        raise HostedHarnessError(
            "fix_source_running", "wait for the run to finish", status_code=409
        )
    kind = agent_kind(environment)
    kind.require_available()
    base_version_id = kind.base_version_id(source_run)
    ran = set(
        source_run.scenario_executions.values_list(
            "source_scenario__scenario_key", flat=True
        )
    )
    if unknown := [key for key in target_scenario_keys if key not in ran]:
        raise HostedHarnessError(
            "fix_targets_unknown",
            f"these scenarios were not in the run: {', '.join(unknown)}",
            status_code=400,
        )
    failing = failing_sub_goals(source_run, target_scenario_keys)
    if not failing:
        raise HostedHarnessError(
            "fix_targets_passed",
            "nothing failed on the chosen scenarios in this run",
            status_code=422,
        )
    request = FixRequest(
        target_scenario_keys=target_scenario_keys,
        failing_sub_goals=failing,
        surfaces=surfaces,
        locked_paths=locked_paths,
        budget_usd=budget_usd,
        max_minutes=max_minutes,
    )
    try:
        with transaction.atomic():
            fix_run = AgentFixRun.no_workspace_objects.create(
                organization_id=environment.organization_id,
                workspace_id=environment.workspace_id,
                environment=environment,
                source_run=source_run,
                base_agent_version_id=base_version_id,
                created_by=user,
                request=request.model_dump(mode="json"),
            )
            transaction.on_commit(lambda: start_agent_fix_workflow(str(fix_run.id)))
    except IntegrityError as exc:
        raise HostedHarnessError(
            "fix_run_active",
            "this environment already has a fix in progress",
            status_code=409,
        ) from exc
    logger.info(
        "agent_fix_run_launched",
        fix_run=str(fix_run.id),
        environment=str(environment.id),
    )
    return fix_run


def request_stop(fix_run: AgentFixRun) -> AgentFixRun:
    """Ask a running fix to stop. The workflow kills the sandbox and closes the run."""
    with transaction.atomic():
        locked = AgentFixRun.no_workspace_objects.select_for_update().get(id=fix_run.id)
        if locked.state not in AgentFixRun.ACTIVE_STATES:
            return locked
        locked.state = AgentFixRun.State.STOPPING
        locked.save(update_fields=["state", "updated_at"])
        transaction.on_commit(lambda: cancel_agent_fix_workflow(str(locked.id)))
    return locked


def close_fix_run(
    fix_run_id: Any,
    state: AgentFixRun.State,
    *,
    failure: dict[str, str] | None = None,
) -> AgentFixRun:
    """Move a fix run to its ending once. The sandbox's token stops working, open
    check runs are canceled, and rejected candidates are set aside (never deleted)."""
    with transaction.atomic():
        locked = AgentFixRun.no_workspace_objects.select_for_update().get(id=fix_run_id)
        if locked.state not in AgentFixRun.ACTIVE_STATES:
            return locked
        locked.state = state
        locked.failure = failure
        locked.finished_at = timezone.now()
        locked.token_hash = ""
        locked.fence_hash = ""
        locked.save(
            update_fields=[
                "state",
                "failure",
                "finished_at",
                "token_hash",
                "fence_hash",
                "updated_at",
            ]
        )
    _cancel_open_check_runs(locked)
    _set_aside_rejected(locked)
    logger.info("agent_fix_run_closed", fix_run=str(locked.id), state=state)
    return locked


def _cancel_open_check_runs(fix_run: AgentFixRun) -> None:
    open_runs = HostedHarnessJob.no_workspace_objects.filter(fix_run=fix_run).exclude(
        state__in=_TERMINAL_RUN_STATES
    )
    for run in open_runs:
        request_cancellation(run, "user_canceled")
        try:
            cancel_hosted_harness_gateway_workflow(str(run.id))
        except Exception:  # noqa: BLE001 - the run's own deadline still ends it
            logger.exception("agent_fix_check_run_cancel_failed", run=str(run.id))


def _set_aside_rejected(fix_run: AgentFixRun) -> None:
    kept = (AgentFixCandidate.Outcome.FINALIST, AgentFixCandidate.Outcome.WINNER)
    for candidate in fix_run.candidates.exclude(outcome__in=kept).select_related(
        "agent_version"
    ):
        try:
            discard_agent_version(
                candidate.agent_version, by_environment=fix_run.environment_id
            )
        except HostedHarnessError as exc:
            # Another environment holds it; it stays listed, which is harmless.
            logger.info(
                "agent_fix_candidate_kept", candidate=str(candidate.id), reason=exc.code
            )


def make_candidate_default(candidate: AgentFixCandidate, *, user: Any) -> str:
    """Make a finished run's proven candidate the environment's default, on the user's word."""
    fix_run = candidate.fix_run
    if fix_run.state in AgentFixRun.ACTIVE_STATES:
        raise HostedHarnessError(
            "fix_run_active", "wait for the fix to finish", status_code=409
        )
    if candidate.outcome not in (
        AgentFixCandidate.Outcome.WINNER,
        AgentFixCandidate.Outcome.FINALIST,
    ):
        raise HostedHarnessError(
            "fix_candidate_not_proven",
            "only a proven candidate can become the default",
            status_code=422,
        )
    outcome = agent_kind(fix_run.environment).make_default(candidate, user=user)
    if outcome not in ("activated", "already_default"):
        raise HostedHarnessError(
            "fix_candidate_not_activated",
            f"the version could not become the default: {outcome}",
            status_code=409,
        )
    return outcome
