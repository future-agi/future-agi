"""Reads for Run Epoch from an Error Feed issue: the issue's sample, a job's phase and
epoch, and the links between an issue, its imports and its epochs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db.models import (
    Case,
    Count,
    Exists,
    F,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
    UUIDField,
    Value,
    When,
)
from django.db.models.functions import Coalesce

from simulate.models import (
    AgentFixCandidate,
    AgentFixIssueJob,
    AgentFixRun,
    EvidenceImport,
    EvidenceImportCase,
    HarnessSuiteMember,
    HostedHarnessJob,
)
from simulate.pydantic_schemas.agent_fix import (
    BUDGET_CREDITS_MAX,
    MARGIN_PCT_DEFAULT,
    MAX_MINUTES_MAX,
    FixStep,
)
from simulate.pydantic_schemas.agent_fix_issue import (
    SAMPLE_ALTERNATES,
    SAMPLE_PICKS,
    SAMPLE_POOL,
    IssueJobPhase,
    IssueSample,
    PickReason,
    SamplePick,
    SampleUnit,
)
from simulate.selectors.agent_fix import (
    DEFAULT_BUDGET_CREDITS,
    fix_lineage,
    fix_winner,
    fresh_draft_pieces,
    is_retryable,
    launch_surfaces,
    limited_validation,
    retries_taking_the_retry,
    unsaved_changes,
)
from simulate.selectors.evidence_import import import_bundle
from simulate.services.agent_fix_agents import agent_kind
from simulate.services.harness_environment import environment_name
from simulate.services.harness_versioning import drafts
from simulate.services.hosted_harness import HostedHarnessError
from tracer.models.project import Project
from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.models.trace_grouping import TraceGroupingIssueState
from tracer.queries.feed import _current_memberships
from tracer.queries.grouping_redirect import (
    MAX_REDIRECT_HOPS,
    resolve_issue_redirect,
    resolve_issue_row,
)

# Rows of the issue's newest members read to fill the pool: one trace can hold several.
_RECENT_ROWS = SAMPLE_POOL * 5
# The Observe kinds a sample imports as.
_OBSERVE_KINDS = (
    EvidenceImport.SourceKind.SESSION,
    EvidenceImport.SourceKind.CALL,
    EvidenceImport.SourceKind.TRACE,
)
# A case that can join the suite at Run Epoch: drafted, or a repeat of a scenario the
# environment's suite already holds from an earlier import.
RUNNABLE_CASES = Q(status=EvidenceImportCase.Status.DRAFTED) | Q(
    status=EvidenceImportCase.Status.DUPLICATE,
    duplicate_of__status=EvidenceImportCase.Status.ADDED,
)


def requested_issue(cluster_id: str, project: Project) -> TraceErrorGroup | None:
    """The live issue ``cluster_id`` names in ``project``: the issue itself, or the one
    its merges redirect to."""
    found = resolve_issue_redirect(cluster_id, [str(project.id)])
    if found is None:
        return None
    return TraceErrorGroup.objects.filter(
        cluster_id=found["resolved_cluster_id"],
        project=project,
        target_type="error_feed",
    ).first()


def live_issue(issue: TraceErrorGroup | None) -> TraceErrorGroup | None:
    """The issue as the feed shows it now: itself, or the live issue its merges redirect
    to; None once no live issue is left."""
    if issue is None or not issue.deleted:
        return issue
    return resolve_issue_row(issue)


def environment_blocker(environment: HostedHarnessJob) -> HostedHarnessError | None:
    """Why an epoch cannot be run from an issue on the environment now; None when it
    can. Drafting needs the environment authored and idle, an agent Epoch can change,
    and no unsaved changes for the builder to draft over."""
    if environment.state != HostedHarnessJob.State.COMPLETED:
        return HostedHarnessError(
            "environment_busy",
            "The environment isn't ready. Run Epoch once it finishes updating.",
            status_code=409,
        )
    try:
        agent_kind(environment).require_available()
    except HostedHarnessError as exc:
        return exc
    if not drafts.drafts_apply(environment):
        return HostedHarnessError(
            "drafts_unavailable",
            "This environment keeps no unsaved changes, so scenarios cannot be"
            " drafted in it.",
            status_code=409,
        )
    if unsaved := unsaved_changes(fresh_draft_pieces(environment)):
        return HostedHarnessError(
            "fix_draft_open",
            f"This environment has unsaved {unsaved} changes. Save or discard them,"
            " then Run Epoch.",
            status_code=409,
        )
    return None


def _groups_sessions(issue: TraceErrorGroup) -> bool:
    """Whether the issue's members are sessions (a session-eval issue), not traces."""
    return issue.eval_target_type == "session"


