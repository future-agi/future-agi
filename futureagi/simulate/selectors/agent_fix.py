"""Reads for "Fix my agent": the engine's brief, a check run's results, the run list."""

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import (
    DecimalField,
    ExpressionWrapper,
    F,
    Prefetch,
    Q,
    QuerySet,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce

from simulate.models import (
    AgentFixRun,
    HarnessSuiteMember,
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.models.test_execution import CallTranscript
from simulate.pydantic_schemas.agent_fix import (
    CheckRecord,
    CheckRunStatus,
    EngineBrief,
    FixRequest,
    InventoryScenario,
)
from simulate.services.harness_environment import environment_name
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


def failing_sub_goals(
    source_run: HostedHarnessJob, scenario_keys: list[str]
) -> list[str]:
    """The sub-goals that did not hold on the given scenarios of a finished run."""
    executions = HostedHarnessExecution.no_workspace_objects.filter(
        job=source_run, source_scenario__scenario_key__in=scenario_keys
    ).select_related("call_execution")
    failing: list[str] = []
    for execution in executions:
        receipt = (execution.call_execution.call_metadata or {}).get(
            "hosted_harness_receipt"
        )
        if not isinstance(receipt, Mapping):
            continue
        for name, held in judged_sub_goals(receipt).items():
            if held is False and name not in failing:
                failing.append(name)
    return failing


def with_committed_usd(runs: QuerySet[AgentFixRun]) -> QuerySet[AgentFixRun]:
    """Each fix's ``committed_usd``: the engine's own spend plus a worst-case figure for
    every check run so far, which is what its budget is held against."""
    executions = Coalesce(
        Sum("check_runs__scenario_count", filter=Q(check_runs__deleted=False)), 0
    )
    worst_case = Value(Decimal(settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION))
    return runs.annotate(
        committed_usd=ExpressionWrapper(
            F("spent_usd") + executions * worst_case,
            output_field=DecimalField(max_digits=12, decimal_places=4),
        )
    )


def fix_runs_for_read(runs: QuerySet[AgentFixRun]) -> QuerySet[AgentFixRun]:
    """Fixes with everything their read shows: committed spend, candidates, check runs."""
    check_runs = HostedHarnessJob.no_workspace_objects.only(
        "id",
        "fix_run_id",
        "fix_label",
        "state",
        "test_execution_id",
        "agent_version_id",
        "created_at",
    ).order_by("created_at")
    return with_committed_usd(runs).prefetch_related(
        "candidates__agent_version",
        Prefetch("check_runs", queryset=check_runs, to_attr="check_run_list"),
    )


def fix_runs_for_environment(environment: HostedHarnessJob) -> QuerySet[AgentFixRun]:
    return fix_runs_for_read(
        AgentFixRun.no_workspace_objects.filter(environment=environment)
        .select_related("base_agent_version", "source_run")
        .order_by("-created_at")
    )
