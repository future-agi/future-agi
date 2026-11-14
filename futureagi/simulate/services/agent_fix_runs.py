"""Start, stop and close "Fix my agent" runs, and make a candidate the default."""

from collections import Counter
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from model_hub.models.develop_dataset import Row
from simulate.models import (
    AgentFixCandidate,
    AgentFixRun,
    HarnessSuiteMember,
    HostedHarnessJob,
    HostedHarnessScenario,
    Scenarios,
)
from simulate.pydantic_schemas.agent_fix import (
    HELD_BACK_SHARE,
    HELD_BACK_VERIFIED_MIN,
    MARGIN_PCT_DEFAULT,
    VARIATIONS_PER_CASE,
    EvalGrading,
    FixRequest,
    Surface,
)
from simulate.selectors.agent_fix import (
    FINISHED_RUN_STATES,
    default_max_minutes,
    eval_review,
    fewest_failing_cases,
    is_retryable,
    launch_blocker,
    model_key,
    models_called,
    overridable_regressions,
    recent_lost_models,
    scenario_results,
    source_run_usage,
    variations_blocker,
    written_variations,
)
from simulate.services.agent_fix_agents import agent_kind
from simulate.services.alk_simulate_ingestion import GROUPED_SCENARIOS_ORIGIN
from simulate.services.harness_agent_versions import discard_agent_version
from simulate.services.harness_usage import (
    require_agent_fix_usage,
    require_harness_authoring,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    request_cancellation,
)
from simulate.services.hosted_harness_conversation import CHAT_CHECKPOINT_MAX_SCENARIOS
from simulate.temporal.client import (
    cancel_agent_fix_workflow,
    cancel_hosted_harness_gateway_workflow,
    start_agent_fix_workflow,
)

logger = structlog.get_logger(__name__)


HELD_BACK_MIN_CASES = HELD_BACK_VERIFIED_MIN * HELD_BACK_SHARE


def split_held_back(
    seed: str,
    failing: Mapping[str, list[str]],
    parents: Mapping[str, str] | None = None,
) -> list[str]:
    """The failing cases the worker never sees, so the verdict can prove the fix holds beyond
    the cases it was made on: one in three, picked by a stable hash of ``seed`` and the key,
    and never the last cases left to search on for a failing sub-goal. None below twelve.

    A variation (a key of ``parents``) goes with the case it varies: a case and its
    variations are held back together or not at all."""
    parents = parents or {}
    cases = [key for key, names in failing.items() if names]
    if len(cases) < HELD_BACK_MIN_CASES:
        return []
    families: dict[str, list[str]] = {}
    for key in cases:
        families.setdefault(parents.get(key, key), []).append(key)
    quota = len(cases) // HELD_BACK_SHARE
    searched = Counter(name for key in cases for name in failing[key])
    held: list[str] = []
    for parent in sorted(
        families, key=lambda key: sha256(f"{seed}:{key}".encode()).hexdigest()
    ):
        if len(held) == quota:
            break
        family = families[parent]
        names = Counter(name for key in family for name in failing[key])
        if len(held) + len(family) <= quota and all(
            searched[name] > count for name, count in names.items()
        ):
            held.extend(family)
            searched.subtract(names)
    return held