def _uuids(values: Iterable[Any]) -> list[str]:
    found = []
    for value in values:
        try:
            found.append(str(UUID(str(value))))
        except ValueError:
            continue
    return found


def _feed_picks(issue: TraceErrorGroup) -> tuple[list[str], list[str]]:
    """The traces the feed itself points at, still members: its examples of the issue
    (its prototypes' traces, in their order), and the traces its root-cause analysis
    cited. None for an issue of sessions."""
    if _groups_sessions(issue):
        return [], []
    members = _current_memberships().filter(cluster=issue)
    state = TraceGroupingIssueState.objects.filter(cluster=issue).first()
    prototypes = _uuids(state.prototype_occurrence_ids if state else [])
    traces = (
        {
            str(finding): str(trace)
            for finding, trace in members.filter(finding_id__in=prototypes).values_list(
                "finding_id", "trace_id"
            )
        }
        if prototypes
        else {}
    )
    examples = [traces[one] for one in prototypes if one in traces]
    cited = _uuids(issue.rca_evidence_trace_ids or [])[:SAMPLE_POOL]
    held = (
        {
            str(one)
            for one in members.filter(trace_id__in=cited).values_list(
                "trace_id", flat=True
            )
        }
        if cited
        else set()
    )
    return examples, [one for one in cited if one in held]


def pick_reasons(
    issue: TraceErrorGroup, member_ids: list[str]
) -> dict[str, PickReason]:
    """Why each member is in a sample: one of the feed's examples, a trace its
    root-cause analysis cited, or else a member picked from the rest."""
    examples, cited = (set(found) for found in _feed_picks(issue))
    return {
        member: "example"
        if member in examples
        else "cited"
        if member in cited
        else "recent"
        for member in member_ids
    }


def _pool(issue: TraceErrorGroup) -> list[tuple[str, PickReason]]:
    """The members to pick from, each once, in the order they are offered: the feed's
    own examples, the traces its root-cause analysis cited, then the newest members.
    At most SAMPLE_POOL members, read from at most _RECENT_ROWS rows."""
    pool: dict[str, PickReason] = {}

    def offer(ids: Iterable[Any], reason: PickReason) -> None:
        for one in ids:
            if one is not None and len(pool) < SAMPLE_POOL:
                pool.setdefault(str(one), reason)

    examples, cited = _feed_picks(issue)
    offer(examples, "example")
    offer(cited, "cited")
    column = "trace_session_id" if _groups_sessions(issue) else "trace_id"
    members = _current_memberships().filter(cluster=issue)
    offer(
        members.filter(**{f"{column}__isnull": False})
        .order_by("-created_at", "-id")
        .values_list(column, flat=True)[:_RECENT_ROWS],
        "recent",
    )
    return list(pool.items())


def member_units(
    issue: TraceErrorGroup, member_ids: list[str]
) -> dict[str, tuple[SampleUnit, str, str | None]]:
    """Each member's unit, the id its import reads, and when it started. A session
    member is a session. A trace is a call when its root is a conversation, else it is
    read through its session when it has one, else alone; so a trace's unit is the
    same in every sample. One bounded read of the traces' roots; a trace with no root
    left is left out."""
    if _groups_sessions(issue):
        return {member: ("session", member, None) for member in member_ids}
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        roots = reader.trace_units(member_ids, project_id=str(issue.project_id))
    units: dict[str, tuple[SampleUnit, str, str | None]] = {}
    for trace_id, root in roots.items():
        unit: SampleUnit
        if root.observation_type == "conversation":
            unit, source = "call", trace_id
        elif root.session_id:
            unit, source = "session", root.session_id
        else:
            unit, source = "trace", trace_id
        units[trace_id] = (unit, source, root.start_time.isoformat())
    return units


