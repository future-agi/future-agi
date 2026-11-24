"""Reads for an outside attempt: a fix a person's own coding agent submits, which an Epoch
verifies. The brief the coding agent fixes from, the request an attempt runs on, the
attempt's verdict, and an Epoch read with no held-back case named in it.

The held-back guarantee is not credential isolation. Nothing these reads give a coding
agent names a held-back case, the brief tells it not to read one anywhere else, and Future
AGI runs every candidate and computes its verdict."""

import math
from collections.abc import Iterable, Mapping
from decimal import Decimal
from hashlib import sha256
from typing import Any

from django.db.models import Exists, OuterRef, Q, QuerySet
from pydantic import ValidationError

from simulate.models import AgentFixCandidate, AgentFixRun, HostedHarnessJob
from simulate.pydantic_schemas.agent_fix import (
    BUDGET_CREDITS_MAX,
    OUTSIDE_ATTEMPTS_MAX,
    SCENARIOS_MAX,
    AttemptTier,
    AttemptVerdict,
    CheckRecord,
    CodingAgentBrief,
    CodingAgentTarget,
    Ending,
    FixRequest,
    FixStep,
    LaunchBlocked,
    OutsideAttempts,
    OutsideCandidate,
)
from simulate.selectors.agent_fix import (
    base_commit,
    default_max_minutes,
    fix_step,
    launch_options,
    measured_automatically,
    models_called,
    outside_attempt_hold_usd,
    scenario_results,
    source_failures,
    source_run_usage,
    verdict_tiers,
)
from simulate.selectors.evidence_import import evidence_origins
from simulate.services.agent_fix_agents import agent_kind
from simulate.services.agent_fix_engine import (
    MAX_PATCH_BYTES,
    archive_files,
    run_source_archive,
)
from simulate.services.agent_fix_runs import split_held_back
from simulate.services.hosted_harness import HostedHarnessError

HELD_BACK_RULE = (
    "Do not read held-back cases or any held-back field of an Epoch. Future AGI checks"
    " your fix on cases you were not given."
)
# The tiers whose detail a coding agent may read, as the engine tells its own proposer
# (verdict-service.mjs feedbackFor): the others are named, never shown, because the
# held-back, must-pass and repeats checks run on cases it was not given.
FEEDBACK_TIERS = frozenset({"static", "target"})
REASON_CHARS = 300
# The default budget's room over the worst-case hold: a check run holds its actual cost
# once that is more than the estimate.
BUDGET_HEADROOM = Decimal("1.25")


def outside_attempts(source_run: HostedHarnessJob) -> QuerySet[AgentFixRun]:
    """The outside attempts on a run, oldest first. An engine's own fix has no number."""
    return AgentFixRun.no_workspace_objects.filter(
        source_run=source_run, request__outside_candidate__number__isnull=False
    ).order_by("created_at")


def used_attempts(source_run: HostedHarnessJob) -> QuerySet[AgentFixRun]:
    """The outside attempts that used one of the run's attempts. One that failed with no
    verdict on its change, the platform's failure or the agent's own, told its coding
    agent nothing, so it does not count."""
    verdicts = AgentFixCandidate.no_workspace_objects.filter(
        fix_run=OuterRef("pk"), verdict__isnull=False
    )
    return outside_attempts(source_run).exclude(
        Q(state=AgentFixRun.State.FAILED) & ~Exists(verdicts)
    )


def attempts_used(attempts: int) -> HostedHarnessError | None:
    """The refusal once a run has had all its outside attempts; None before."""
    if attempts < OUTSIDE_ATTEMPTS_MAX:
        return None
    return HostedHarnessError(
        "fix_attempts_used",
        f"This run has had its {OUTSIDE_ATTEMPTS_MAX} attempts. Run the environment"
        " again, then fix the new run.",
        status_code=409,
    )