def launch_fix_run(
    source_run: HostedHarnessJob,
    *,
    target_scenario_keys: list[str],
    surfaces: list[Surface],
    locked_paths: list[str],
    budget_usd: float,
    max_minutes: int | None,
    user: Any,
    protected_scenario_keys: Sequence[str] = (),
    allowed_models: Sequence[str] | None = None,
    reply_latency_margin_pct: int | None = None,
    cost_margin_pct: int | None = None,
    cost_limit_usd: float | None = None,
) -> AgentFixRun:
    """Start fixing the agent of a finished run on the scenarios the user chose; without
    ``max_minutes``, within the time the launch form suggests for the run's suite,
    without ``allowed_models``, locked to the models the run's spans show, and without a
    margin, at the default margin."""
    environment = source_run.environment
    if environment is None:
        raise HostedHarnessError(
            "fix_source_not_a_run", "Epoch starts from a run", status_code=422
        )
    # Usage is checked below, to answer with the usage refusal itself.
    if blocked := launch_blocker(source_run, usage=False):
        raise blocked
    kind = agent_kind(environment)
    if refused := sorted(set(surfaces) - kind.surfaces):
        raise HostedHarnessError(
            "fix_surface_not_allowed",
            f"an epoch cannot change this agent's {', '.join(refused)}",
            status_code=422,
        )
    base_version_id = kind.base_version_id(source_run)
    ran = {one.key: one for one in scenario_results(source_run)}
    chosen = [*target_scenario_keys, *protected_scenario_keys]
    if unknown := [key for key in chosen if key not in ran]:
        raise HostedHarnessError(
            "fix_targets_unknown",
            f"these scenarios were not in the run: {', '.join(unknown)}",
            status_code=400,
        )
    if both := sorted(set(target_scenario_keys) & set(protected_scenario_keys)):
        raise HostedHarnessError(
            "fix_protected_is_target",
            f"a scenario to fix cannot also be protected: {', '.join(both)}",
            status_code=400,
        )
    # A protected case is the behavior to keep, so it must have been judged and held.
    if unproven := [key for key in protected_scenario_keys if not ran[key].passed]:
        raise HostedHarnessError(
            "fix_protected_not_passed",
            "only a scenario that passed in the run can be protected: "
            f"{', '.join(unproven)}",
            status_code=400,
        )
    by_scenario = {key: ran[key].failing_sub_goals for key in target_scenario_keys}
    failing = list(
        dict.fromkeys(name for names in by_scenario.values() for name in names)
    )
    if not failing:
        raise HostedHarnessError(
            "fix_targets_passed",
            "nothing failed on the chosen scenarios in this run",
            status_code=422,
        )
    failing_cases = sum(1 for names in by_scenario.values() if names)
    # Below the floor the fix first writes variations of each failing case.
    variations = failing_cases < settings.AGENT_FIX_MIN_FAILING_CASES
    if failing_cases < fewest_failing_cases():
        raise HostedHarnessError(
            "fix_targets_too_few",
            f"an epoch needs at least {settings.AGENT_FIX_MIN_FAILING_CASES} failing cases "
            f"to be proven; these have {failing_cases}, too few to reach it even with "
            f"{VARIATIONS_PER_CASE} variations of each",
            status_code=422,
        )
    if variations:
        if blocked := variations_blocker(source_run):
            raise blocked
        _require_variation_room(source_run, failing_cases)
    if max_minutes is None:
        max_minutes = default_max_minutes(
            len(ran) + (VARIATIONS_PER_CASE * failing_cases if variations else 0)
        )
    usage = source_run_usage(source_run)
    seen = models_called(usage)
    allowed = list(dict.fromkeys(seen if allowed_models is None else allowed_models))
    # A fix that sees the agent's models with none allowed, or without one the run
    # called, would refuse every candidate once the base version's run showed it.
    if seen and not allowed:
        raise HostedHarnessError(
            "fix_models_empty",
            "allow at least one model: this agent's model calls are traced, and each"
            " must name an allowed model",
            status_code=400,
        )
    kept = {model_key(model) for model in allowed}
    if left_out := [model for model in seen if model_key(model) not in kept]:
        raise HostedHarnessError(
            "fix_models_exclude_source",
            "allow every model this run's agent called, or the epoch would refuse every"
            f" candidate: {', '.join(left_out)}",
            status_code=400,
        )
    request = FixRequest(
        target_scenario_keys=target_scenario_keys,
        protected_scenario_keys=list(protected_scenario_keys),
        held_back_scenario_keys=split_held_back(str(source_run.id), by_scenario),
        failing_sub_goals=failing,
        surfaces=surfaces,
        locked_paths=locked_paths,
        budget_usd=budget_usd,
        max_minutes=max_minutes,
        variations_pending=variations,
        allowed_models=allowed,
        reply_latency_margin_pct=(
            MARGIN_PCT_DEFAULT
            if reply_latency_margin_pct is None
            else reply_latency_margin_pct
        ),
        cost_margin_pct=MARGIN_PCT_DEFAULT
        if cost_margin_pct is None
        else cost_margin_pct,
        cost_limit_usd=cost_limit_usd,
        observes_models=bool(seen),
        meters_agent_cost=bool(usage),
    )
    # The engine's model calls bill AI credits. Each check run checks its call rail when it starts.
    require_agent_fix_usage(str(environment.organization_id))
    if variations:
        # The builder's turn that writes the variations bills as authoring.
        require_harness_authoring(str(environment.organization_id))
    return _create_fix_run(
        source_run,
        base_version_id=base_version_id,
        request=request.model_dump(mode="json"),
        user=user,
    )