def sources_in_review(environment: HostedHarnessJob) -> set[tuple[str, str]]:
    """The Observe sources the environment's other imports still draft or review: a new
    import of one becomes their repeat, which cannot join the suite until they do."""
    return set(
        EvidenceImportCase.no_workspace_objects.filter(
            evidence_import__environment=environment,
            evidence_import__deleted=False,
            evidence_import__source_kind__in=_OBSERVE_KINDS,
            status__in=(
                EvidenceImportCase.Status.PENDING,
                EvidenceImportCase.Status.DRAFTED,
            ),
            duplicate_of__isnull=True,
        ).values_list("evidence_import__source_kind", "source_id")
    )


def issue_sample(
    issue: TraceErrorGroup,
    environment: HostedHarnessJob | None,
    *,
    picks: int = SAMPLE_PICKS,
    alternates: int = SAMPLE_ALTERNATES,
) -> IssueSample:
    """A small, varied sample of the issue's members, the same for the same issue
    state. The unit is the first member's; members of another unit, a second member of
    one session, and sources another import of ``environment`` reviews now are left
    out. The rest beyond ``picks`` are the alternates offered to swap one for."""
    pool = _pool(issue)
    units = member_units(issue, [member for member, _ in pool])
    busy = sources_in_review(environment) if environment is not None else set()
    chosen: list[SamplePick] = []
    unit: SampleUnit | None = None
    seen: set[str] = set()
    for member, reason in pool:
        found = units.get(member)
        if found is None:
            continue
        kind, source, started = found
        unit = unit or kind
        if kind != unit or source in seen or (kind, source) in busy:
            continue
        seen.add(source)
        chosen.append(
            SamplePick(
                member_id=member,
                unit=kind,
                source_id=source,
                reason=reason,
                started_at=started,
            )
        )
        if len(chosen) == picks + alternates:
            break
    return IssueSample(unit=unit, picks=chosen[:picks], alternates=chosen[picks:])


def members_of(issue: TraceErrorGroup, member_ids: list[str]) -> set[str]:
    """Which of ``member_ids`` are live members of the issue: one indexed read."""
    column = "trace_session_id" if _groups_sessions(issue) else "trace_id"
    ids = _uuids(member_ids)
    if not ids:
        return set()
    return {
        str(one)
        for one in _current_memberships()
        .filter(cluster=issue, **{f"{column}__in": ids})
        .values_list(column, flat=True)
    }


def runnable_cases(evidence_import: EvidenceImport) -> QuerySet[EvidenceImportCase]:
    return EvidenceImportCase.no_workspace_objects.filter(
        RUNNABLE_CASES, evidence_import=evidence_import
    )


def issue_job_phase(job: AgentFixIssueJob) -> IssueJobPhase:
    """Where the job is, from what it holds. Before Run Epoch the import says: the
    builder drafts while it has a drafting job, and with none left the job waits for
    review when a case can run, and is blocked when none can."""
    if job.ending:
        return IssueJobPhase.ENDED
    if job.fix_run_id is not None:
        return IssueJobPhase.LAUNCHED
    if job.baseline_run_id is not None:
        return IssueJobPhase.MEASURING
    if job.evidence_import.drafting_job_id is not None:
        return IssueJobPhase.DRAFTING
    runnable = getattr(job, "has_runnable_case", None)
    if runnable is None:
        runnable = runnable_cases(job.evidence_import).exists()
    if runnable:
        return IssueJobPhase.REVIEW
    return IssueJobPhase.BLOCKED


def job_epoch(job: AgentFixIssueJob) -> AgentFixRun | None:
    """The job's newest epoch: the one it launched, or the retry that started it again."""
    if job.fix_run_id is None:
        return None
    current = job.fix_run
    while (
        retry := AgentFixRun.no_workspace_objects.filter(retried_from=current)
        .order_by("-created_at")
        .first()
    ) is not None:
        current = retry
    return current


