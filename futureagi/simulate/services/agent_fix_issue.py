"""Run Epoch from an Error Feed issue, from the issue's traces to a running epoch.

A job imports a sample of the issue's traces as evidence for one environment, and the
builder drafts them into scenarios. A person reviews the drafts. Run Epoch adds the kept
ones to the environment's suite and runs its current version on them (the baseline).
Once the baseline finishes, AgentFixIssueWorkflow launches the epoch from it with the
launch's own rules, and a refusal becomes the job's ending.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from simulate.models import (
    AgentFixIssueJob,
    AgentFixRun,
    EvidenceImportCase,
    HarnessSuiteMember,
    HostedHarnessJob,
)
from simulate.pydantic_schemas.agent_fix import VARIATIONS_PER_CASE
from simulate.pydantic_schemas.agent_fix_issue import (
    IssueJobEnding,
    IssueJobPhase,
    IssueLaunch,
    SamplePick,
)
from simulate.selectors.agent_fix import (
    FINISHED_RUN_STATES,
    default_max_minutes,
    environment_state_blocker,
    environment_variations_blocker,
    fewest_failing_cases,
    scenario_results,
)
from simulate.selectors.agent_fix_issue import (
    OPEN_ELSEWHERE,
    can_open,
    environment_blocker,
    issue_job_phase,
    issue_scenarios,
    issue_scenarios_max,
    jobs_of_epochs,
    live_issue,
    measuring_job,
    member_units,
    members_of,
    open_job,
    pick_reasons,
    runnable_cases,
)
from simulate.selectors.evidence_import import MAX_SOURCES
from simulate.services.agent_fix_agents import agent_kind
from simulate.services.agent_fix_engine import dispatch_run
from simulate.services.agent_fix_runs import launch_fix_run, require_variation_room
from simulate.services.evidence_drafts import forget, start_drafting
from simulate.services.evidence_import import (
    add_to_suite,
    create_evidence_import,
    discard_evidence_import,
    discard_open_cases,
)
from simulate.services.harness_agent_versions import advisory_xact_lock
from simulate.services.harness_usage import (
    require_agent_fix_usage,
    require_harness_authoring,
    require_harness_call_usage,
    usage_limit_error,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_selected_harness_run,
    request_cancellation,
)
from simulate.services.hosted_harness_gateway import HostedHarnessGateway
from simulate.temporal.client import (
    cancel_hosted_harness_gateway_workflow,
    start_agent_fix_issue_workflow,
)
from tracer.models.trace_error_analysis import TraceErrorGroup

logger = structlog.get_logger(__name__)

# The Run Epoch refusal after which the job cannot go on: its drafts were made on an
# environment that changed since, and they cannot be drafted again in the same import.
_ENDS_THE_JOB = frozenset({"evidence_draft_stale"})
# How long a job may take from its baseline's start to its epoch's, whatever holds it
# up: the run itself, or a launch that keeps failing in a way a later look might outlive.
_BASELINE_PATIENCE = timedelta(hours=12)
STOPPED = IssueJobEnding(code="stopped", message="Stopped")


def _refused(
    code: str, message: str, status_code: int = 409, **details: Any
) -> HostedHarnessError:
    return HostedHarnessError(
        code, message, status_code=status_code, details=details or None
    )


def create_issue_job(
    issue: TraceErrorGroup,
    *,
    environment: HostedHarnessJob,
    environments: QuerySet[HostedHarnessJob],
    member_ids: list[str],
    user: Any,
    endpoint_base_url: str,
) -> AgentFixIssueJob:
    """Start an epoch from ``issue`` on ``environment``: import ``member_ids`` (the
    plan's picks, after any swap) as evidence and ask the builder to draft them.

    Refused while the issue holds an open job, and while a pick repeats a source
    another import of the environment still drafts or reviews: its repeat could not
    join the suite, so it is named for a swap. Nothing is kept of a refused start."""
    if not member_ids or len(member_ids) > MAX_SOURCES:
        raise _refused(
            "issue_epoch_picks",
            f"Pick between 1 and {MAX_SOURCES} traces of the issue.",
            status_code=400,
        )
    if len(set(member_ids)) != len(member_ids):
        raise _refused(
            "issue_epoch_picks", "Each trace may be picked once.", status_code=400
        )
    if blocked := environment_blocker(environment):
        raise blocked
    members = members_of(issue, member_ids)
    if strangers := [one for one in member_ids if one not in members]:
        raise _refused(
            "issue_epoch_not_member",
            "Some picks are not traces of this issue.",
            status_code=400,
            member_ids=strangers,
        )
    units = member_units(issue, member_ids)
    if gone := [one for one in member_ids if one not in units]:
        raise _refused(
            "issue_epoch_member_gone",
            "Observe no longer keeps some of the picked traces. Swap them for others.",
            status_code=400,
            member_ids=gone,
        )
    if len({units[one][0] for one in member_ids}) > 1:
        raise _refused(
            "issue_epoch_mixed_units",
            "The picked traces are recorded in different ways. Pick traces of one kind.",
            status_code=400,
        )
    sources = [units[one][1] for one in member_ids]
    if len(set(sources)) != len(sources):
        raise _refused(
            "issue_epoch_same_session",
            "Two picks are traces of one session. Keep one of them.",
            status_code=400,
        )
    unit = units[member_ids[0]][0]
    reasons = pick_reasons(issue, member_ids)
    sample = [
        SamplePick(
            member_id=one,
            unit=units[one][0],
            source_id=units[one][1],
            reason=reasons[one],
            started_at=units[one][2],
        ).model_dump(mode="json")
        for one in member_ids
    ]
    member_of_source = dict(zip(sources, member_ids, strict=True))
    with transaction.atomic():
        # One open job per issue, without locking the feed's own rows: grouping
        # locks those, and an epoch must never hold it up.
        advisory_xact_lock(f"agent-fix-issue:{issue.id}")
        if (held := open_job(issue)) is not None:
            if not can_open(held, environments):
                raise _refused("issue_epoch_open", OPEN_ELSEWHERE)
            raise _refused(
                "issue_epoch_open",
                "This issue already has an epoch in progress. Open it.",
                job_id=str(held.id),
            )
        evidence_import, _evidence = create_evidence_import(
            environment=environment,
            project=issue.project,
            source_kind=unit,
            source_ids=sources,
            user=user,
            source_issue=issue,
        )
        cases = list(evidence_import.cases.select_related("duplicate_of"))
        # A pick that repeats another pick is drafted once, as that pick; one that
        # repeats another import's case waits on that import's review.
        if waiting := [
            member_of_source[case.source_id]
            for case in cases
            if case.status == EvidenceImportCase.Status.DUPLICATE
            and case.duplicate_of is not None
            and case.duplicate_of.evidence_import_id != evidence_import.id
            and case.duplicate_of.status != EvidenceImportCase.Status.ADDED
        ]:
            raise _refused(
                "issue_epoch_picks_in_review",
                "Another import in this environment is still drafting or reviewing some"
                " of these traces. Swap them for others.",
                member_ids=waiting,
            )
        pending = [
            str(case.id)
            for case in cases
            if case.status == EvidenceImportCase.Status.PENDING
        ]
        if not pending and not any(
            case.status == EvidenceImportCase.Status.DUPLICATE for case in cases
        ):
            raise _refused(
                "issue_epoch_nothing_readable",
                "None of the picked traces could be read. Swap them for others.",
                status_code=422,
            )
        job = AgentFixIssueJob.no_workspace_objects.create(
            organization_id=environment.organization_id,
            workspace_id=environment.workspace_id,
            environment=environment,
            evidence_import=evidence_import,
            sample=sample,
            created_by=user,
        )
        if pending:
            start_drafting(
                evidence_import,
                pending,
                endpoint_base_url=endpoint_base_url,
                user=user,
                shows=dict.fromkeys(pending, "failure"),
            )
    logger.info(
        "agent_fix_issue_job_created",
        job=str(job.id),
        issue=str(issue.id),
        environment=str(environment.id),
        picks=len(member_ids),
        drafting=len(pending),
    )
    return job


def _active_epoch_refusal(environment: HostedHarnessJob) -> HostedHarnessError | None:
    """Why a baseline may not start on the environment now: an epoch runs there, or
    another issue's baseline does. Each names its issue when it has one."""
    active = AgentFixRun.no_workspace_objects.filter(
        environment=environment, state__in=AgentFixRun.ACTIVE_STATES
    ).first()
    if active is not None:
        job = jobs_of_epochs([active]).get(active.id)
        issue = live_issue(job.evidence_import.source_issue) if job else None
        return _refused(
            "fix_run_active",
            (
                f"An epoch from {issue.cluster_id} is already running in this"
                " environment. Open it, or wait for it to finish."
                if issue is not None
                else "An epoch is already running in this environment. Open it, or"
                " wait for it to finish."
            ),
            fix_run_id=str(active.id),
        )
    if (measuring := measuring_job(environment)) is not None:
        issue = live_issue(measuring.evidence_import.source_issue)
        return _refused(
            "issue_epoch_measuring",
            f"An epoch from {issue.cluster_id if issue else 'another issue'} is starting"
            " in this environment. Wait for it, or open it.",
            job_id=str(measuring.id),
        )
    return None


def _require_launchable(
    environment: HostedHarnessJob,
    launch: IssueLaunch,
    *,
    added: int,
    scenarios: int,
) -> None:
    """Every refusal of the launch that can be known before the baseline is paid for:
    ``added`` new scenarios join the suite, and the baseline runs ``scenarios``."""
    # The epoch's targets are the baseline's scenarios that fail: never more than these.
    if scenarios < (fewest := fewest_failing_cases()):
        raise _refused(
            "fix_targets_too_few",
            f"Keep at least {fewest} scenarios: an epoch starts from that many failing"
            " ones.",
            status_code=422,
        )
    if blocked := environment_state_blocker(environment):
        raise blocked
    kind = agent_kind(environment)
    kind.require_available()
    if refused := sorted(set(launch.surfaces) - kind.surfaces):
        raise _refused(
            "fix_surface_not_allowed",
            f"an epoch cannot change this agent's {', '.join(refused)}",
            status_code=422,
        )
    if blocked := _active_epoch_refusal(environment):
        raise blocked
    # Drafts are made from failures, so the epoch writes variations of what fails
    # unless at least the floor of scenarios fail. Which do is known only after the
    # baseline, so the variations' checks run on the most there can be.
    if blocked := environment_variations_blocker(environment):
        raise blocked
    held = len(issue_scenarios(environment))
    limit = issue_scenarios_max()
    if held + added > limit:
        raise _refused(
            "issue_scenarios_limit",
            f"This environment holds {held} scenarios from Error Feed issues, and it"
            f" may hold {limit}. Remove older ones from its scenarios, then Run Epoch.",
            held=held,
            limit=limit,
        )
    organization_id = str(environment.organization_id)
    require_agent_fix_usage(organization_id)
    require_harness_call_usage(environment)
    # Writing the variations is a builder turn, billed as authoring.
    require_harness_authoring(organization_id)
    suite = HarnessSuiteMember.no_workspace_objects.filter(
        suite_version_id=environment.suite_version_id
    ).count()
    require_variation_room(suite + added, _variation_cases(scenarios))


def _variation_cases(scenarios: int) -> int:
    """The most failing cases an epoch from ``scenarios`` writes variations of: it
    writes them only below the floor of failing cases."""
    return min(scenarios, settings.AGENT_FIX_MIN_FAILING_CASES - 1)


def run_issue_job(
    job: AgentFixIssueJob, *, case_ids: list[str], launch: IssueLaunch, user: Any
) -> AgentFixIssueJob:
    """Run Epoch after the review: add the kept drafts to the environment's suite and
    start the current version's run on the kept scenarios. Every refusal leaves the
    suite as it was. One the job cannot get past (its drafts went stale) also ends it."""
    try:
        return _run(job, case_ids=case_ids, launch=launch, user=user)
    except HostedHarnessError as exc:
        if exc.code in _ENDS_THE_JOB and _end(
            job.id, IssueJobEnding(code=exc.code, message=exc.message)
        ):
            # Its drafts can go no further, so they hold back no later import.
            discard_open_cases(job.evidence_import)
        raise


def _run(
    job: AgentFixIssueJob, *, case_ids: list[str], launch: IssueLaunch, user: Any
) -> AgentFixIssueJob:
    wanted = {str(case_id) for case_id in case_ids}
    with transaction.atomic():
        # The job, then the environment, in the order advance_issue_job takes them
        # when it launches: adding to the suite and starting a run take it too.
        locked = (
            AgentFixIssueJob.no_workspace_objects.select_for_update(of=("self",))
            .select_related("evidence_import")
            .get(id=job.id)
        )
        environment = (
            HostedHarnessJob.no_workspace_objects.select_for_update(of=("self",))
            .select_related("environment_version", "suite_version")
            .get(id=job.environment_id)
        )
        if issue_job_phase(locked) != IssueJobPhase.REVIEW:
            raise _refused(
                "issue_epoch_not_in_review",
                "Run Epoch goes on from drafted scenarios that wait for review.",
            )
        cases = list(
            runnable_cases(locked.evidence_import)
            .filter(id__in=wanted)
            .select_related("duplicate_of")
            .order_by("position")
        )
        if not wanted or len(cases) != len(wanted):
            raise _refused(
                "evidence_case_not_drafted",
                "Only this epoch's drafted scenarios can be kept.",
            )
        added = [
            case for case in cases if case.status == EvidenceImportCase.Status.DRAFTED
        ]
        # A kept repeat runs as the scenario its original was added as.
        keys = list(
            dict.fromkeys(
                case.scenario_key
                if case.status == EvidenceImportCase.Status.DRAFTED
                else (case.duplicate_of or case).scenario_key
                for case in cases
            )
        )
        _require_launchable(environment, launch, added=len(added), scenarios=len(keys))
        if added:
            add_to_suite(
                locked.evidence_import,
                [str(case.id) for case in added],
                expected={},
                idempotency_key=f"issue-epoch-{locked.id}",
                user=user,
            )
        # The drafts the person did not keep are discarded, so they hold back no
        # later import of their sources.
        discard_open_cases(locked.evidence_import)
        environment.refresh_from_db()
        run, _created = create_selected_harness_run(
            environment,
            scenario_keys=keys,
            trials=1,
            idempotency_key=f"issue-epoch:{locked.id}:baseline",
        )
        if launch.max_minutes is None:
            suite = HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id=environment.suite_version_id
            ).count()
            # Every scenario of the suite is the epoch's to check, with variations of
            # the most failing ones it can write them of.
            variations = VARIATIONS_PER_CASE * _variation_cases(len(keys))
            launch = launch.model_copy(
                update={"max_minutes": default_max_minutes(suite + variations)}
            )
        locked.launch = launch.model_dump(mode="json")
        locked.baseline_run = run
        locked.save(update_fields=["launch", "baseline_run", "updated_at"])
        job_id = str(locked.id)
        transaction.on_commit(lambda: dispatch_run(run))
        transaction.on_commit(lambda: _start_workflow(job_id))
    logger.info(
        "agent_fix_issue_job_measuring",
        job=job_id,
        run=str(run.id),
        added=len(added),
        scenarios=len(keys),
    )
    return locked