def _attempt_scope(
    source_run: HostedHarnessJob, previous: AgentFixRun | None
) -> FixRequest:
    """The cases an outside attempt is checked on. A later attempt keeps the request of the
    one before it, so it reads that one's baseline again. The first takes the run's failing
    cases as targets and its passing ones as protected, SCENARIOS_MAX of each at most, and
    holds back a third of the targets (none below twelve). Its budget is sized by
    outside_attempt_request."""
    if previous is not None:
        return FixRequest.model_validate(previous.request)
    environment = source_run.environment
    if environment is None:
        raise HostedHarnessError(
            "fix_source_not_a_run", "Epoch starts from a run", status_code=422
        )
    seed = str(source_run.id)
    ran = scenario_results(source_run)
    failing = set(_capped([one.key for one in ran if one.failing_sub_goals], seed))
    by_scenario = {one.key: one.failing_sub_goals for one in ran if one.key in failing}
    if not by_scenario:
        raise HostedHarnessError(
            "fix_targets_passed",
            "Nothing failed in this run, so there is nothing to fix. Pick a run with"
            " failing cases.",
            status_code=422,
        )
    usage = source_run_usage(source_run)
    seen = models_called(usage)
    targets = list(by_scenario)
    try:
        return FixRequest(
            target_scenario_keys=targets,
            protected_scenario_keys=_capped(
                [one.key for one in ran if one.passed], seed
            ),
            held_back_scenario_keys=split_held_back(seed, by_scenario),
            failing_sub_goals=list(
                dict.fromkeys(name for names in by_scenario.values() for name in names)
            ),
            # The coding agent edits the agent's own files, so every part is open to it.
            surfaces=sorted(agent_kind(environment).surfaces),
            budget_usd=BUDGET_CREDITS_MAX / 100,
            max_minutes=default_max_minutes(len(ran)),
            evidence=evidence_origins(environment, targets),
            allowed_models=seen,
            observes_models=bool(seen),
            meters_agent_cost=bool(usage),
            measured_automatically=bool(usage) and measured_automatically(source_run),
        )
    except ValidationError as exc:
        raise _scope_refused() from exc


def _capped(keys: list[str], seed: str) -> list[str]:
    """At most SCENARIOS_MAX of ``keys``, in the run's order: with more, the ones a stable
    hash of the run and the key picks, so every attempt on the run picks the same."""
    if len(keys) <= SCENARIOS_MAX:
        return keys
    ranked = sorted(
        keys, key=lambda key: sha256(f"{seed}:cap:{key}".encode()).hexdigest()
    )
    picked = set(ranked[:SCENARIOS_MAX])
    return [key for key in keys if key in picked]


def _scope_refused() -> HostedHarnessError:
    return HostedHarnessError(
        "fix_attempt_scope_invalid",
        "This run has more cases or models than an attempt can check. Run the"
        " environment on fewer scenarios, then fix the new run.",
        status_code=422,
    )


def outside_attempt_request(
    source_run: HostedHarnessJob,
    previous: AgentFixRun | None,
    *,
    number: int,
    summary: str,
    locked_paths: list[str] | None,
    budget_credits: int | None,
) -> FixRequest:
    """The request of the next outside attempt on a run. Its locked paths are the previous
    attempt's and ``locked_paths``: an attempt may lock more, never unlock. Its budget is
    attempt_budget_credits."""
    scope = _attempt_scope(source_run, previous)
    budget_credits = attempt_budget_credits(source_run, scope, previous, budget_credits)
    try:
        return FixRequest.model_validate(
            {
                **scope.model_dump(mode="json"),
                "locked_paths": list(
                    dict.fromkeys([*scope.locked_paths, *(locked_paths or [])])
                ),
                "budget_usd": budget_credits / 100,
                "outside_candidate": OutsideCandidate(
                    summary=summary, number=number
                ).model_dump(mode="json"),
            }
        )
    except ValidationError as exc:
        raise _scope_refused() from exc