def is_open(job: AgentFixIssueJob) -> bool:
    """Whether the job still holds its issue: it drafts, waits for review or measures, or
    its epoch is still active. A blocked or ended job does not."""
    phase = issue_job_phase(job)
    if phase == IssueJobPhase.LAUNCHED:
        epoch = job_epoch(job)
        return epoch is not None and epoch.state in AgentFixRun.ACTIVE_STATES
    return phase in (
        IssueJobPhase.DRAFTING,
        IssueJobPhase.REVIEW,
        IssueJobPhase.MEASURING,
    )


def _issue_jobs(
    issues: Iterable[TraceErrorGroup], environments: QuerySet[HostedHarnessJob] | None
) -> QuerySet[AgentFixIssueJob]:
    """Map retired source rows to this page's live issues, one query per depth."""
    roots = {issue.pk: issue.pk for issue in issues}
    frontier = set(roots)
    for _ in range(MAX_REDIRECT_HOPS):
        if not frontier:
            break
        parents = TraceErrorGroup.all_objects.filter(
            deleted=True,
            target_type="error_feed",
            redirect_to_id__in=frontier,
            project_id=F("redirect_to__project_id"),
        ).values_list("pk", "redirect_to_id")
        more = {pk: roots[target] for pk, target in parents if pk not in roots}
        roots.update(more)
        frontier = set(more)

    source = "evidence_import__source_issue_id"
    jobs = AgentFixIssueJob.no_workspace_objects.filter(
        environment__deleted=False, evidence_import__source_issue_id__in=roots
    )
    if environments is not None:
        jobs = jobs.filter(environment__in=environments)
    merged: dict[UUID, list[UUID]] = {}
    for pk, root in roots.items():
        if pk != root:
            merged.setdefault(root, []).append(pk)
    branches = [
        When(
            evidence_import__source_issue_id__in=pks,
            then=Value(root, output_field=UUIDField()),
        )
        for root, pks in merged.items()
    ]
    return jobs.annotate(
        live_id=Case(*branches, default=F(source), output_field=UUIDField())
    ).select_related("environment", "evidence_import", "baseline_run", "fix_run")


def jobs_of_issues(
    issues: Iterable[TraceErrorGroup],
    *,
    environments: QuerySet[HostedHarnessJob] | None = None,
) -> dict[UUID, list[AgentFixIssueJob]]:
    """All jobs, including older jobs which might still be open."""
    found: dict[UUID, list[AgentFixIssueJob]] = {}
    for job in _issue_jobs(issues, environments).order_by("-created_at", "-id"):
        found.setdefault(job.live_id, []).append(job)
    return found


def issue_status_page(
    issues: list[TraceErrorGroup], environments: QuerySet[HostedHarnessJob]
) -> dict[UUID, dict[str, Any]]:
    """The newest visible job per live issue, with its newest retry."""
    by_id = {issue.pk: issue for issue in issues}
    jobs = list(
        _issue_jobs(issues, environments)
        .annotate(
            has_runnable_case=Exists(
                EvidenceImportCase.no_workspace_objects.filter(
                    RUNNABLE_CASES, evidence_import_id=OuterRef("evidence_import_id")
                )
            )
        )
        .order_by("live_id", "-created_at", "-id")
        .distinct("live_id")
    )
    epochs = _status_epochs(jobs)
    return {
        job.live_id: _job_view(job, by_id[job.live_id], epochs.get(job.fix_run_id))
        for job in jobs
    }


