"""Reads for "Fix my agent": the engine's brief, a check run's results, the run list."""

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce

from simulate.models import (
    AgentFixRun,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessSuiteMember,
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.models.test_execution import CallTranscript
from simulate.pydantic_schemas.agent_fix import (
    BUDGET_CREDITS_MAX,
    MAX_MINUTES_MAX,
    SCENARIOS_MAX,
    AgentIdentity,
    CheckRecord,
    CheckRunStatus,
    EngineBrief,
    FixRequest,
    InventoryScenario,
    LaunchBlocked,
    LaunchDefaults,
    LaunchEnvironment,
    LaunchLimits,
    LaunchOptions,
    LaunchScenario,
    LaunchSourceRun,
    LaunchSuggested,
    LaunchSurface,
    Surface,
)
from simulate.selectors.runs import user_executions
from simulate.services.agent_fix_agents import AgentKind, agent_kind
from simulate.services.harness_environment import environment_name
from simulate.services.harness_github import short_ref
from simulate.services.harness_usage import check_harness_action
from simulate.services.hosted_harness import HostedHarnessError

TRANSCRIPT_CHARS = 8000
FINISHED_RUN_STATES = {
    HostedHarnessJob.State.COMPLETED,
    HostedHarnessJob.State.FAILED,
    HostedHarnessJob.State.CANCELED,
}


def sub_goal_names(raw: Any) -> list[str]:
    """Sub-goal names from an authored scenario, whichever shape it stored them in."""
    names = []
    for item in raw or []:
        name = item.get("name") if isinstance(item, Mapping) else item
        if name:
            names.append(str(name))
    return names


def inventory(fix_run: AgentFixRun) -> list[InventoryScenario]:
    """Every scenario a check run may select: the source run's pinned suite, or the
    environment's own scenarios when the source run was not pinned."""
    suite_version_id = (
        fix_run.source_run.suite_version_id if fix_run.source_run else None
    )
    if suite_version_id:
        members = HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=suite_version_id
        ).select_related("scenario")
        return [
            _scenario(member.scenario_key, member.content or {}, member.scenario)
            for member in members.order_by("number")
        ]
    rows = HostedHarnessScenario.no_workspace_objects.filter(
        job_id=fix_run.environment_id
    ).order_by("number", "created_at")
    return [_scenario(row.scenario_key, {}, row) for row in rows]


def _scenario(
    key: str, content: Mapping[str, Any], row: HostedHarnessScenario | None
) -> InventoryScenario:
    def field(name: str) -> Any:
        return content.get(name) or (getattr(row, name, None) if row else None) or ""

    return InventoryScenario(
        key=key,
        name=str(field("name")),
        tests=str(field("tests")),
        use_case=str(field("use_case")),
        sub_goals=sub_goal_names(
            content.get("sub_goals") or getattr(row, "sub_goals", None)
        ),
    )


def fix_deadline(fix_run: AgentFixRun) -> datetime:
    """When the fix must have ended: its start plus the minutes the user allowed."""
    minutes = FixRequest.model_validate(fix_run.request).max_minutes
    return (fix_run.started_at or fix_run.created_at) + timedelta(minutes=minutes)


def engine_brief(fix_run: AgentFixRun) -> EngineBrief:
    if fix_run.source_run is None:
        raise HostedHarnessError(
            "fix_source_deleted",
            "the run this fix started from was deleted",
            status_code=409,
        )
    source = (fix_run.source_run.payload or {}).get("source") or {}
    request = FixRequest.model_validate(fix_run.request)
    return EngineBrief(
        fix_run_id=str(fix_run.id),
        environment_name=environment_name(fix_run.environment),
        base_agent_version_id=str(fix_run.base_agent_version_id),
        base_commit=str(source.get("commit_sha") or ""),
        request=request,
        inventory=inventory(fix_run),
        has_checkpoint=bool(fix_run.checkpoint_key),
        deadline_at=fix_deadline(fix_run).isoformat(),
    )