def attempt_budget_credits(
    source_run: HostedHarnessJob,
    scope: FixRequest,
    previous: AgentFixRun | None,
    given: int | None = None,
) -> int:
    """An attempt's limit in credits. Left out, its worst-case hold with BUDGET_HEADROOM
    on top, since a check run holds its actual cost once that is more than the estimate.
    Given, at least the hold, or the attempt's own check runs would be refused. An
    attempt that needs more than an Epoch may use is refused, never cut short."""
    hold = max(
        1, math.ceil(outside_attempt_hold_usd(source_run, scope, previous) * 100)
    )
    credits = max(1, math.ceil(hold * BUDGET_HEADROOM)) if given is None else given
    if max(hold, credits) > BUDGET_CREDITS_MAX:
        needed = max(hold, credits) if given is None else hold
        raise HostedHarnessError(
            "fix_attempt_too_large",
            f"An attempt on this run could need {needed:,} credits, more than the"
            f" {BUDGET_CREDITS_MAX:,} an Epoch may use. Run the environment on fewer"
            " scenarios, then fix the new run.",
            status_code=422,
        )
    if credits < hold:
        raise HostedHarnessError(
            "fix_budget_below_hold",
            f"This attempt's check runs can hold up to {hold:,} credits. Give a budget"
            " of at least that, or leave it out to use the default.",
            status_code=422,
        )
    return credits


def coding_agent_brief(source_run: HostedHarnessJob) -> CodingAgentBrief:
    """What a person's own coding agent reads to fix a finished run: its failing cases
    (never a held-back one), the agent's files, the rules, and how to submit."""
    options = launch_options(source_run)
    attempts = list(outside_attempts(source_run))
    used = used_attempts(source_run).count()
    previous = attempts[-1] if attempts else None
    blocked = options.blocked
    request, files = None, []
    try:
        request = _attempt_scope(source_run, previous)
        attempt_budget_credits(source_run, request, previous)
        files = sorted(archive_files(run_source_archive(source_run)))
    except HostedHarnessError as exc:
        blocked = blocked or LaunchBlocked(code=exc.code, message=exc.message)
    if blocked is None and (refused := attempts_used(used)):
        blocked = LaunchBlocked(code=refused.code, message=refused.message)
    held_back = set(request.held_back_scenario_keys) if request else set()
    scenarios = {one.key: one for one in options.scenarios}
    targets = [
        CodingAgentTarget(
            key=key,
            name=scenarios[key].name if key in scenarios else key,
            failing_sub_goals=scenarios[key].failing_sub_goals
            if key in scenarios
            else [],
        )
        for key in (request.target_scenario_keys if request else [])
        if key not in held_back
    ]
    failures = source_failures(source_run, request) if request else []
    commit = base_commit(source_run)
    return CodingAgentBrief(
        source_run=options.source_run,
        environment=options.environment,
        agent=options.agent,
        base_commit=commit,
        files=files,
        targets=targets,
        protected_scenario_keys=request.protected_scenario_keys if request else [],
        locked_paths=request.locked_paths if request else [],
        failures=failures,
        attempts=OutsideAttempts(used=used, max=OUTSIDE_ATTEMPTS_MAX),
        blocked=blocked,
        instructions=_instructions(
            source_run_id=options.source_run.id,
            environment=options.environment.name,
            targets=targets,
            failures=failures,
            commit=commit,
            used=used,
            locked=request.locked_paths if request else [],
        ),
    )