def _status_epochs(jobs: list[AgentFixIssueJob]) -> dict[UUID, AgentFixRun]:
    newest = {job.fix_run_id: job.fix_run_id for job in jobs if job.fix_run_id}
    frontier = set(newest)
    while frontier:
        children = dict(
            AgentFixRun.no_workspace_objects.filter(retried_from_id__in=frontier)
            .order_by("retried_from_id", "-created_at", "-pk")
            .distinct("retried_from_id")
            .values_list("retried_from_id", "pk")
        )
        newest = {root: children.get(pk, pk) for root, pk in newest.items()}
        frontier = set(children.values())
    numbers = (
        AgentFixRun.no_workspace_objects.filter(
            environment_id=OuterRef("environment_id"),
            created_at__lte=OuterRef("created_at"),
        )
        .order_by()
        .values("environment_id")
        .annotate(total=Count("pk"))
        .values("total")
    )
    epochs = {
        run.pk: run
        for run in AgentFixRun.no_workspace_objects.filter(pk__in=newest.values())
        .annotate(
            number=Subquery(numbers),
            retry_taken=Exists(
                retries_taking_the_retry(
                    Coalesce(
                        OuterRef("retried_from_id"),
                        OuterRef("pk"),
                        output_field=UUIDField(),
                    )
                )
            ),
        )
        .prefetch_related(
            Prefetch(
                "candidates",
                queryset=AgentFixCandidate.objects.filter(
                    outcome=AgentFixCandidate.Outcome.WINNER
                ).only("fix_run_id", "outcome", "pull_request_url"),
            )
        )
    }
    return {root: epochs[pk] for root, pk in newest.items() if pk in epochs}


# An issue has one open job, whichever environment it runs in. A caller who cannot
# open that job is told only that it exists.
OPEN_ELSEWHERE = (
    "An epoch from this issue is already in progress in another environment."
)


def open_job(issue: TraceErrorGroup) -> AgentFixIssueJob | None:
    """The issue's open job, in whichever environment it runs."""
    return next(
        (job for job in jobs_of_issues([issue]).get(issue.id, []) if is_open(job)),
        None,
    )


def can_open(job: AgentFixIssueJob, environments: QuerySet[HostedHarnessJob]) -> bool:
    """Whether ``job`` runs in one of the caller's ``environments``: only then is its id
    named to the caller."""
    return environments.filter(id=job.environment_id).exists()


def jobs_of_epochs(
    fix_runs: Iterable[AgentFixRun],
) -> dict[UUID, AgentFixIssueJob]:
    """The job each epoch came from: the job that launched it, or the job of the epoch
    it retries."""
    roots = {run.id: fix_lineage(run)[-1] for run in fix_runs}
    if not roots:
        return {}
    jobs = {
        job.fix_run_id: job
        for job in AgentFixIssueJob.no_workspace_objects.filter(
            fix_run_id__in=set(roots.values())
        ).select_related("evidence_import__source_issue__project")
    }
    return {run_id: jobs[root] for run_id, root in roots.items() if root in jobs}


def measuring_job(environment: HostedHarnessJob) -> AgentFixIssueJob | None:
    """The job whose baseline runs on the environment now: it holds the environment
    until its epoch starts (launch_blocker reads the same rule)."""
    return (
        AgentFixIssueJob.no_workspace_objects.filter(
            environment=environment,
            baseline_run__isnull=False,
            fix_run__isnull=True,
            ending__isnull=True,
        )
        .select_related("evidence_import__source_issue")
        .first()
    )


def issue_scenarios(
    environment: HostedHarnessJob, scenario_keys: Iterable[str] | None = None
) -> dict[str, TraceErrorGroup | None]:
    """The issue each of the environment's issue scenarios came from, by key: added to
    its suite by an issue's import, named as the feed shows it now. Without
    ``scenario_keys``, the scenarios its default suite holds now, which the limit
    counts (issue_scenarios_max). An issue no longer live leaves its scenarios out of
    the names, not out of the count."""
    keys = list(
        scenario_keys
        if scenario_keys is not None
        else HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=environment.suite_version_id
        ).values_list("scenario_key", flat=True)
    )
    if not keys:
        return {}
    cases = (
        EvidenceImportCase.no_workspace_objects.filter(
            evidence_import__environment=environment,
            evidence_import__deleted=False,
            evidence_import__source_issue__isnull=False,
            status=EvidenceImportCase.Status.ADDED,
            scenario_key__in=keys,
        )
        .select_related("evidence_import__source_issue")
        .order_by("evidence_import__created_at", "position")
    )
    live: dict[UUID, TraceErrorGroup | None] = {}
    found: dict[str, TraceErrorGroup | None] = {}
    for case in cases:
        issue = case.evidence_import.source_issue
        if issue is None:  # the filter reads only imports of an issue
            continue
        if issue.id not in live:
            live[issue.id] = live_issue(issue)
        found.setdefault(case.scenario_key, live[issue.id])
    return found