def _require_variation_room(source_run: HostedHarnessJob, failing_cases: int) -> None:
    """Refuse variations that would not fit: the builder saves the environment's suite with
    them, the fix's suite holds the run's suite with them, and one run plays every failing
    case and its variations once."""
    added = VARIATIONS_PER_CASE * failing_cases
    suites = (source_run.suite_version_id, source_run.environment.suite_version_id)
    largest = max(
        HarnessSuiteMember.no_workspace_objects.filter(suite_version_id=one).count()
        for one in suites
    )
    if largest + added > CHAT_CHECKPOINT_MAX_SCENARIOS:
        raise HostedHarnessError(
            "scenario_limit_exceeded",
            f"the {added} variations would take the suite past"
            f" {CHAT_CHECKPOINT_MAX_SCENARIOS} scenarios",
            status_code=422,
        )
    max_executions = int(getattr(settings, "HARNESS_MAX_EXECUTIONS_PER_RUN", 200))
    if failing_cases + added > max_executions:
        raise HostedHarnessError(
            "run_too_large",
            f"the run on the failing cases and their variations would pass"
            f" {max_executions} executions",
            status_code=422,
        )


def retry_fix_run(failed: AgentFixRun, *, user: Any) -> AgentFixRun:
    """Start a failed fix again: a new fix with its request, linked to it, so the check runs
    it finished are reused instead of paid for twice. A fix that wrote variations keeps
    them, on the same suite version."""
    if not is_retryable(failed):
        raise HostedHarnessError(
            "fix_not_retryable",
            "Only a failed epoch can be retried, and only once.",
            status_code=409,
        )
    source_run = failed.source_run
    if source_run is None:
        raise HostedHarnessError(
            "fix_source_deleted",
            "the run this epoch started from was deleted",
            status_code=409,
        )
    if blocked := launch_blocker(source_run, usage=False):
        raise blocked
    require_agent_fix_usage(str(failed.organization_id))
    # The models the failed fix lost recently, with when: the retry's first launch starts
    # without them. Its last error stays with it.
    lost_models = recent_lost_models(failed.engine_notes)
    return _create_fix_run(
        source_run,
        base_version_id=failed.base_agent_version_id,
        request=failed.request,
        user=user,
        suite_version_id=failed.suite_version_id,
        retried_from=failed,
        engine_notes={"lost_models": lost_models, "last_error": None}
        if lost_models
        else None,
    )


