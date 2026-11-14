"""Reads for "Fix my agent": the engine's brief, a check run's results, the run list."""

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import (
    Case,
    Count,
    DecimalField,
    Exists,
    ExpressionWrapper,
    F,
    Max,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from simulate.models import (
    AgentFixCandidate,
    AgentFixRun,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessSuiteMember,
    HarnessSuiteVersion,
    HostedHarnessConversation,
    HostedHarnessExecution,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.models.chat_message import ChatMessageModel
from simulate.models.test_execution import CallTranscript
from simulate.pydantic_schemas.agent_fix import (
    BUDGET_CREDITS_MAX,
    HELD_BACK_SHARE,
    HELD_BACK_VERIFIED_MIN,
    MAX_MINUTES_MAX,
    SCENARIOS_MAX,
    VARIATIONS_PER_CASE,
    AgentIdentity,
    CheckFailure,
    CheckRecord,
    CheckRunStatus,
    EngineBrief,
    EvalWarning,
    FailureOwner,
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
from simulate.services.hosted_harness import (
    HostedHarnessError,
    run_max_duration_seconds,
)
from simulate.services.run_compare import REGRESSED, verdict
from simulate.services.run_results_v3_scoring import (
    LiveEvals,
    judge_stored_eval,
    live_eval_specs,
)
from simulate.temporal.constants import (
    AGENT_FIX_VARIATIONS_RUN_GRACE_MINUTES,
    AGENT_FIX_VARIATIONS_WRITE_MINUTES,
)

TRANSCRIPT_CHARS = 8000
# A lost model is skipped for this long after it was lost: a short outage may be over.
LOST_MODELS_SKIPPED_FOR = timedelta(hours=1)
FINISHED_RUN_STATES = {
    HostedHarnessJob.State.COMPLETED,
    HostedHarnessJob.State.FAILED,
    HostedHarnessJob.State.CANCELED,
}
# The fix's check run of the base version on its failing cases and their variations.
VARIATIONS_LABEL = "variations"


def sub_goal_names(raw: Any) -> list[str]:
    """Sub-goal names from an authored scenario, whichever shape it stored them in."""
    names = []
    for item in raw or []:
        name = item.get("name") if isinstance(item, Mapping) else item
        if name:
            names.append(str(name))
    return names


def written_variations(suite_version: HarnessSuiteVersion | None) -> dict[str, str]:
    """The variations a fix's builder wrote into the fix's private suite version, each
    with the failing case it varies; none on any other suite version."""
    if suite_version is None or suite_version.fix_run_id is None:
        return {}
    return dict((suite_version.documents or {}).get("variations") or {})


def inventory(fix_run: AgentFixRun) -> list[InventoryScenario]:
    """Every scenario a check run may select: the fix's suite (the source run's pinned
    suite, or the fix's own when it wrote variations), or the environment's own
    scenarios when the source run was not pinned. A variation the base version passed is
    not a case of the fix, so it is left out."""
    suite_version_id = fix_run.suite_version_id or (
        fix_run.source_run.suite_version_id if fix_run.source_run else None
    )
    if suite_version_id:
        kept = FixRequest.model_validate(fix_run.request).variation_parents
        dropped = set(written_variations(fix_run.suite_version)) - set(kept)
        members = (
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id=suite_version_id
            )
            .exclude(scenario_key__in=dropped)
            .select_related("scenario")
        )
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
    """When the fix must have ended: its start plus the minutes the user allowed. While its
    first step writes variations, that step's own cap comes on top: the fix's minutes start
    once the step ends."""
    request = FixRequest.model_validate(fix_run.request)
    minutes = request.max_minutes
    if request.variations_pending:
        minutes += variations_minutes(fix_run)
    return (fix_run.started_at or fix_run.created_at) + timedelta(minutes=minutes)


def variations_minutes(fix_run: AgentFixRun) -> int:
    """The time of a fix's first step: the builder's cap to write the variations, then the
    current version's run on the failing cases and their variations within that run's own
    time limit, and a grace for the run to start and report. Until the run exists, its
    limit is the one a run of every target and its variations would get."""
    run = (
        HostedHarnessJob.no_workspace_objects.filter(
            fix_run=fix_run, fix_label=VARIATIONS_LABEL
        )
        .order_by("-created_at")
        .only("payload")
        .first()
    )
    if run is not None:
        seconds = int(run.payload["runtime"]["max_duration_seconds"])
    else:
        targets = len(FixRequest.model_validate(fix_run.request).target_scenario_keys)
        seconds = run_max_duration_seconds(
            int(fix_run.environment.payload["runtime"]["max_duration_seconds"]),
            targets * (1 + VARIATIONS_PER_CASE),
        )
    return (
        AGENT_FIX_VARIATIONS_WRITE_MINUTES
        + -(-seconds // 60)
        + AGENT_FIX_VARIATIONS_RUN_GRACE_MINUTES
    )


def queue_deadline(fix_run: AgentFixRun) -> datetime:
    """When a fix still waiting for room fails. A fix ahead may run its whole limit and end
    up to the grace after it, so a fix waits its own limit and the grace, or the queue
    setting when that is longer."""
    minutes = FixRequest.model_validate(fix_run.request).max_minutes
    wait = max(
        settings.AGENT_FIX_MAX_QUEUED_SECONDS,
        minutes * 60 + settings.AGENT_FIX_TOKEN_GRACE_SECONDS,
    )
    return fix_run.created_at + timedelta(seconds=wait)


def recent_lost_models(engine_notes: dict[str, Any] | None) -> dict[str, str]:
    """The fix's lost models, each with when it was lost, that were lost within
    LOST_MODELS_SKIPPED_FOR."""
    cutoff = timezone.now() - LOST_MODELS_SKIPPED_FOR
    lost = (engine_notes or {}).get("lost_models") or {}
    return {
        model: lost_at
        for model, lost_at in lost.items()
        if datetime.fromisoformat(lost_at) > cutoff
    }


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
        lost_models=list(recent_lost_models(fix_run.engine_notes)),
        last_attempt=fix_run.candidates.aggregate(last=Max("attempt"))["last"] or 0,
        committed_usd=float(committed_usd(fix_run)),
        deadline_at=fix_deadline(fix_run).isoformat(),
        source_failures=source_failures(
            fix_run.source_run, request, variations_run(fix_run)
        ),
    )


def variations_run(fix_run: AgentFixRun) -> HostedHarnessJob | None:
    """The base version's finished run on the fix's failing cases and their variations,
    which kept the variations it failed; a retry reads the one its failed fix finished."""
    return (
        HostedHarnessJob.no_workspace_objects.filter(
            fix_run_id__in=fix_lineage(fix_run),
            fix_label=VARIATIONS_LABEL,
            state=HostedHarnessJob.State.COMPLETED,
            deleted=False,
        )
        .order_by("-created_at")
        .first()
    )


def source_failures(
    source_run: HostedHarnessJob,
    request: FixRequest,
    variations_run: HostedHarnessJob | None = None,
) -> list[CheckRecord]:
    """The source run's evidence on each target the engine searches: the first trial it
    judged in full that failed a sub-goal, as a check record. A variation's evidence is its
    trial in ``variations_run``, the run that kept it. The engine reads one such trial a
    scenario, so the brief holds at most one record for each. The held-back targets stay
    out, as they do from everything the engine reads."""
    searched = set(request.target_scenario_keys) - set(request.held_back_scenario_keys)
    variations = searched & set(request.variation_parents)
    runs = Q(job=source_run, source_scenario__scenario_key__in=searched - variations)
    if variations_run is not None:
        runs |= Q(job=variations_run, source_scenario__scenario_key__in=variations)
    found: dict[str, tuple[Any, CheckRecord]] = {}
    for execution in (
        HostedHarnessExecution.no_workspace_objects.filter(runs)
        .select_related("source_scenario", "call_execution")
        .order_by("trial_index", "execution_key")
    ):
        key = execution.source_scenario.scenario_key
        receipt = (execution.call_execution.call_metadata or {}).get(
            "hosted_harness_receipt"
        )
        if key in found or not isinstance(receipt, Mapping):
            continue
        record = _record(key, execution.trial_index, receipt, "")
        if record.status == "ok" and False in record.sub_goals.values():
            found[key] = (execution.call_execution_id, record)
    transcripts = _transcripts(call_id for call_id, _ in found.values())
    return [
        record.model_copy(update={"transcript": transcripts.get(call_id, "")})
        for call_id, record in found.values()
    ]


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
        failure=(
            check_failure(run.failure)
            if run.state == HostedHarnessJob.State.FAILED
            else None
        ),
        records=records,
        parallelism=ran_at(run, len(executions)) if finished else None,
    )


def ran_at(run: HostedHarnessJob, trials: int | None = None) -> int:
    """The parallelism a run really had: what it was admitted at, less any degrade its last
    attempt recorded. Two arms are only comparable when these match. A run with fewer trials
    than its parallelism uses a world for each: that is no lower a parallelism, so it reads
    as what it asked for."""
    payload = run.payload or {}
    requested = (payload.get("runtime") or {}).get("parallelism") or 1
    clamped = (payload.get("metadata") or {}).get("parallelism_clamped") or {}
    admitted = clamped.get("admitted") or requested
    attempt = run.attempts.order_by("-attempt_number").first()
    ran = (attempt.effective_parallelism if attempt else None) or admitted
    if trials is None:
        trials = HostedHarnessExecution.no_workspace_objects.filter(job=run).count()
    return requested if ran >= min(requested, trials) else ran


# A plan's limit, met mid-run, is the user's to lift even though the platform reports it.
_USER_FAILURE_CODES = frozenset({"usage_exhausted"})
# Certification reports every failure as the environment's. A chat that timed out waiting
# for the agent's reply, or an agent slower to come up than the wait (infrastructure on any
# other run), says nothing about the agent. A reply that failed, or a connection the
# agent's own process refused or never accepted, stays the user's.
_CERTIFY_FAILED = "authoring_runtime_validation_failed"
_CERTIFY_TIMED_OUT = re.compile(
    r"chat_target_failed: (?!Connect|[^;\n]*\bConnection timed out\b)"
    r"(?:\w*Timeout\w*|\w+: [^;\n]*\btimed out\b)"
    r"|depends_on/depends_on_timeout"
)


def check_failure(failure: Mapping[str, Any] | None) -> CheckFailure | None:
    """A run's or a trial's failure, with who can fix it: the user for their environment
    (credentials, source, an agent that never came up) and their plan, else the platform."""
    if not isinstance(failure, Mapping):
        return None
    code = str(failure.get("code") or "unknown")
    message = str(failure.get("message") or "")
    user = failure.get("domain") == "environment" or code in _USER_FAILURE_CODES
    if code == _CERTIFY_FAILED and _CERTIFY_TIMED_OUT.search(message):
        user = False
    return CheckFailure(
        owner=FailureOwner.USER if user else FailureOwner.OURS,
        code=code,
        message=message[:500],
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
    failure = receipt.get("failure")
    # An agent that broke the conversation is judged, not excused.
    agent_broke_it = isinstance(failure, Mapping) and failure.get("domain") == "agent"
    call = receipt.get("call")
    duration_ms = call.get("duration_ms") if isinstance(call, Mapping) else None
    return CheckRecord(
        scenario_key=scenario_key,
        trial=trial,
        status="error" if error else "ok",
        error=error,
        failure=check_failure(failure) if error and not agent_broke_it else None,
        sub_goals={
            name: held for name, held in judged.items() if isinstance(held, bool)
        },
        reasons={
            str(one["name"]): str(one.get("reason") or "")
            for one in checks
            if one.get("name")
        },
        transcript=transcript,
        duration_s=duration_ms / 1000 if duration_ms else None,
    )


# The engine's speaker for a chat row's role.
_CHAT_SPEAKERS = {
    ChatMessageModel.RoleChoices.USER: "user",
    ChatMessageModel.RoleChoices.ASSISTANT: "assistant",
    ChatMessageModel.RoleChoices.TOOL: "tool_call_result",
}
# Chat ingestion means one exchange's rows to share a time, its user turn first.
_USER_TURN_FIRST = Case(
    When(role=ChatMessageModel.RoleChoices.USER, then=Value(0)), default=Value(1)
)


def _transcripts(call_ids: Iterable[Any]) -> dict[Any, str]:
    """Each call's conversation as "<role>: <text>" lines, the way the engine reads them: a
    voice call's CallTranscript rows, else a chat call's ChatMessage rows."""
    ids = list(call_ids)
    lines: dict[Any, list[str]] = {}
    rows = CallTranscript.no_workspace_objects.filter(
        call_execution_id__in=ids
    ).order_by("call_execution_id", "start_time_ms", "created_at")
    for row in rows.values_list("call_execution_id", "speaker_role", "content"):
        lines.setdefault(row[0], []).append(f"{row[1]}: {row[2]}")
    if chats := [one for one in ids if one not in lines]:
        messages = ChatMessageModel.no_workspace_objects.filter(
            call_execution_id__in=chats
        ).order_by("call_execution_id", "created_at", _USER_TURN_FIRST)
        for call_id, *row in messages.values_list(
            "call_execution_id", "role", "messages", "content", "tool_calls"
        ):
            lines.setdefault(call_id, []).extend(_chat_lines(*row))
    return {
        call_id: "\n".join(turns)[:TRANSCRIPT_CHARS] for call_id, turns in lines.items()
    }


def _chat_lines(role: str, messages: Any, content: Any, tool_calls: Any) -> list[str]:
    """One chat row as transcript lines. Hosted chat folds an exchange's agent side into one
    row: each item is its text, a call it made (``tool_calls``) or a call's result
    (``tool_call_result``), in order. A text with calls is followed by a line of them."""
    speaker = _CHAT_SPEAKERS.get(role, "unknown")
    items = [item for item in content or [] if isinstance(item, Mapping)] or [
        {"content": text} for text in messages or []
    ]
    lines = []
    for item in items:
        text = str(item.get("content") or "")
        if item.get("kind") in ("tool_calls", "tool_call_result"):
            lines.append(f"{item['kind']}: {text}")
            continue
        if text:
            lines.append(f"{speaker}: {text}")
        if item.get("tool_calls"):
            lines.append(f"tool_calls: {_calls_json(item['tool_calls'])}")
    if tool_calls:
        lines.append(f"tool_calls: {_calls_json(tool_calls)}")
    return lines


def _calls_json(calls: Any) -> str:
    """Tool calls as compact JSON of their names and arguments, from either the OpenAI
    shape (``function.name``, arguments as a JSON string) or a flat one."""
    named = []
    for call in calls if isinstance(calls, list) else []:
        if not isinstance(call, Mapping):
            continue
        function = call.get("function")
        function = function if isinstance(function, Mapping) else call
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                # Arguments that are not JSON are shown as the model sent them.
                pass
        named.append({"name": function.get("name"), "arguments": arguments})
    return json.dumps(named, separators=(",", ":"), ensure_ascii=False, default=str)


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


def committed_usd(fix_run: AgentFixRun) -> Decimal:
    """What the fix's limit is held against: its spend, and a worst case for each check run
    whose usage is not sealed yet."""
    return (
        with_spend(AgentFixRun.no_workspace_objects.filter(id=fix_run.id))
        .values_list("committed_usd", flat=True)
        .get()
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
        "run_test_id",
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
        .annotate(
            number=Subquery(number),
            source_run_number=run_number("source_run__"),
            retried=Exists(
                AgentFixRun.no_workspace_objects.filter(retried_from=OuterRef("pk"))
            ),
        )
        .select_related(
            "source_run",
            "environment__environment_version",
            "base_agent_version__agent_definition",
            "base_agent_version__harness_runtime",
        )
        .prefetch_related(
            "candidates__agent_version",
            "candidates__override_by",
            Prefetch("check_runs", queryset=check_runs, to_attr="check_run_list"),
        )
    )


def limited_validation(fix_run: AgentFixRun) -> bool:
    """Whether too few cases were held back to call this fix verified, however it scored:
    they must come from HELD_BACK_VERIFIED_MIN different cases of the run, a variation
    counting as the case it varies."""
    request = FixRequest.model_validate(fix_run.request)
    cases = {
        request.variation_parents.get(key, key)
        for key in request.held_back_scenario_keys
    }
    return len(cases) < HELD_BACK_VERIFIED_MIN


# The tiers the engine's final check records for a finalist, in order.
VERDICT_TIERS = ("static", "target", "held-back", "must-pass", "repeats", "ceilings")


def overridable_regressions(candidate: AgentFixCandidate) -> list[str]:
    """The must-pass checks a refused candidate regressed on, as "scenario key: check",
    when nothing else refused it: its verdict holds the final check's every tier, each one
    passed but must-pass, and no check is on a case the user protected. Such a candidate
    may ship on the user's reason; for any other the list is empty."""
    if candidate.outcome != AgentFixCandidate.Outcome.REJECTED:
        return []
    verdict = candidate.verdict or {}
    # Fail closed: a verdict missing a tier, or naming one twice, proves too little.
    tiers = [
        (tier.get("name"), tier.get("status")) for tier in verdict.get("tiers") or []
    ]
    # The frozen plan, not the verdict being checked, says whether the final check owed a
    # held-back tier: the engine drops that tier when it froze no held-back case.
    held_back = (candidate.fix_run.plan or {}).get("held_back")
    if not isinstance(held_back, int) or isinstance(held_back, bool):
        return []
    expected = [
        (name, "failed" if name == "must-pass" else "passed")
        for name in VERDICT_TIERS
        if held_back or name != "held-back"
    ]
    regressions = verdict.get("must_pass_regressions") or []
    # The engine names a lost protected case only in prose, so every regression on a
    # protected case counts as one: a protected case is never shipped regressed.
    protected = candidate.fix_run.request.get("protected_scenario_keys") or []
    if tiers != expected or any(
        check.startswith(f"{key}: ") for check in regressions for key in protected
    ):
        return []
    return list(regressions)


def is_retryable(fix_run: AgentFixRun) -> bool:
    """A failed fix not started again yet. Whatever failed it, the user may try once more;
    a fix that came through fix_runs_for_read answers without a query."""
    if fix_run.state != AgentFixRun.State.FAILED:
        return False
    retried = getattr(fix_run, "retried", None)
    return not (fix_run.retries.exists() if retried is None else retried)


def fix_lineage(fix_run: AgentFixRun) -> list[Any]:
    """The fix's id, then the ids of the failed fixes it retries, newest first."""
    ids, earlier = [fix_run.id], fix_run.retried_from_id
    while earlier is not None:
        ids.append(earlier)
        earlier = (
            AgentFixRun.no_workspace_objects.filter(id=earlier)
            .values_list("retried_from_id", flat=True)
            .first()
        )
    return ids


def check_run_parallelism(fix_run: AgentFixRun) -> int:
    """The parallelism a fix's next check run asks for. Arms compare only at one parallelism,
    and a baseline can run below the setting: a bundle sealed by an older runtime can hold its
    agent to one world while a new candidate's would not. So candidates follow the baseline."""
    baseline = (
        HostedHarnessJob.no_workspace_objects.filter(
            fix_run_id__in=fix_lineage(fix_run),
            agent_version_id=fix_run.base_agent_version_id,
            state=HostedHarnessJob.State.COMPLETED,
            deleted=False,
        )
        .order_by("-created_at")
        .first()
    )
    if baseline is None:
        return settings.AGENT_FIX_CHECK_RUN_PARALLELISM
    return ran_at(baseline)


def base_check_runs(fix_run: AgentFixRun) -> list[HostedHarnessJob]:
    """The base version's check runs a fix's read shows. A retry reuses what the failed fix
    finished, and those runs stay on the failed fix, so they are added here."""
    base = fix_run.base_agent_version_id
    runs = [run for run in fix_run.check_run_list if run.agent_version_id == base]
    earlier = fix_lineage(fix_run)[1:]
    if not earlier:
        return runs
    labels = {run.fix_label for run in runs}
    for run in HostedHarnessJob.no_workspace_objects.filter(
        fix_run_id__in=earlier,
        agent_version_id=base,
        state=HostedHarnessJob.State.COMPLETED,
        deleted=False,
    ).order_by("-created_at"):
        if run.fix_label not in labels:
            labels.add(run.fix_label)
            runs.append(run)
    return runs


def _eval_trials(
    run_ids: Iterable[Any], evals: LiveEvals
) -> dict[str, dict[str, list[int]]]:
    """Each eval's trials on the given runs, by scenario: [passed, judged]. An eval that
    errored says nothing about the agent, so it is not a trial."""
    trials: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0])
    )
    for scenario_key, outputs in HostedHarnessExecution.no_workspace_objects.filter(
        job_id__in=run_ids
    ).values_list("source_scenario__scenario_key", "call_execution__eval_outputs"):
        for config_id, live in evals.items():
            outcome = judge_stored_eval(
                (outputs or {}).get(config_id), live.spec
            ).outcome
            if outcome in ("passed", "failed"):
                trials[config_id][scenario_key][0] += outcome == "passed"
                trials[config_id][scenario_key][1] += 1
    return trials