def check_run_status(run: HostedHarnessJob) -> CheckRunStatus:
    """A check run's finished trials as engine records. A trial without a receipt yet is
    left out; ``finished`` says when no more will come."""
    executions = list(
        HostedHarnessExecution.no_workspace_objects.filter(job=run)
        .select_related("source_scenario", "call_execution")
        .order_by("execution_key")
    )
    finished = run.state in FINISHED_RUN_STATES
    # The engine counts records while the run goes and reads them once it ends: the
    # transcripts, the heavy part, come only then.
    calls = [execution.call_execution for execution in executions]
    transcripts = _transcripts(call.id for call in calls) if finished else {}
    records = []
    for execution in executions:
        receipt = (execution.call_execution.call_metadata or {}).get(
            "hosted_harness_receipt"
        )
        if not isinstance(receipt, Mapping):
            continue
        records.append(
            _record(
                execution.source_scenario.scenario_key,
                execution.trial_index,
                receipt,
                transcripts.get(execution.call_execution_id, ""),
            )
        )
    return CheckRunStatus(
        run_id=str(run.id),
        state=run.state,
        finished=finished,
        total=len(executions),
        records=records,
    )


def judged_sub_goals(receipt: Mapping[str, Any]) -> dict[str, bool | None]:
    """Each sub-goal of a receipt and whether it held, None when nothing decided it. A
    conversation the agent itself broke fails every goal it left undecided."""
    checks = [one for one in receipt.get("sub_goals") or [] if isinstance(one, Mapping)]
    judged = {str(one["name"]): one.get("held") for one in checks if one.get("name")}
    if (receipt.get("failure") or {}).get("domain") == "agent":
        judged = {name: held is True for name, held in judged.items()}
    return {
        name: held if isinstance(held, bool) else None for name, held in judged.items()
    }


def _record(
    scenario_key: str, trial: int, receipt: Mapping[str, Any], transcript: str
) -> CheckRecord:
    checks = [one for one in receipt.get("sub_goals") or [] if isinstance(one, Mapping)]
    judged = judged_sub_goals(receipt)
    undecided = [name for name, held in judged.items() if not isinstance(held, bool)]
    if not judged:
        error = f"the trial ended {receipt.get('status') or 'without a result'}"
    elif undecided:
        error = f"the judge decided nothing on {', '.join(undecided)}"
    else:
        error = ""
    return CheckRecord(
        scenario_key=scenario_key,
        trial=trial,
        status="error" if error else "ok",
        error=error,
        sub_goals={
            name: held for name, held in judged.items() if isinstance(held, bool)
        },
        reasons={
            str(one["name"]): str(one.get("reason") or "")
            for one in checks
            if one.get("name")
        },
        transcript=transcript,
    )


def _transcripts(call_ids: Iterable[Any]) -> dict[Any, str]:
    lines: dict[Any, list[str]] = {}
    rows = CallTranscript.no_workspace_objects.filter(
        call_execution_id__in=list(call_ids)
    ).order_by("call_execution_id", "start_time_ms", "created_at")
    for row in rows.values_list("call_execution_id", "speaker_role", "content"):
        lines.setdefault(row[0], []).append(f"{row[1]}: {row[2]}")
    return {
        call_id: "\n".join(turns)[:TRANSCRIPT_CHARS] for call_id, turns in lines.items()
    }


def scenario_results(source_run: HostedHarnessJob) -> list[LaunchScenario]:
    """Each scenario of a finished run with the sub-goals that did not hold in any of its
    trials, in the suite's order. ``passed`` is None when nothing of it was judged."""
    executions = (
        HostedHarnessExecution.no_workspace_objects.filter(job=source_run)
        .select_related("call_execution", "source_scenario")
        .order_by("source_scenario__number", "source_scenario__scenario_key")
    )
    results: dict[str, LaunchScenario] = {}
    for execution in executions:
        scenario = execution.source_scenario
        result = results.setdefault(
            scenario.scenario_key,
            LaunchScenario(
                key=scenario.scenario_key,
                name=scenario.name or scenario.scenario_key,
                passed=None,
                failing_sub_goals=[],
            ),
        )
        receipt = (execution.call_execution.call_metadata or {}).get(
            "hosted_harness_receipt"
        )
        if not isinstance(receipt, Mapping):
            continue
        for name, held in judged_sub_goals(receipt).items():
            if held is False and name not in result.failing_sub_goals:
                result.failing_sub_goals.append(name)
            if held is not None:
                result.passed = not result.failing_sub_goals
    return list(results.values())