def _start_workflow(job_id: str) -> None:
    """Start the job's workflow; a job whose workflow cannot start ends at once, so its
    baseline never holds the environment with nobody to launch from it."""
    try:
        start_agent_fix_issue_workflow(job_id)
    except Exception:
        logger.exception("agent_fix_issue_workflow_start_failed", job=job_id)
        stopped = _end(
            job_id,
            IssueJobEnding(
                code="issue_epoch_dispatch_failed",
                message="The epoch could not be scheduled. Run Epoch again.",
            ),
        )
        if stopped is not None and stopped.baseline_run is not None:
            _cancel_run(stopped.baseline_run)


def advance_issue_job(job_id: str) -> bool:
    """One look at a job whose baseline runs. Once the run finishes, the epoch is
    launched from it, on the kept scenarios that failed, with those that passed kept
    passing; a refusal ends the job with its reason. True once the job needs no more
    looks. Raises only what a later look can outlive."""
    with transaction.atomic():
        job = (
            AgentFixIssueJob.no_workspace_objects.select_for_update(of=("self",))
            .select_related("baseline_run", "created_by")
            .get(id=job_id)
        )
        if job.ending or job.fix_run_id is not None:
            return True
        run = job.baseline_run
        if run is None:
            _set_ending(job, _baseline_failed())
            return True
        # Checked before anything else, so nothing a look raises keeps the job going.
        if timezone.now() - run.created_at > _BASELINE_PATIENCE:
            _set_ending(
                job,
                IssueJobEnding(
                    code="issue_epoch_timed_out",
                    message="The epoch did not start in time. Run Epoch again.",
                ),
            )
            if run.state not in FINISHED_RUN_STATES:
                transaction.on_commit(lambda: _cancel_run(run))
            return True
        if run.state not in FINISHED_RUN_STATES:
            return False
        ran = scenario_results(run)
        # A run that ended without judging a scenario says nothing about the issue.
        if run.state != HostedHarnessJob.State.COMPLETED or all(
            one.passed is None for one in ran
        ):
            _set_ending(job, _baseline_failed())
            return True
        launch = IssueLaunch.model_validate(job.launch)
        try:
            fix_run = launch_fix_run(
                run,
                target_scenario_keys=[one.key for one in ran if one.failing_sub_goals],
                protected_scenario_keys=[one.key for one in ran if one.passed],
                surfaces=launch.surfaces,
                locked_paths=launch.locked_paths,
                budget_usd=launch.budget_credits / 100,
                max_minutes=launch.max_minutes,
                user=job.created_by,
                allowed_models=launch.allowed_models,
                reply_latency_margin_pct=launch.reply_latency_margin_pct,
                cost_margin_pct=launch.cost_margin_pct,
                cost_limit_usd=launch.cost_limit_usd,
            )
        except Exception as exc:
            # A plan limit is a refusal like any other, not an outage to retry.
            refusal = (
                exc if isinstance(exc, HostedHarnessError) else usage_limit_error(exc)
            )
            if refusal is None or refusal.retryable:
                raise
            _set_ending(job, IssueJobEnding(code=refusal.code, message=refusal.message))
            return True
        job.fix_run = fix_run
        job.save(update_fields=["fix_run", "updated_at"])
    logger.info("agent_fix_issue_job_launched", job=job_id, fix_run=str(fix_run.id))
    return True


