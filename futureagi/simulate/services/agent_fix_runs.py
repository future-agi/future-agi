"""Start, stop and close "Fix my agent" runs, and make a candidate the default."""

from collections import Counter
from collections.abc import Mapping
from hashlib import sha256
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
from simulate.selectors.agent_fix import (
    FINISHED_RUN_STATES,
    failing_sub_goals_by_scenario,
    launch_blocker,
)
from simulate.services.agent_fix_agents import agent_kind
from simulate.services.harness_agent_versions import discard_agent_version
from simulate.services.harness_usage import require_agent_fix_usage
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


# A third of the failing cases are held back, once there are enough for a third to tell:
# 4 held back pass a large real fix 87% of the time, 2 only 56%.
HELD_BACK_SHARE = 3
HELD_BACK_MIN_CASES = 12


def split_held_back(seed: str, failing: Mapping[str, list[str]]) -> list[str]:
    """The failing cases the worker never sees, so the verdict can prove the fix holds beyond
    the cases it was made on: one in three, picked by a stable hash of ``seed`` and the key,
    and never the last case left to search on for a failing sub-goal. None below twelve."""
    cases = [key for key, names in failing.items() if names]
    if len(cases) < HELD_BACK_MIN_CASES:
        return []
    searched = Counter(name for key in cases for name in failing[key])
    held: list[str] = []
    for key in sorted(
        cases, key=lambda key: sha256(f"{seed}:{key}".encode()).hexdigest()
    ):
        if len(held) == len(cases) // HELD_BACK_SHARE:
            break
        if all(searched[name] > 1 for name in failing[key]):
            held.append(key)
            searched.subtract(failing[key])
    return held


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
    # Usage is checked below, to answer with the usage refusal itself.
    if blocked := launch_blocker(source_run, usage=False):
        raise blocked
    kind = agent_kind(environment)
    if refused := sorted(set(surfaces) - kind.surfaces):
        raise HostedHarnessError(
            "fix_surface_not_allowed",
            f"a fix cannot change this agent's {', '.join(refused)}",
            status_code=422,
        )
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
    by_scenario = failing_sub_goals_by_scenario(source_run, target_scenario_keys)
    failing = list(
        dict.fromkeys(name for names in by_scenario.values() for name in names)
    )
    if not failing:
        raise HostedHarnessError(
            "fix_targets_passed",
            "nothing failed on the chosen scenarios in this run",
            status_code=422,
        )
    request = FixRequest(
        target_scenario_keys=target_scenario_keys,
        held_back_scenario_keys=split_held_back(str(source_run.id), by_scenario),
        failing_sub_goals=failing,
        surfaces=surfaces,
        locked_paths=locked_paths,
        budget_usd=budget_usd,
        max_minutes=max_minutes,
    )
    # The engine's model calls bill AI credits. Each check run checks its call rail when it starts.
    require_agent_fix_usage(str(environment.organization_id))
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
            transaction.on_commit(lambda: _start_workflow(fix_run.id))
    except IntegrityError as exc:
        if "uniq_active_agent_fix_per_environment" not in str(exc):
            raise
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
    fix_run.refresh_from_db()
    return fix_run


def _start_workflow(fix_run_id: Any) -> None:
    """Start the fix's workflow; a fix whose workflow cannot start ends failed at once, so
    it never holds the environment's one active fix."""
    try:
        start_agent_fix_workflow(str(fix_run_id))
    except Exception:
        logger.exception("agent_fix_workflow_start_failed", fix_run=str(fix_run_id))
        close_fix_run(
            fix_run_id,
            AgentFixRun.State.FAILED,
            failure={
                "code": "fix_dispatch_failed",
                "message": "the fix could not be scheduled; try again",
            },
        )


def request_stop(fix_run: AgentFixRun) -> AgentFixRun:
    """Ask a running fix to stop. The workflow kills the sandbox and closes the run."""
    with transaction.atomic():
        locked = AgentFixRun.no_workspace_objects.select_for_update().get(id=fix_run.id)
        if locked.state not in AgentFixRun.ACTIVE_STATES:
            return locked
        locked.state = AgentFixRun.State.STOPPING
        locked.save(update_fields=["state", "updated_at"])
        transaction.on_commit(lambda: _signal_stop(locked.id))
    return locked


def _signal_stop(fix_run_id: Any) -> None:
    """Tell the workflow to stop; without a workflow to tell, the fix is closed here."""
    try:
        cancel_agent_fix_workflow(str(fix_run_id))
    except Exception:
        logger.exception("agent_fix_stop_signal_failed", fix_run=str(fix_run_id))
        close_fix_run(fix_run_id, AgentFixRun.State.STOPPED)


def close_fix_run(
    fix_run_id: Any,
    state: AgentFixRun.State,
    *,
    failure: dict[str, str] | None = None,
) -> AgentFixRun:
    """Move a fix run to its ending once. The sandbox's token stops working, open
    check runs are canceled, and rejected candidates are set aside (never deleted). The
    cleanup runs on every call, so a close that failed halfway finishes on the next."""
    with transaction.atomic():
        locked = AgentFixRun.no_workspace_objects.select_for_update().get(id=fix_run_id)
        if locked.state in AgentFixRun.ACTIVE_STATES:
            locked.state = state
            locked.failure = failure
            locked.finished_at = timezone.now()
            locked.token_hash = ""
            locked.save(
                update_fields=[
                    "state",
                    "failure",
                    "finished_at",
                    "token_hash",
                    "updated_at",
                ]
            )
            logger.info("agent_fix_run_closed", fix_run=str(locked.id), state=state)
    _cancel_open_check_runs(locked)
    _set_aside_rejected(locked)
    return locked


def _cancel_open_check_runs(fix_run: AgentFixRun) -> None:
    open_runs = HostedHarnessJob.no_workspace_objects.filter(fix_run=fix_run).exclude(
        state__in=FINISHED_RUN_STATES
    )
    for run in open_runs:
        request_cancellation(run, "fix_ended")
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


def require_shippable(candidate: AgentFixCandidate) -> None:
    """Refuse to ship a candidate before its fix finished, or one the fix did not prove."""
    if candidate.fix_run.state in AgentFixRun.ACTIVE_STATES:
        raise HostedHarnessError(
            "fix_run_active", "wait for the fix to finish", status_code=409
        )
    # Only the winner went through the final check; a finalist passed its screens only.
    if candidate.outcome != AgentFixCandidate.Outcome.WINNER:
        raise HostedHarnessError(
            "fix_candidate_not_proven",
            "only the fix's winner can ship",
            status_code=422,
        )


def make_candidate_default(candidate: AgentFixCandidate, *, user: Any) -> str:
    """Make a finished fix's winner the environment's default, on the user's word."""
    require_shippable(candidate)
    fix_run = candidate.fix_run
    outcome = agent_kind(fix_run.environment).make_default(candidate, user=user)
    if outcome not in ("activated", "already_default"):
        raise HostedHarnessError(
            "fix_candidate_not_activated",
            f"the version could not become the default: {outcome}",
            status_code=409,
        )
    return outcome