def failing_sub_goals_by_scenario(
    source_run: HostedHarnessJob, scenario_keys: list[str]
) -> dict[str, list[str]]:
    """The sub-goals that did not hold on each of the given scenarios of a finished run, in
    the order the scenarios are given; a scenario where everything held maps to []."""
    found = {one.key: one.failing_sub_goals for one in scenario_results(source_run)}
    return {key: found.get(key, []) for key in scenario_keys}


def failing_sub_goals(
    source_run: HostedHarnessJob, scenario_keys: list[str]
) -> list[str]:
    """The sub-goals that did not hold on the given scenarios of a finished run."""
    by_scenario = failing_sub_goals_by_scenario(source_run, scenario_keys)
    return list(dict.fromkeys(name for names in by_scenario.values() for name in names))


def with_spend(runs: QuerySet[AgentFixRun]) -> QuerySet[AgentFixRun]:
    """Each fix's spend in billed US dollars. ``fix_spent_usd`` is the engine's model calls
    plus every check run whose usage is sealed. ``committed_usd`` also holds a worst case for
    each check run still unsealed; it is what the fix's limit is held against."""
    live = Q(check_runs__deleted=False)
    money = DecimalField(max_digits=12, decimal_places=4)
    sealed = Coalesce(
        Sum("check_runs__fix_cost_usd", filter=live),
        Value(Decimal(0)),
        output_field=money,
    )
    unsealed = Coalesce(
        Sum(
            "check_runs__scenario_count",
            filter=live & Q(check_runs__fix_cost_usd__isnull=True),
        ),
        0,
    )
    worst_case = Value(Decimal(settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION))
    return runs.annotate(
        fix_spent_usd=ExpressionWrapper(F("spent_usd") + sealed, output_field=money),
        committed_usd=ExpressionWrapper(
            F("spent_usd") + sealed + unsealed * worst_case, output_field=money
        ),
    )


def run_number(path: str = "") -> Subquery:
    """A run's place among its environment's runs, for the run at ``path`` (e.g.
    ``"source_run__"``); the count ``run_summaries`` shows."""
    return Subquery(
        user_executions()
        .filter(
            run_test=OuterRef(f"{path}test_execution__run_test"),
            created_at__lte=OuterRef(f"{path}test_execution__created_at"),
        )
        .values("run_test")
        .annotate(total=Count("id"))
        .values("total")
    )


def agent_identity(version: AgentVersion) -> AgentIdentity:
    """How the user names an agent version: its agent, kind, number and, for a GitHub
    agent, the branch and commit it was built from."""
    runtime = getattr(version, "harness_runtime", None)
    source = (runtime.source if runtime else None) or {}
    kind = str(runtime.source_kind) if runtime else ""
    ref = None
    if kind == HarnessAgentVersionRuntime.SourceKind.GITHUB:
        branch = short_ref(source.get("ref")) or "default branch"
        commit = str(source.get("commit_sha") or "")[:7]
        ref = f"{branch} @ {commit}" if commit else branch
    return AgentIdentity(
        name=version.agent_definition.agent_name,
        kind=kind,
        version_id=str(version.id),
        version_number=version.version_number,
        ref=ref,
    )


def fix_runs_for_read(runs: QuerySet[AgentFixRun]) -> QuerySet[AgentFixRun]:
    """Fixes with everything their read shows: spend, candidates, check runs, and the
    numbers and names the user knows the fix, its run and its agent by."""
    check_runs = HostedHarnessJob.no_workspace_objects.only(
        "id",
        "fix_run_id",
        "fix_label",
        "state",
        "test_execution_id",
        "agent_version_id",
        "created_at",
    ).order_by("created_at")
    number = (
        AgentFixRun.no_workspace_objects.filter(
            environment=OuterRef("environment"), created_at__lte=OuterRef("created_at")
        )
        .values("environment")
        .annotate(total=Count("id"))
        .values("total")
    )
    return (
        with_spend(runs)
        .annotate(number=Subquery(number), source_run_number=run_number("source_run__"))
        .select_related(
            "environment__environment_version",
            "base_agent_version__agent_definition",
            "base_agent_version__harness_runtime",
        )
        .prefetch_related(
            "candidates__agent_version",
            Prefetch("check_runs", queryset=check_runs, to_attr="check_run_list"),
        )
    )