def _baseline_failed() -> IssueJobEnding:
    return IssueJobEnding(
        code="issue_epoch_baseline_failed",
        message="The run on today's version did not finish. Run Epoch again.",
    )


def _set_ending(job: AgentFixIssueJob, ending: IssueJobEnding) -> None:
    job.ending = ending.model_dump()
    job.save(update_fields=["ending", "updated_at"])
    logger.info("agent_fix_issue_job_ended", job=str(job.id), code=ending.code)


def _end(job_id: Any, ending: IssueJobEnding) -> AgentFixIssueJob | None:
    """End a job that has not launched an epoch, once; None when it had."""
    with transaction.atomic():
        job = (
            AgentFixIssueJob.no_workspace_objects.select_for_update(of=("self",))
            .select_related("baseline_run")
            .get(id=job_id)
        )
        if job.ending or job.fix_run_id is not None:
            return None
        _set_ending(job, ending)
    return job


def _cancel_run(run: HostedHarnessJob) -> None:
    """Cancel the baseline. Nothing but its workflow reads the cancel request, so when
    the workflow cannot be told, the gateway cancels the run itself."""
    request_cancellation(run, "user_canceled")
    try:
        cancel_hosted_harness_gateway_workflow(str(run.id))
    except Exception:
        logger.exception("agent_fix_issue_baseline_cancel_failed", run=str(run.id))
        try:
            HostedHarnessGateway().cancel(run, reason="user_canceled")
        except Exception:
            # Stop, and a launch whose workflow would not start, still answer: a
            # later Stop of the ended job cancels the baseline it left running.
            logger.exception(
                "agent_fix_issue_baseline_gateway_cancel_failed", run=str(run.id)
            )