def _pooled(
    trials: Mapping[str, list[int]], scenario_keys: Iterable[str]
) -> tuple[int, int]:
    rows = [trials[key] for key in scenario_keys]
    return sum(one[0] for one in rows), sum(one[1] for one in rows)


def eval_warnings(fix_run: AgentFixRun) -> list[EvalWarning]:
    """The platform evals the winner does worse on than the base version, on the scenarios
    both ran. They do not decide a fix's verdict, so the person who reviews it reads them
    here. Evals land after their call ends, so this reads them rather than a snapshot."""
    winner = next(
        (
            one
            for one in fix_run.candidates.all()
            if one.outcome == AgentFixCandidate.Outcome.WINNER
        ),
        None,
    )
    if winner is None:
        return []
    evals = live_eval_specs(fix_run.environment.run_test_id)
    mine = _eval_trials(
        [
            run.id
            for run in fix_run.check_run_list
            if run.agent_version_id == winner.agent_version_id
        ],
        evals,
    )
    base = _eval_trials([run.id for run in base_check_runs(fix_run)], evals)
    warnings = []
    for config_id, live in evals.items():
        shared = mine[config_id].keys() & base[config_id].keys()
        passed, trials = _pooled(mine[config_id], shared)
        base_passed, base_trials = _pooled(base[config_id], shared)
        if verdict(passed, trials, base_passed, base_trials)[0] == REGRESSED:
            warnings.append(
                EvalWarning(
                    name=live.config.name,
                    pass_rate=passed / trials,
                    base_pass_rate=base_passed / base_trials,
                    trials=trials,
                    base_trials=base_trials,
                )
            )
    return warnings


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
# What the launch form starts from: the instructions only, within 1,500 credits and the time
# default_max_minutes gives the run's suite.
DEFAULT_SURFACES = [Surface.PROMPT]
DEFAULT_BUDGET_CREDITS = 1500
DEFAULT_MAX_MINUTES = 180