def issue_scenarios_max() -> int:
    return settings.AGENT_FIX_ISSUE_SCENARIOS_MAX


def _epoch_view(epoch: AgentFixRun) -> dict[str, Any]:
    winner = fix_winner(epoch)
    return {
        "id": epoch.id,
        # Its place among the environment's epochs: "Epoch 6".
        "number": epoch.number
        if hasattr(epoch, "number")
        else AgentFixRun.no_workspace_objects.filter(
            environment_id=epoch.environment_id, created_at__lte=epoch.created_at
        ).count(),
        "environment_id": epoch.environment_id,
        "state": epoch.state,
        "step": FixStep.DONE.value
        if epoch.state not in AgentFixRun.ACTIVE_STATES
        else epoch.step or None,
        "failure_code": (epoch.failure or {}).get("code"),
        "retryable": is_retryable(epoch),
        "limited_validation": limited_validation(epoch),
        "pull_request_url": (winner.pull_request_url or None) if winner else None,
    }


def issue_view(issue: TraceErrorGroup | None) -> dict[str, Any] | None:
    if issue is None:
        return None
    return {
        "cluster_id": issue.cluster_id,
        "project_id": issue.project_id,
        "title": issue.title,
    }


def launch_defaults(environment: HostedHarnessJob | None) -> dict[str, Any]:
    """What Run Epoch's form starts from on the environment: the parts of the agent an
    epoch may change, and the budget and margins, within their limits. The rest comes
    from the baseline at launch."""
    try:
        offered = agent_kind(environment).surfaces if environment else frozenset()
    except HostedHarnessError:
        offered = frozenset()
    return {
        "surfaces": [one.model_dump(mode="json") for one in launch_surfaces(offered)],
        "budget_credits": DEFAULT_BUDGET_CREDITS,
        "budget_credits_max": BUDGET_CREDITS_MAX,
        "max_minutes_max": MAX_MINUTES_MAX,
        "reply_latency_margin_pct": MARGIN_PCT_DEFAULT,
        "cost_margin_pct": MARGIN_PCT_DEFAULT,
    }


def issue_job_view(job: AgentFixIssueJob, *, detail: bool = False) -> dict[str, Any]:
    """The job as the issue page and the CLI read it: its phase, its issue as the feed
    shows it now, its sample, and its baseline and newest epoch once it has them."""
    return _job_view(
        job, live_issue(job.evidence_import.source_issue), job_epoch(job), detail=detail
    )


def _job_view(
    job: AgentFixIssueJob,
    issue: TraceErrorGroup | None,
    epoch: AgentFixRun | None,
    *,
    detail: bool = False,
) -> dict[str, Any]:
    baseline = job.baseline_run
    view: dict[str, Any] = {
        "id": job.id,
        "phase": issue_job_phase(job).value,
        "issue": issue_view(issue),
        "environment": {
            "id": job.environment_id,
            "name": environment_name(job.environment),
        },
        "sample": job.sample,
        "ending": job.ending,
        "baseline": {
            "test_execution_id": baseline.test_execution_id,
            "state": baseline.state,
            "scenarios": baseline.scenario_count,
            "completed_count": baseline.completed_count,
            "failed_count": baseline.failed_count,
        }
        if baseline is not None
        else None,
        "epoch": _epoch_view(epoch) if epoch is not None else None,
        "created_at": job.created_at,
    }
    if detail:
        view["evidence_import"] = import_bundle(job.evidence_import)
        view["defaults"] = launch_defaults(job.environment)
    return view


def default_environment(
    project: Project, environments: QuerySet[HostedHarnessJob]
) -> HostedHarnessJob | None:
    """The environment the project's last Run Epoch from an issue used, among
    ``environments`` (the caller's), while it still exists: the developer picks one
    once, and it is offered after."""
    last = (
        EvidenceImport.no_workspace_objects.filter(
            project=project,
            source_issue__isnull=False,
            environment__in=environments.filter(
                deleted=False, environment__isnull=True
            ),
        )
        .order_by("-created_at")
        .values_list("environment_id", flat=True)
        .first()
    )
    return environments.filter(id=last).first() if last else None