def stop_issue_job(job: AgentFixIssueJob) -> AgentFixIssueJob:
    """Stop a job before its epoch starts. Its baseline is canceled; before Run
    Epoch, its drafting is forgotten and its import discarded, and only then is it
    ended, so a stop that fails part way can be asked again. The scenarios Run Epoch
    added stay in the suite. A launched job's epoch stops on its own page."""
    if issue_job_phase(job) in (
        IssueJobPhase.DRAFTING,
        IssueJobPhase.REVIEW,
        IssueJobPhase.BLOCKED,
    ):
        # Drafting first: an import is not discarded while its builder drafts.
        forget(str(job.evidence_import_id))
    with transaction.atomic():
        locked = (
            AgentFixIssueJob.no_workspace_objects.select_for_update(of=("self",))
            .select_related("evidence_import", "baseline_run")
            .get(id=job.id)
        )
        phase = issue_job_phase(locked)
        if phase == IssueJobPhase.LAUNCHED:
            raise _refused(
                "issue_epoch_launched",
                "This epoch has started. Stop it on its own page.",
                fix_run_id=str(locked.fix_run_id),
            )
        if phase == IssueJobPhase.ENDED:
            # Asked again after a cancel failed: a baseline the ended job left
            # running is canceled again.
            stray = (
                locked.baseline_run
                if locked.fix_run_id is None
                and locked.baseline_run is not None
                and locked.baseline_run.state not in FINISHED_RUN_STATES
                else None
            )
        else:
            if phase != IssueJobPhase.MEASURING:
                discard_evidence_import(locked.evidence_import)
            # Ended first, under the job's lock, so the epoch cannot launch from it.
            _set_ending(locked, STOPPED)
            stray = locked.baseline_run if phase == IssueJobPhase.MEASURING else None
    if stray is not None:
        _cancel_run(stray)
    return locked