def _create_fix_run(
    source_run: HostedHarnessJob,
    *,
    base_version_id: Any,
    request: dict[str, Any],
    user: Any,
    suite_version_id: Any = None,
    retried_from: AgentFixRun | None = None,
    engine_notes: dict[str, Any] | None = None,
) -> AgentFixRun:
    """The new fix, its workflow started once it is committed; one active fix per environment."""
    environment = source_run.environment
    try:
        with transaction.atomic():
            fix_run = AgentFixRun.no_workspace_objects.create(
                organization_id=environment.organization_id,
                workspace_id=environment.workspace_id,
                environment=environment,
                source_run=source_run,
                base_agent_version_id=base_version_id,
                suite_version_id=suite_version_id,
                retried_from=retried_from,
                created_by=user,
                request=request,
                engine_notes=engine_notes,
            )
            transaction.on_commit(lambda: _start_workflow(fix_run.id))
    except IntegrityError as exc:
        if "uniq_active_agent_fix_per_environment" not in str(exc):
            raise
        raise HostedHarnessError(
            "fix_run_active",
            "this environment already has an epoch in progress",
            status_code=409,
        ) from exc
    logger.info(
        "agent_fix_run_launched",
        fix_run=str(fix_run.id),
        environment=str(environment.id),
        retried_from=str(retried_from.id) if retried_from else None,
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
                "message": "the epoch could not be scheduled; try again",
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
    check runs are canceled, rejected candidates are set aside (never deleted), and the
    dataset rows its variations' runs added are removed. The cleanup runs on every call,
    so a close that failed halfway finishes on the next."""
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
    drop_variation_rows(locked)
    # Imported here: the variations step imports this module.
    from simulate.services.agent_fix_variations import stop_writing

    stop_writing(locked)
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
        # One only its must-pass check refused may still become the default on a reason.
        if overridable_regressions(candidate):
            continue
        try:
            discard_agent_version(
                candidate.agent_version, by_environment=fix_run.environment_id
            )
        except HostedHarnessError as exc:
            # Another environment holds it; it stays listed, which is harmless.
            logger.info(
                "agent_fix_candidate_kept", candidate=str(candidate.id), reason=exc.code
            )


def drop_variation_rows(fix_run: AgentFixRun) -> bool:
    """Remove the dataset rows a run of the fix's variations added, so the environment's
    dataset is as it was. Each variation is unbound too: a later run of it (a retry's)
    binds a row of its own. Only the newest fix on a private suite owns its rows: a retry
    runs the same variations, so an older fix's late close leaves them to it.

    True once nothing the fix owns is left bound. Fail open: the fix's ending stands, and
    the sweep tries again (sweep_variation_rows)."""
    variations = written_variations(fix_run.suite_version)
    if not variations:
        return True
    if AgentFixRun.no_workspace_objects.filter(
        suite_version_id=fix_run.suite_version_id, created_at__gt=fix_run.created_at
    ).exists():
        return True
    try:
        with transaction.atomic():
            bound = list(
                HostedHarnessScenario._base_manager.filter(
                    job_id=fix_run.environment_id,
                    scenario_key__in=variations,
                    scenario__isnull=False,
                )
            )
            if not bound:
                return True
            now = timezone.now()
            rows = [one.dataset_row_id for one in bound if one.dataset_row_id]
            Row.all_objects.filter(id__in=rows).update(deleted=True, deleted_at=now)
            # A legacy environment gave each persona a scenario of its own, linked to
            # the run test; a grouped one's shared scenario stays.
            legacy = list(
                Scenarios.all_objects.filter(
                    id__in=[one.scenario_id for one in bound if not one.dataset_row_id]
                ).exclude(metadata__origin=GROUPED_SCENARIOS_ORIGIN)
            )
            Scenarios.all_objects.filter(id__in=[one.id for one in legacy]).update(
                deleted=True, deleted_at=now
            )
            if legacy and fix_run.environment.run_test_id:
                fix_run.environment.run_test.scenarios.remove(*legacy)
            for scenario in Scenarios.all_objects.filter(
                id__in={one.scenario_id for one in bound if one.dataset_row_id}
            ).select_related("dataset"):
                scenario.metadata = {
                    **(scenario.metadata or {}),
                    "persona_count": scenario.dataset.row_set.filter(
                        deleted=False
                    ).count(),
                }
                scenario.save(update_fields=["metadata", "updated_at"])
            HostedHarnessScenario._base_manager.filter(
                id__in=[one.id for one in bound]
            ).update(scenario=None, dataset_row=None, updated_at=now)
        logger.info(
            "agent_fix_variation_rows_dropped", fix_run=str(fix_run.id), rows=len(bound)
        )
        return True
    except Exception:
        logger.exception("agent_fix_variation_rows_kept", fix_run=str(fix_run.id))
        return False


def require_shippable(candidate: AgentFixCandidate, *, override_reason: str) -> None:
    """Refuse to ship a candidate before its fix finished, or one the fix did not prove. A
    candidate only its must-pass check refused ships on the user's reason, and needs one."""
    if candidate.fix_run.state in AgentFixRun.ACTIVE_STATES:
        raise HostedHarnessError(
            "fix_run_active", "wait for the fix to finish", status_code=409
        )
    # Only the winner went through the final check; a finalist passed its screens only.
    if candidate.outcome == AgentFixCandidate.Outcome.WINNER:
        # Its evals read the winner's final check; it ships once they have.
        if eval_review(candidate.fix_run).grading == EvalGrading.PENDING:
            raise HostedHarnessError(
                "fix_grading_pending",
                "Platform evals are still scoring this candidate. You can ship it once"
                " they finish.",
                status_code=409,
            )
        if override_reason:
            raise HostedHarnessError(
                "fix_override_not_needed",
                "the epoch's winner ships without a reason",
                status_code=422,
            )
        return
    if not overridable_regressions(candidate):
        raise HostedHarnessError(
            "fix_candidate_not_proven",
            "only the epoch's winner can ship; a candidate only the must-pass check"
            " refused can ship with a reason, unless it regressed a protected case",
            status_code=422,
        )
    if not override_reason:
        raise HostedHarnessError(
            "fix_override_reason_required",
            "this candidate regressed on its must-pass check: give the reason to ship it",
            status_code=422,
        )


def record_override(candidate: AgentFixCandidate, *, reason: str, user: Any) -> None:
    """Record who shipped a refused candidate and why, once: a later ship keeps the first
    reason, which a pull request already carries."""
    if not reason:
        return
    AgentFixCandidate.no_workspace_objects.filter(
        id=candidate.id, override_at__isnull=True
    ).update(override_reason=reason, override_by=user, override_at=timezone.now())
    candidate.refresh_from_db(fields=["override_reason", "override_by", "override_at"])
    logger.info(
        "agent_fix_candidate_overridden",
        candidate=str(candidate.id),
        by=str(candidate.override_by_id),
    )


def base_moved(base: str, current: str | None) -> HostedHarnessError:
    """The refusal to ship a fix whose agent changed since its base: what it proved is no
    longer what it would change. ``base`` and ``current`` name the agent then and now;
    ``current`` is None when the environment has no default version any more."""
    now = (
        f"the agent is now {current}"
        if current is not None
        else "the environment has no default version any more"
    )
    return HostedHarnessError(
        "fix_base_moved",
        f"The agent changed since this epoch started: the epoch was made on {base} and"
        f" {now}. Run the environment on the current version, then run a new epoch.",
        status_code=409,
    )


def make_candidate_default(
    candidate: AgentFixCandidate, *, user: Any, override_reason: str = ""
) -> str:
    """Make a finished fix's winner the environment's default, on the user's word; never
    over a default that moved since the fix's base, whatever the reason given."""
    require_shippable(candidate, override_reason=override_reason)
    fix_run = candidate.fix_run
    base = f"v{fix_run.base_agent_version.version_number}"
    # The default is SET_NULL: with it deleted no agent kind matches the environment,
    # so say the base moved before agent_kind refuses the agent as not editable.
    if fix_run.environment.agent_version_id is None:
        raise base_moved(base, None)
    outcome = agent_kind(fix_run.environment).make_default(candidate, user=user)
    if outcome == "environment_busy":
        raise HostedHarnessError(
            "fix_environment_busy",
            "The environment is being updated; make the version the default again when"
            " the update ends",
            status_code=409,
        )
    if outcome == "base_moved":
        current = HostedHarnessJob.no_workspace_objects.select_related(
            "agent_version"
        ).get(id=fix_run.environment_id)
        # Read again under the activation's lock: the default may be gone by now.
        raise base_moved(
            base,
            (
                f"v{current.agent_version.version_number}"
                if current.agent_version is not None
                else None
            ),
        )
    if outcome not in ("activated", "already_default"):
        raise HostedHarnessError(
            "fix_candidate_not_activated",
            "The version could not become the environment default. Try again.",
            status_code=409,
            details={"outcome": outcome},
        )
    record_override(candidate, reason=override_reason, user=user)
    return outcome