def default_max_minutes(cases: int) -> int:
    """A fix's default time limit for a suite of ``cases`` scenarios: Reproduce and Verify run
    every one. Three hours up to 30 cases, then 4 minutes a case (5 hours at 60), within
    the limit a launch accepts."""
    return min(MAX_MINUTES_MAX, max(DEFAULT_MAX_MINUTES, 60 + 4 * cases))


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


def fewest_failing_cases() -> int:
    """The fewest failing cases a fix starts from: below the floor it writes variations of
    each, and with every variation failing these just reach it."""
    floor = settings.AGENT_FIX_MIN_FAILING_CASES
    return -(-floor // (1 + VARIATIONS_PER_CASE))


def verified_cases_min() -> int:
    """The fewest failing cases a fix that writes variations can end verified from. Its
    held-back cases must come from HELD_BACK_VERIFIED_MIN cases, held back each with its
    variations, within one in HELD_BACK_SHARE of all its cases; every case brings at most
    VARIATIONS_PER_CASE variations, so the cases kept to search on hold at least
    (HELD_BACK_SHARE - 1) times what is held back."""
    held = HELD_BACK_VERIFIED_MIN
    return held + -(-(HELD_BACK_SHARE - 1) * held // (1 + VARIATIONS_PER_CASE))


def variations_blocker(source_run: HostedHarnessJob) -> HostedHarnessError | None:
    """What would refuse a fix that first writes variations of its failing cases, on top of
    launch_blocker; None when nothing would. The environment's builder writes them, on top
    of any unsaved scenario changes, so those must be saved or discarded first."""
    from simulate.services.harness_versioning import drafts

    environment = source_run.environment
    if source_run.suite_version_id is None or source_run.environment_version_id is None:
        return HostedHarnessError(
            "fix_variations_unpinned",
            "variations need a run pinned to scenario and environment versions; run the"
            " environment again",
            status_code=409,
        )
    if environment.state != HostedHarnessJob.State.COMPLETED:
        return HostedHarnessError(
            "environment_busy",
            "The environment isn't ready; variations can't be written now",
            status_code=409,
        )
    moved = variations_source_moved(environment, source_run)
    if moved is not None:
        return moved
    if HostedHarnessConversation.no_workspace_objects.filter(
        job=environment, state=HostedHarnessConversation.State.RETIRED
    ).exists():
        return HostedHarnessError(
            "conversation_workspace_not_ready",
            "This environment's saved files are no longer available; rebuild it to"
            " write variations.",
            status_code=409,
        )
    try:
        drafts.require_no_rebuild(environment, piece="suite")
    except HostedHarnessError as exc:
        return exc
    if drafts.fresh_draft(environment, "suite") is not None:
        return HostedHarnessError(
            "fix_suite_draft_open",
            "Save or discard the environment's unsaved scenario changes first: the fix"
            " writes its variations of the failing cases on top of them",
            status_code=409,
        )
    return None


def variations_source_moved(
    environment: HostedHarnessJob, source_run: HostedHarnessJob
) -> HostedHarnessError | None:
    """Why variations can't be written from this run now, if its environment's scenarios
    or setup moved since it ran: the builder writes them from the environment as it is,
    and the fix's suite holds the run's cases as they ran, so both must be one basis."""
    if (environment.suite_version_id, environment.environment_version_id) == (
        source_run.suite_version_id,
        source_run.environment_version_id,
    ):
        return None
    return HostedHarnessError(
        "fix_variations_source_moved",
        "The environment's scenarios or setup changed since this run. Run the"
        " environment again and start the epoch from that run: its variations are"
        " written from the cases as they ran.",
        status_code=409,
    )


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
    failing = sum(1 for one in scenarios if one.failing_sub_goals)
    # Below the floor a fix's suite also holds the variations it writes.
    variations = (
        VARIATIONS_PER_CASE * failing
        if failing < settings.AGENT_FIX_MIN_FAILING_CASES
        else 0
    )
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
    variations_blocked = variations_blocker(source_run)
    return LaunchOptions(
        source_run=LaunchSourceRun(
            id=str(source_run.test_execution_id),
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
            max_minutes=default_max_minutes(len(scenarios) + variations),
        ),
        limits=LaunchLimits(
            budget_credits_max=BUDGET_CREDITS_MAX,
            max_minutes_max=MAX_MINUTES_MAX,
            scenarios_min=fewest_failing_cases(),
            cases_min=settings.AGENT_FIX_MIN_FAILING_CASES,
            variations_per_case=VARIATIONS_PER_CASE,
            verified_cases_min=verified_cases_min(),
            scenarios_max=SCENARIOS_MAX,
        ),
        blocked=LaunchBlocked(code=blocked.code, message=blocked.message)
        if blocked
        else None,
        variations_blocked=LaunchBlocked(
            code=variations_blocked.code, message=variations_blocked.message
        )
        if variations_blocked
        else None,
    )