def _instructions(
    *,
    source_run_id: str,
    environment: str,
    targets: list[CodingAgentTarget],
    failures: list[CheckRecord],
    commit: str,
    used: int,
    locked: list[str],
) -> str:
    """The brief as the prompt a coding agent follows, in markdown."""
    reasons = {
        record.scenario_key: {
            name: record.reasons.get(name, "")[:REASON_CHARS]
            for name, held in record.sub_goals.items()
            if held is False
        }
        for record in failures
    }
    lines = [
        f"# Fix the failed run on {environment}",
        "",
        "Future AGI ran your agent on its test cases, and the cases below failed. Change"
        " the agent so that they pass, and keep the cases that pass today passing. Future"
        " AGI then checks your change as an Epoch and gives a verdict.",
        "",
        "## What failed",
        "",
    ]
    for target in targets:
        lines.append(
            f"- **{target.name}** (`{target.key}`): did not meet"
            f" {', '.join(target.failing_sub_goals)}."
        )
        lines.extend(
            f"  - {name}: {why}"
            for name, why in reasons.get(target.key, {}).items()
            if why
        )
    lines += [
        "",
        "Each failed conversation is in the brief's `failures`. To read them, run"
        f" `fagi epoch brief --run {source_run_id} --json`.",
        "",
        "## Rules for your change",
        "",
        "- Change the agent's files that the brief lists in `files`. You can add new"
        " files. A file you leave out of the tree is deleted.",
        "- Do not change or delete a locked file. Future AGI refuses an attempt that does."
        + (
            f" Earlier attempts on this run locked: {', '.join(f'`{one}`' for one in locked)}."
            if locked
            else ""
        ),
        f"- Keep the change under {MAX_PATCH_BYTES // (1024 * 1024)} MB as a patch.",
    ]
    if commit:
        lines.append(f"- Start from commit `{commit}`. This is the commit the run ran.")
    else:
        lines.append(
            "- Work in a git working tree of the agent's files, and submit from it."
            " `fagi epoch submit` sends the files git tracks, and new files git would"
            " track."
        )
    left = max(0, OUTSIDE_ATTEMPTS_MAX - used)
    lines += [
        "",
        "## Submit your fix",
        "",
        "```sh",
        f'fagi epoch submit --run {source_run_id} --summary "<what you changed and why>"',
        "fagi epoch verdict <attempt-id> --wait",
        "```",
        "",
        f"A run takes at most {OUTSIDE_ATTEMPTS_MAX} attempts. This run has {left} left."
        " Each attempt runs the agent on the run's cases and uses credits. A verdict can"
        " take tens of minutes.",
        "",
        "The command is `fagi`. To install it, run `npm install -g @future-agi/cli`.",
        "",
        "## Held-back cases",
        "",
        HELD_BACK_RULE,
        "",
    ]
    return "\n".join(lines)


def attempt_verdict(fix_run: AgentFixRun) -> AttemptVerdict:
    """An outside attempt's verdict as its coding agent reads it: the held-back, must-pass
    and repeats tiers by status only, and no must-pass check on a held-back case."""
    request = FixRequest.model_validate(fix_run.request)
    if request.outside_candidate is None:
        raise HostedHarnessError("not_found", "Not found", status_code=404)
    held_back = set(request.held_back_scenario_keys)
    candidate = next(iter(fix_run.candidates.all()), None)
    verdict = (candidate.verdict if candidate else None) or {}
    report = fix_run.report or {}
    step = fix_step(fix_run)
    return AttemptVerdict(
        id=str(fix_run.id),
        number=request.outside_candidate.number,
        state=fix_run.state,
        step=FixStep(step) if step else None,
        ending=report.get("ending"),
        failure=LaunchBlocked.model_validate(fix_run.failure)
        if fix_run.failure
        else None,
        summary=shown_summary(
            report,
            outside=True,
            candidates=int(candidate is not None),
            failure=fix_run.failure,
        ),
        tiers=[
            AttemptTier.model_validate(hidden_tier(tier.model_dump()))
            for tier in (verdict_tiers(candidate) if candidate else [])
        ],
        must_pass_regressions=hidden_checks(
            verdict.get("must_pass_regressions") or [], held_back
        ),
        patch_available=candidate is not None,
        pull_request_url=candidate.pull_request_url if candidate else "",
    )