def fix_runs_for_environment(environment: HostedHarnessJob) -> QuerySet[AgentFixRun]:
    return fix_runs_for_read(
        AgentFixRun.no_workspace_objects.filter(environment=environment)
        .select_related("base_agent_version", "source_run")
        # The id breaks ties, so a page holds still while fixes are added.
        .order_by("-created_at", "-id")
    )


SURFACE_LABELS = {
    Surface.PROMPT: "Instructions",
    Surface.TOOLS: "Tools",
    Surface.SETTINGS: "Settings",
    Surface.CODE: "Code",
}
# What the launch form starts from: the instructions only, within 1,500 credits and two
# and a half hours.
DEFAULT_SURFACES = [Surface.PROMPT]
DEFAULT_BUDGET_CREDITS = 1500
DEFAULT_MAX_MINUTES = 150


def launch_blocker(
    source_run: HostedHarnessJob, *, usage: bool = True
) -> HostedHarnessError | None:
    """What would refuse a fix from this run now, in the order launch checks it; None
    when nothing would. Launch checks usage itself, to answer with the usage refusal."""
    if source_run.state not in FINISHED_RUN_STATES:
        return HostedHarnessError(
            "fix_source_running", "wait for the run to finish", status_code=409
        )
    try:
        agent_kind(source_run.environment).require_available()
    except HostedHarnessError as exc:
        return exc
    if AgentFixRun.no_workspace_objects.filter(
        environment=source_run.environment, state__in=AgentFixRun.ACTIVE_STATES
    ).exists():
        return HostedHarnessError(
            "fix_run_active",
            "this environment already has a fix in progress",
            status_code=409,
        )
    if usage:
        decision = check_harness_action(str(source_run.organization_id), "agent_fix")
        if not decision["allowed"]:
            return HostedHarnessError(
                "usage_limit",
                decision.get("reason") or "AI credits are used up",
                status_code=402,
            )
    return None


def _kind_or_none(environment: HostedHarnessJob) -> AgentKind | None:
    try:
        return agent_kind(environment)
    except HostedHarnessError:
        return None


def launch_options(source_run: HostedHarnessJob) -> LaunchOptions:
    """Everything the launch form shows and prefills for a finished run of an environment."""
    environment = source_run.environment
    kind = _kind_or_none(environment)
    version_id = (
        kind.base_version_id(source_run)
        if kind
        else source_run.agent_version_id or environment.agent_version_id
    )
    version = AgentVersion.no_workspace_objects.select_related(
        "agent_definition", "harness_runtime"
    ).get(id=version_id)
    scenarios = scenario_results(source_run)
    offered = kind.surfaces if kind else frozenset()
    number = (
        HostedHarnessJob.no_workspace_objects.filter(id=source_run.id)
        .annotate(number=run_number())
        .values_list("number", flat=True)
        .first()
    )
    environment_version = (
        source_run.environment_version or environment.environment_version
    )
    blocked = launch_blocker(source_run)
    return LaunchOptions(
        source_run=LaunchSourceRun(
            id=str(source_run.id),
            number=number,
            state=source_run.state,
            finished_at=source_run.terminal_at.isoformat()
            if source_run.terminal_at
            else None,
        ),
        environment=LaunchEnvironment(
            id=str(environment.id),
            name=environment_name(environment),
            version=environment_version.version_number if environment_version else None,
        ),
        agent=agent_identity(version),
        scenarios=scenarios,
        suggested=LaunchSuggested(
            target_scenario_keys=[
                one.key for one in scenarios if one.failing_sub_goals
            ],
            protected_scenario_keys=[one.key for one in scenarios if one.passed],
        ),
        surfaces=[
            LaunchSurface(
                id=surface,
                label=label,
                allowed=surface in offered,
                reason=None
                if surface in offered
                else "this kind of agent does not let a fix change it",
            )
            for surface, label in SURFACE_LABELS.items()
        ],
        defaults=LaunchDefaults(
            surfaces=[one for one in DEFAULT_SURFACES if one in offered],
            budget_credits=DEFAULT_BUDGET_CREDITS,
            max_minutes=DEFAULT_MAX_MINUTES,
        ),
        limits=LaunchLimits(
            budget_credits_max=BUDGET_CREDITS_MAX,
            max_minutes_max=MAX_MINUTES_MAX,
            scenarios_max=SCENARIOS_MAX,
        ),
        blocked=LaunchBlocked(code=blocked.code, message=blocked.message)
        if blocked
        else None,
    )
