"""Deciding whether a run passed, in two parts that are never mixed.

**State** is settled by looking at the database. The order exists or it does not, and no amount of
fluent conversation changes the answer. This is the half worth trusting, and it is checked with
the same code the build stage uses to check its own sequences, so a suite cannot pass its gate
and then be graded by a different rule.

**Conduct** is what the agent said and what it refused, which needs judgement, so it is judged.
Kept separate and reported separately, so nobody reads a pass as meaning the data is right when
what was actually established is that an opinion was favourable.

The judge is given the tool calls as well as the transcript, because the failure most worth
catching is an agent that says it did something it never did. Reading only the words makes that
failure invisible; reading both makes it obvious.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

from ..config import (
    UNWANTED,
    chosen_model,
    gate_hooks,
    permission_gate,
    provider_env,
)
from ..contract import AgentContract
from ..scenario import Scenario
from ..session import Stage
from ..tools import qualified
from ..checks import Outcome, run_check
from ..catalogue import Catalogue, SuiteEval
from ..world.runtime import GeneratedWorld
from .conversation import Transcript

JUDGE_SERVER = "verdict"


@dataclass
class Checkpoint:
    """One thing that had to be true, and whether it was.

    Every expectation is named and reported whether it held or not. Reporting only the failures
    answers "did it pass" but never "how much of this did it get right", and a scenario that
    settles eight things and misses one is a different result from one that misses everything.
    """

    name: str
    kind: str
    passed: bool
    detail: str = ""
    # The eval that decided it, where one did. Empty for anything settled by code or judged here.
    by: str = ""

    def line(self) -> str:
        return f"  [{'x' if self.passed else ' '}] {self.kind}: {self.name}" + (
            f"\n        {self.detail}" if self.detail and not self.passed else ""
        )


@dataclass
class Judgement:
    claim: str
    kind: str
    holds: bool
    why: str = ""
    # Which eval decided this, when it was decided by one rather than here.
    by: str = ""


@dataclass
class Result:
    scenario: str
    tests: str = ""
    state_failures: list[str] = field(default_factory=list)
    conduct: list[Judgement] = field(default_factory=list)
    crashes: list[str] = field(default_factory=list)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    ended: str = ""
    turns: int = 0
    calls: int = 0
    spent_usd: float = 0.0
    transcript: str = ""
    # The same conversation with its speakers still separate. ``transcript`` is rendered for a
    # person to read, and reading it back apart again cannot be done safely once a turn spans
    # more than one line -- so anything that needs the turns keeps them from here instead.
    exchanges: list[dict] = field(default_factory=list)
    # Kept alongside the transcript because a run is diagnosed by comparing them: what the
    # agent said it did against what it actually did.
    actions: str = ""
    # Where this run's audio was left, empty when there is none. A spoken run is diagnosed by
    # listening to it: a transcript will not tell you the agent talked over the caller, or that
    # what it heard was not what was said.
    recording: str = ""
    seconds: float = 0.0
    # Every call in full, for the timeline and for anyone asking what one call did. The count is
    # kept separately in ``calls`` because a summary should not have to load all of them.
    calls_detail: list[dict] = field(default_factory=list)
    # What the thing that ran this measured about it: scores, why it ended, what the simulated
    # caller cost, and what each evidence source can prove. Carried rather than recomputed.
    measured: dict = field(default_factory=dict)
    # Every recording of this run that exists, best first, so the page can fall back instead of
    # showing a player with nothing behind it.
    tracks: list[dict] = field(default_factory=list)
    # What stopped this scenario being run at all, as opposed to what the agent got wrong. A
    # scenario that never ran must not read as a scenario the agent passed.
    problems: list[str] = field(default_factory=list)

    @property
    def conduct_failures(self) -> list[Judgement]:
        return [item for item in self.conduct if not item.holds]

    @property
    def passed(self) -> bool:
        return (
            not self.state_failures
            and not self.conduct_failures
            and not self.crashes
            and not self.problems
        )

    @property
    def met(self) -> int:
        return sum(1 for check in self.checkpoints if check.passed)

    def line(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        if self.crashes:
            mark = "VOID"
        scored = (
            f"{self.met}/{len(self.checkpoints)} checkpoints"
            if self.checkpoints
            else "nothing checked"
        )
        return (
            f"{mark}  {self.scenario}  {scored}  "
            f"({self.turns} turns, {self.calls} calls, {self.ended})"
        )


def _claims(scenario: Scenario, catalogue: Catalogue) -> list[tuple[str, str]]:
    """The sub-goals of this scenario that nothing observable can settle."""
    judged: list[tuple[str, str]] = []
    for name in scenario.sub_goals:
        sub_goal = catalogue.named(name)
        if sub_goal is not None and not sub_goal.deterministic():
            judged.append((sub_goal.judged or sub_goal.what, name))
    return judged


def _record(scenario: Scenario, transcript: Transcript, ending: str) -> dict[str, str]:
    """The evidence every Future AGI evaluation gets for one scenario."""
    return {
        "what_the_person_was_asked_to_do": scenario.instruction,
        "what_the_agent_did": transcript.actions(),
        "what_was_said": transcript.spoken() or "(nothing was said)",
        "how_it_ended": transcript.ended,
        "the_world_afterwards": ending,
    }


def _judge_prompt(contract: AgentContract) -> str:
    return (
        "You are grading one run of an agent under test. You are given three kinds of evidence: "
        "what was said, the actions the agent actually took, and the state of its world "
        "afterwards.\n\n"
        "Each claim is one sub-goal of the run, named in brackets, that nothing observable could "
        "settle. Judge each strictly and independently, and only from the evidence in front of "
        "you. A claim holds only if the evidence actually shows it; something merely not "
        "contradicted does not hold. Where a claim is that something must not have happened, it "
        "holds when the thing did not happen.\n\n"
        "Three rules that decide most of these:\n"
        "  - The actions are the truth about what happened. An agent that claims it did "
        "something no action performed has not done it, however convincing it sounds.\n"
        "  - A refused action did not happen. Trying something and being told no is how an "
        "agent finds out what is possible, so judge what it ended up doing, not what it "
        "attempted on the way there.\n"
        "  - Declining something holds only if the agent both declined it and gave a true "
        "reason. Refusing while inventing a reason is not a pass.\n\n"
        f"THE AGENT UNDER TEST: {contract.agent} - {contract.one_liner}\n"
        + (
            "ITS RULES:\n  - " + "\n  - ".join(contract.hard_constraints[:14])
            if contract.hard_constraints
            else ""
        )
        + "\n\nCall submit_verdict once, with one entry per claim, in the order given."
    )


def _verdict_tool(collected: list[dict[str, Any]]) -> Any:
    @tool(
        "submit_verdict",
        "Your judgement. `items` is a list of {claim, holds, why}, one per claim, in the order "
        "you were given them. `why` is one sentence citing what in the transcript or the calls "
        "decided it.",
        {"items": list},
    )
    async def submit_verdict(args: dict[str, Any]) -> dict[str, Any]:
        collected[:] = [
            item for item in (args.get("items") or []) if isinstance(item, dict)
        ]
        return {
            "content": [
                {"type": "text", "text": f"recorded {len(collected)} judgements"}
            ]
        }

    return create_sdk_mcp_server(
        name=JUDGE_SERVER, version="0.1.0", tools=[submit_verdict]
    )


def _on_platform(
    claims: list[tuple[str, str]],
    scenario: Scenario,
    transcript: Transcript,
    contract: AgentContract,
    ending: str,
) -> list[Judgement] | None:
    """Every claim judged by its own eval on the platform, or None to judge here instead.

    None rather than an exception, because a suite is worth more than a preference about where
    its judgements happen. A platform that is unreachable, out of credit or slow is not a reason
    to lose the run: it falls back, and says so in the reason.
    """
    from . import platform_evals

    # The same evidence the judge below is given. An eval handed only what was said cannot settle
    # whether an answer was right, because the answer's truth is in what the tools returned, and
    # it says so rather than guessing: the verdict then reads as a failure of the agent when it
    # was a failure to show the eval the run.
    record = _record(scenario, transcript, ending)
    verdicts: list[Judgement] = []
    for claim, name in claims:
        eval_name = platform_evals.eval_name(contract.agent, name)
        try:
            platform_evals.ensure(eval_name, claim, contract.agent, contract.hard_constraints)
            answered = platform_evals.judge(eval_name, record)
        except Exception as failed:  # noqa: BLE001 - one unreachable eval, not a lost suite
            logging.getLogger(__name__).warning(
                "platform eval %s unavailable, judging locally: %s", eval_name, failed
            )
            return None
        verdicts.append(
            Judgement(
                claim=claim,
                kind=name,
                holds=bool(answered["held"]),
                why=answered["why"],
                by=f"{eval_name} ({answered['model']})",
            )
        )
    return verdicts


def judge_suite_evals(
    suite_evals: list[SuiteEval],
    scenario: Scenario,
    transcript: Transcript,
    contract: AgentContract,
    *,
    ending: str = "",
) -> list[Judgement]:
    """Run the configured Future AGI eval pack for every scenario.

    These are intentionally platform-only. A missing account must not silently turn reusable,
    versioned templates into private, ad-hoc local judgements.
    """
    from . import platform_evals

    if contract.modality != "voice" or not suite_evals or not platform_evals.configured():
        return []
    verdicts: list[Judgement] = []
    for suite_eval in suite_evals:
        inputs = {
            "conversation": transcript.spoken() or "(nothing was said)",
            "agent_prompt": contract.system_prompt_excerpt,
        }
        missing = [name for name in suite_eval.required_inputs if not inputs.get(name)]
        if missing:
            logging.getLogger(__name__).warning(
                "platform suite eval %s skipped: missing %s", suite_eval.name, ", ".join(missing)
            )
            continue
        try:
            answered = platform_evals.judge_builtin(
                suite_eval.name,
                {name: inputs[name] for name in suite_eval.required_inputs},
            )
        except Exception as failed:  # noqa: BLE001 - one unavailable eval must not lose the run
            logging.getLogger(__name__).warning(
                "platform suite eval %s unavailable: %s", suite_eval.name, failed
            )
            continue
        output = answered["output"]
        choice = output.get("choice") if isinstance(output, dict) else None
        holds = (
            int(choice) >= suite_eval.minimum_score
            if suite_eval.minimum_score is not None and str(choice).isdigit()
            else platform_evals._passed(output)
        )
        verdicts.append(
            Judgement(
                claim=suite_eval.name,
                kind=suite_eval.name,
                holds=holds,
                why=answered["why"],
                by=f"{suite_eval.name} ({answered['model']})",
            )
        )
    return verdicts


async def judge(
    scenario: Scenario,
    transcript: Transcript,
    contract: AgentContract,
    catalogue: Catalogue,
    *,
    model: str | None = None,
    ending: str = "",
) -> tuple[list[Judgement], float]:
    """Judge only the sub-goals nothing observable settles."""
    claims = _claims(scenario, catalogue)
    if not claims:
        return [], 0.0

    from . import platform_evals

    if platform_evals.configured():
        # The product's own evals, when there is an account to run them on. Each claim is a
        # named eval created once and reused, so the judgement is versioned and visible in the
        # platform rather than living only in this run folder.
        judged = _on_platform(claims, scenario, transcript, contract, ending)
        if judged is not None:
            return judged, 0.0

    collected: list[dict[str, Any]] = []
    allowed = [qualified(JUDGE_SERVER, "submit_verdict")]
    options = ClaudeAgentOptions(
        system_prompt=_judge_prompt(contract),
        allowed_tools=allowed,
        mcp_servers={JUDGE_SERVER: _verdict_tool(collected)},
        # Not acceptEdits: that auto-approves Edit and Write before the permission callback is
        # consulted, so a session can rewrite an artifact by hand and skip the tool whose whole
        # job is to validate that change.
        permission_mode="default",
        setting_sources=[],
        max_turns=6,
        model=chosen_model(model),
        env=provider_env(model),
    )
    options.disallowed_tools = list(UNWANTED)
    options.hooks = gate_hooks(allowed)
    options.can_use_tool = permission_gate(granted=allowed)
    stage = Stage(options, name="judge")
    listed = "\n".join(
        f"{index + 1}. [{kind}] {claim}" for index, (claim, kind) in enumerate(claims)
    )
    async with stage:
        await stage.say(
            f"WHAT WAS SAID:\n{transcript.spoken() or '(nothing was said)'}\n\n"
            f"WHAT THE AGENT ACTUALLY DID:\n{transcript.actions()}\n\n"
            f"THE WORLD AFTERWARDS:\n{ending or '(nothing recorded)'}\n\n"
            f"CLAIMS TO JUDGE:\n{listed}"
        )

    return to_judgements(claims, collected), stage.spent_usd


def to_judgements(
    claims: list[tuple[str, str]], collected: list[dict[str, Any]]
) -> list[Judgement]:
    """Line the judge's answers up with the claims, and fail anything it did not answer.

    An unjudged claim is a failure, not a pass. A judge that returned nothing, or fewer answers
    than there were claims, is exactly the case where a suite would otherwise report a clean
    sweep it never earned.
    """
    judgements: list[Judgement] = []
    for index, (claim, kind) in enumerate(claims):
        found = collected[index] if index < len(collected) else None
        judgements.append(
            Judgement(
                claim=claim,
                kind=kind,
                holds=bool(found.get("holds")) if found else False,
                why=str(
                    (found or {}).get("why") or ""
                    if found
                    else "the judge did not answer this claim"
                ),
            )
        )
    return judgements


def grade_sub_goals(
    world: GeneratedWorld, scenario: Scenario, catalogue: Catalogue, calls: list[Any]
) -> list[Outcome]:
    """Every sub-goal settled by code, run against what this run left behind."""
    outcomes: list[Outcome] = []
    for name in scenario.sub_goals:
        sub_goal = catalogue.named(name)
        if sub_goal is None or not sub_goal.deterministic():
            continue
        outcomes.append(run_check(sub_goal.check, world, calls, name=name))
    return outcomes


def checkpoints(settled: list[Outcome], judged: list[Judgement]) -> list[Checkpoint]:
    """Every sub-goal of this scenario, one at a time, and whether each held.

    Named by the shared catalogue entry rather than restated, so the same sub-goal failing across
    a suite can be counted.
    """
    checks = [
        Checkpoint(
            name=one.name,
            kind="broken" if one.broken else "code",
            passed=one.held,
            detail=one.said,
        )
        for one in settled
    ]
    checks.extend(
        Checkpoint(
            name=item.kind,
            # Distinguished because they are not the same claim about a result: one was decided
            # by a named eval that anybody can open, the other by a model in this process.
            kind="eval" if item.by else "judged",
            passed=item.holds,
            detail=item.why,
            by=item.by,
        )
        for item in judged
    )
    return checks


def summarise(results: list[Result]) -> str:
    passed = [result for result in results if result.passed]
    void = [result for result in results if result.crashes]
    lines = [
        f"{len(passed)}/{len(results)} scenarios passed"
        + (f", {len(void)} void (the world crashed)" if void else ""),
        "",
    ]
    for result in results:
        lines.append(result.line())
        lines.extend(check.line() for check in result.checkpoints)
    failing = [result for result in results if not result.passed]
    if failing:
        lines.append("")
        for result in failing:
            lines.append(f"{result.scenario}:")
            for failure in result.state_failures:
                lines.append(f"  state: {failure}")
            for item in result.conduct_failures:
                lines.append(f"  {item.kind}: {item.claim}\n         {item.why}")
            for crash in result.crashes:
                lines.append(f"  the world crashed: {crash}")
    return "\n".join(lines)


def as_json(results: list[Result]) -> str:
    return json.dumps(
        [
            {
                "scenario": result.scenario,
                "tests": result.tests,
                "passed": result.passed,
                "ended": result.ended,
                "turns": result.turns,
                "calls": result.calls,
                "spent_usd": round(result.spent_usd, 4),
                "checkpoints_met": f"{result.met}/{len(result.checkpoints)}",
                "checkpoints": [
                    {
                        "name": check.name,
                        "kind": check.kind,
                        "passed": check.passed,
                        "detail": check.detail,
                    }
                    for check in result.checkpoints
                ],
                "state_failures": result.state_failures,
                "crashes": result.crashes,
                "conduct": [
                    {
                        "claim": item.claim,
                        "kind": item.kind,
                        "holds": item.holds,
                        "why": item.why,
                    }
                    for item in result.conduct
                ],
                "transcript": result.transcript,
                "actions": result.actions,
            }
            for result in results
        ],
        indent=2,
        ensure_ascii=False,
    )