def shown_summary(
    report: Mapping[str, Any],
    *,
    outside: bool,
    candidates: int,
    failure: Mapping[str, Any] | None,
) -> str:
    """The engine's closing summary, for a reader not given the held-back cases. It joins
    every failed tier's detail, held-back ids and rates included, and a failed engine's
    is its own error text. So it is served only for a static refusal: a verify run that
    uploaded no candidate and found no improvement. Any other ending reads as the
    platform's words for its failure (agent_fix_engine._ending_failure), or blank."""
    if outside and candidates == 0 and report.get("ending") == Ending.NO_IMPROVEMENT:
        return str(report.get("summary") or "")
    return str((failure or {}).get("message") or "")


def hidden_checks(checks: Iterable[str], held_back: set[str]) -> list[str]:
    """Must-pass checks ("scenario key: check") without those on a held-back case."""
    return [
        check
        for check in checks
        if not any(check.startswith(f"{key}: ") for key in held_back)
    ]


def hidden_tier(tier: Mapping[str, Any]) -> dict[str, Any]:
    """A verdict tier, its detail and its parts' details kept only for FEEDBACK_TIERS."""
    if tier["name"] in FEEDBACK_TIERS:
        return dict(tier)
    hidden = {**tier, "detail": ""}
    if "parts" in tier:
        hidden["parts"] = [{**part, "detail": ""} for part in tier["parts"]]
    return hidden


def hidden_verdict(verdict: Mapping[str, Any], held_back: set[str]) -> dict[str, Any]:
    """A candidate's verdict with no held-back case named in it. The reason joins tier
    details, so it is rebuilt from the tiers left; the held-back pass rate is a result
    on held-back cases, and they come back as a tier status only."""
    tiers = [hidden_tier(tier) for tier in verdict.get("tiers") or []]
    failed = [
        f"{tier['name']}: {tier['detail']}"
        if tier["name"] in FEEDBACK_TIERS
        else f"{tier['name']}: failed"
        for tier in tiers
        if tier["status"] == "failed"
    ]
    accepted = "passed every tier" if verdict.get("accepted") else "not accepted"
    return {
        **verdict,
        "reason": "; ".join(failed) or accepted,
        "held_back_pass_rate": None,
        "must_pass_regressions": hidden_checks(
            verdict.get("must_pass_regressions") or [], held_back
        ),
        "must_pass_within_noise": hidden_checks(
            verdict.get("must_pass_within_noise") or [], held_back
        ),
        "tiers": tiers,
    }


def hidden_epoch(data: Mapping[str, Any], held_back: set[str]) -> dict[str, Any]:
    """An Epoch's read (AgentFixRunSerializer) with no held-back case named in it, every
    field kept: the read every caller but a person's signed-in session gets."""
    request = data["request"]
    report = data.get("report")
    return {
        **data,
        "request": {
            **request,
            "target_scenario_keys": [
                key for key in request["target_scenario_keys"] if key not in held_back
            ],
            "held_back_scenario_keys": [],
            # The sub-goals that failed on every target, held-back ones included: one
            # only a held-back case failed would show what it tests.
            "failing_sub_goals": [] if held_back else request["failing_sub_goals"],
            # A variation is held back with the case it varies.
            "variation_parents": {
                key: parent
                for key, parent in request["variation_parents"].items()
                if key not in held_back and parent not in held_back
            },
        },
        "report": {
            **report,
            "summary": shown_summary(
                report,
                outside=request.get("outside_candidate") is not None,
                candidates=len(data["candidates"]),
                failure=data.get("failure"),
            ),
        }
        if report
        else None,
        "candidates": [
            {
                **candidate,
                "verdict": hidden_verdict(candidate["verdict"], held_back)
                if candidate["verdict"]
                else None,
                "override": _hidden_override(candidate["override"], held_back),
            }
            for candidate in data["candidates"]
        ],
    }


def _hidden_override(
    override: Mapping[str, Any], held_back: set[str]
) -> dict[str, Any]:
    """A refused candidate's override, its held-back regressions left out. With only those
    left out it reads as not allowed: what it would ship over is not shown."""
    regressions = hidden_checks(override["regressions"], held_back)
    return {
        **override,
        "allowed": bool(override["allowed"] and regressions),
        "regressions": regressions,
    }
