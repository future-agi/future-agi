"""Stage three: write the scenarios the agent will be tested with.

Reads the contract and the world that was built from it, and produces scenarios grounded in both.
The stage can look at the world and run calls against throwaway copies of it, which is what keeps
a scenario about a real record rather than a plausible-sounding one.

Like the other stages it stays open. A suite is usually right on the second look, and "make three
of these harder" is the next thing said rather than a regeneration from nothing.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

from .config import (
    artifact_dir,
    UNWANTED,
    gate_hooks,
    chosen_model,
    load_skill,
    permission_gate,
    provider_env,
)
from .contract import AgentContract
from .scenario import Scenario
from . import progress
from .catalogue import load_catalogue
from .scenario_tools import (
    SCENARIO_SERVER,
    TOOL_NAMES,
    load_scenarios,
    scenario_tools,
    world_summary,
    write_scenarios,
)
from .session import Stage
from .tools import qualified, schema

SKILL = "write-scenarios"
REVIEW_SERVER = "suite_review"


# Turns a scenario costs in practice: look at the world, rehearse the calls, submit, and often
# one more to correct what a gate refused.
TURNS_EACH = 3
# Enough to write a handful without the budget being the thing that stops it.
TURNS_FLOOR = 120


def turns_for(wanted: int) -> int:
    """A turn budget that grows with the suite being asked for.

    A fixed ceiling is what made asking for a large suite pointless: generation stopped partway
    through, and `save_scenarios` refuses a count that does not match what was asked for, so a run
    that asked for fifty and reached twenty-eight saved nothing at all. The budget has to follow
    the request, or the request cannot be honoured.
    """
    return max(TURNS_FLOOR, wanted * TURNS_EACH + 40)


def open_stage(
    contract: AgentContract,
    *,
    out: Path | None = None,
    wanted: int = 10,
    ask: Callable[..., Any] | None = None,
    max_turns: int = 0,
) -> tuple[Stage, Path]:
    """A live write-the-scenarios stage, and where it will write."""
    destination = out or artifact_dir(contract.agent)
    server, kept = scenario_tools(contract, destination, destination, wanted=wanted)
    allowed = [
        "AskUserQuestion",
        *(qualified(SCENARIO_SERVER, name) for name in TOOL_NAMES),
    ]
    options = ClaudeAgentOptions(
        system_prompt=(
            f"{load_skill(SKILL)}\n\n## This agent\n\n{contract.brief(with_data=True)}"
            f"\n\n## Its world\n\n{world_summary(destination)}"
            + (
                f"\n\nWrite {wanted} scenarios."
                if not kept
                else f"\n\n{len(kept)} scenarios already exist and are loaded: "
                + ", ".join(scenario.name for scenario in kept)
                + ". Submitting one under an existing name replaces it."
            )
        ),
        allowed_tools=allowed,
        mcp_servers={SCENARIO_SERVER: server},
        # Not acceptEdits: that auto-approves Edit and Write before the permission callback is
        # consulted, so a stage can rewrite an artifact by hand and skip the tool whose
        # whole job is to validate that change.
        permission_mode="default",
        cwd=str(destination.parent if destination.parent.exists() else Path.cwd()),
        setting_sources=[],
        max_turns=max_turns or turns_for(wanted),
        model=chosen_model(),
        env=provider_env(),
    )
    options.disallowed_tools = list(UNWANTED)
    options.hooks = gate_hooks(allowed)
    options.can_use_tool = permission_gate(ask, allowed)
    return Stage(options, name=SKILL), destination


def opening(contract: AgentContract, wanted: int = 10, existing: int = 0) -> str:
    if existing:
        return (
            f"There are already {existing} scenarios for {contract.agent!r}, and they are "
            "loaded. Say what you want changed, or add to them. Anything you submit under an "
            "existing name replaces it."
        )
    return (
        f"Write {wanted} scenarios for {contract.agent!r}.\n\n"
        "Look at the world first with inspect_world so every scenario names real records, and "
        "read the sub-goals already defined. Say briefly how you are splitting the suite across "
        "the agent's use cases, then write it with generate_suite in the same turn: it runs a "
        "writer per use case at the same time and saves what they prove, where writing this "
        "many one at a time would run out of turns before finishing. Cover the ordinary case, "
        "the request that has to be refused, the rule under pressure, and at least one where "
        "state has to carry across several turns."
    )


def load(destination: Path) -> list[Scenario]:
    """The scenarios written for this agent, if any have been."""
    return load_scenarios(Path(destination))


# What a suite costs, and what it is allowed to cost.
#
# Writers run as separate model sessions, so wall clock is roughly the number of scenarios
# divided by how many run at once. The two ceilings below exist for different reasons: one
# protects the machine, the other protects the person waiting. Asking for a thousand scenarios
# is a reasonable thing to want and an unreasonable thing to do in one go, so a large ask is
# served a batch at a time with the rest offered back.
AT_ONCE = 4
MOST_AT_ONCE = int(os.environ.get("HARNESS_WRITERS_AT_ONCE") or 8)
MOST_IN_ONE_GO = int(os.environ.get("HARNESS_SUITE_BATCH") or 50)

# How many times the suite is reviewed and topped up after the first pass. One is enough to
# catch a slice that came back short or a use case nobody covered; more turns it into a loop
# that keeps finding smaller things to say.
TOP_UP_ROUNDS = 1


@dataclass(frozen=True)
class Slice:
    """One writer's share of a suite: what to write, how much, and why it is worth writing."""

    use_case: str
    angle: str = ""
    count: int = 1
    why: str = ""

    def named(self) -> str:
        return f"{self.use_case} — {self.angle}" if self.angle else self.use_case


def even_slices(wanted: int, use_cases: list[str]) -> list[Slice]:
    """The fallback split, when nobody said how the work should be divided.

    Evenly, with the remainder going to the ones named first, because a contract lists its
    primary use cases before its marginal ones. It is a poor plan and it is meant to be: a use
    case with one real branch gets the same share as one with six, so the first pads and the
    second under-covers. It exists so a caller that supplies no plan still gets a suite.
    """
    if not use_cases:
        return []
    if wanted <= len(use_cases):
        return [Slice(use_case=case, count=1) for case in use_cases[:wanted]]
    each, extra = divmod(wanted, len(use_cases))
    return [
        Slice(use_case=case, count=each + (1 if i < extra else 0))
        for i, case in enumerate(use_cases)
    ]


def planned(wanted: int, use_cases: list[str], given: list[dict] | None) -> list[Slice]:
    """The split this suite will actually be written to.

    A plan supplied by the caller wins, because whoever is talking to the person has just read
    the contract and the world and knows which use cases have something in them. Sizing every
    use case identically is the thing that made suites pad in one place and under-cover in
    another, and the plan is the only part of the process that knows the difference.

    Anything the plan leaves out is filled in evenly, and anything it over-asks for is trimmed,
    so a plan can be rough without producing a suite nobody asked for.
    """
    if not given:
        return even_slices(wanted, use_cases)

    known = {case.strip().lower(): case for case in use_cases}
    slices: list[Slice] = []
    for one in given:
        if not isinstance(one, dict):
            continue
        case = str(one.get("use_case") or "").strip()
        if not case:
            continue
        # Match the contract's own wording where the plan paraphrased it, so a slice is filed
        # under a use case the coverage count recognises rather than a near-miss of one.
        case = known.get(case.lower(), case)
        try:
            count = max(1, int(one.get("count") or 1))
        except (TypeError, ValueError):
            count = 1
        slices.append(
            Slice(
                use_case=case,
                angle=str(one.get("angle") or "").strip(),
                count=count,
                why=str(one.get("why") or "").strip(),
            )
        )
    if not slices:
        return even_slices(wanted, use_cases)

    # Trim from the end rather than scaling everything down: the plan put its most valuable
    # slices first, and shaving one scenario off each is how a deliberate plan becomes an even
    # one again.
    total = sum(one.count for one in slices)
    while total > wanted and slices:
        last = slices[-1]
        if last.count > 1:
            slices[-1] = Slice(last.use_case, last.angle, last.count - 1, last.why)
        else:
            slices.pop()
        total = sum(one.count for one in slices)
    return slices


def callers_for(index: int, wanted: int) -> str:
    """Which callers this slice should write, so the suite varies across slices as well as within.

    Instruction alone cannot do this. Each writer is blind to the others, so each independently
    picks the safest value and the suite converges on it: measured across three suites, more
    than half the callers came out "Professional and formal" and over three quarters American,
    with nobody doing anything wrong. Worse, a slice writing a single scenario has nothing to
    vary at all.

    So the spread is dealt out here, the same way the work is. Each slice is handed a different
    starting point in the platform's own vocabularies and told to begin there. It is a
    suggestion rather than a rule, because the caller still has to suit the scenario: a stolen
    phone is not a cheerful call whatever this hands out.
    """
    from .persona_guides import offered

    people = offered("personality")
    accents = offered("accent")
    if not people:
        return ""
    picks = [people[(index + step) % len(people)] for step in range(max(1, wanted))]
    accent = accents[index % len(accents)] if accents else ""
    said = (
        "\n\nStart from these callers, and move off them only where the scenario calls for "
        f"somebody else: {', '.join(picks)}."
    )
    if accent:
        said += (
            f" At least one of your callers has a {accent} accent. Other writers are covering "
            "other use cases with other callers, so a suite where everyone sounds the same is "
            "what happens when each of us picks the safest option."
        )
    return said


def brief_for(
    contract: AgentContract, mine: Slice, siblings: list[Slice], callers: str
) -> str:
    """What one writer is told: its share, what everyone else holds, and the bar.

    Written as a brief rather than a template because a writer that cannot see its siblings
    will otherwise write what they are writing. Naming their angles is cheaper than discovering
    the overlap at the merge and throwing the loser away.
    """
    others = "\n".join(f"  - {one.named()}" for one in siblings if one is not mine)
    aim = f"    {mine.use_case}"
    if mine.angle:
        aim += f"\n    Angle: {mine.angle}"
    if mine.why:
        aim += f"\n    Worth testing because: {mine.why}"

    return (
        f"Write {mine.count} scenario{'s' if mine.count != 1 else ''} for {contract.agent!r}, "
        "all of them within this one slice:\n\n"
        f"{aim}\n\n"
        + (
            "The rest of the suite is being written at the same time by others, covering:\n"
            f"{others}\n\nStay out of theirs. A scenario that strays is either a duplicate of "
            "somebody else's or a gap in yours.\n\n"
            if others
            else ""
        )
        + "Every scenario carries this use case verbatim in `use_case`, and its own one-line "
        "`branch` saying what makes it different from the others you write here. Branches are "
        "where the variety lives: the ordinary path, the branch that cannot be completed, the "
        "rule under pressure, state that has to carry across turns, the same request against a "
        "differently seeded world.\n\n"
        "What each one has to be, before you submit it:\n"
        "  - every value real, read out of the world with inspect_world, never invented\n"
        "  - an instruction that is a circumstance the person is living through, not a script "
        "of lines to say\n"
        "  - a setup that makes true whatever the instruction presumes, and a ready check that "
        "proves it\n"
        "  - a solution worked out with try_calls first, so the gates are not where you find "
        "out it cannot be passed\n"
        "  - sub-goals named from the shared catalogue, and checks that assert the right call "
        "with the right arguments or the right end state, never that something merely happened\n"
        "  - a scenario a competent agent could plausibly fail. If any correct implementation "
        "passes it for free, it teaches nothing and is not worth the run\n\n"
        "Look at the world first, and read the sub-goals already defined. Submit each scenario "
        "with submit_scenario and then stop: do not save, and do not ask what to do next. "
        "Whoever asked for this collects the suite and writes it." + callers
    )


async def _write_slice(
    contract: AgentContract,
    mine: Slice,
    siblings: list[Slice],
    *,
    index: int,
    destination: Path,
    on_event: Callable[..., Any] | None,
    ask: Callable[..., Any] | None,
) -> list[Scenario]:
    """One slice, written by its own session. Returns what it proved, unsaved."""
    server, kept = scenario_tools(
        contract,
        destination,
        destination,
        wanted=mine.count,
        can_save=False,
        start_from=[],
    )
    progress.started(destination, mine.named())

    def watch(event: Any) -> None:
        # Report as they land rather than at the end. A slice that proves its first scenario
        # four minutes in is the difference between a run that looks alive and one that does not.
        progress.kept(destination, mine.named(), len(kept))
        if on_event:
            on_event(event)

    allowed = [
        qualified(SCENARIO_SERVER, name) for name in TOOL_NAMES if name != "save_scenarios"
    ]
    options = ClaudeAgentOptions(
        system_prompt=(
            f"{load_skill(SKILL)}\n\n## This agent\n\n{contract.brief(with_data=True)}"
            f"\n\n## Its world\n\n{world_summary(destination)}"
            f"\n\n## Your slice\n\nYou are writing only: {mine.named()}"
        ),
        allowed_tools=allowed,
        mcp_servers={SCENARIO_SERVER: server},
        permission_mode="default",
        cwd=str(destination.parent if destination.parent.exists() else Path.cwd()),
        setting_sources=[],
        max_turns=turns_for(mine.count),
        model=chosen_model(),
        env=provider_env(),
    )
    options.disallowed_tools = list(UNWANTED)
    options.hooks = gate_hooks(allowed)
    options.can_use_tool = permission_gate(ask, allowed)
    stage = Stage(options, name=f"{SKILL}:{mine.named()[:40]}")
    try:
        async with stage:
            await stage.say(
                brief_for(contract, mine, siblings, callers_for(index, mine.count)),
                on_event=watch,
            )
    except Exception as broke:  # noqa: BLE001 - one slice failing must not lose the others
        progress.failed(destination, mine.named(), str(broke))
        if on_event:
            on_event({"type": "slice_failed", "slice": mine.named(), "why": str(broke)[:300]})
        return list(kept)
    progress.finished(destination, mine.named(), len(kept))
    return list(kept)


def merged(written: list[list[Scenario]]) -> list[Scenario]:
    """One suite out of several writers, with the collisions they could not see removed.

    The writers run blind to each other, so two can land on the same folder name or on the same
    use case and branch. Both are dropped here rather than at save time, where the loser would
    silently overwrite the winner's folder.
    """
    suite: list[Scenario] = []
    names: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for batch in written:
        for one in batch:
            pair = ((one.use_case or "").strip().lower(), (one.branch or "").strip().lower())
            if one.name in names or (pair[0] and pair in pairs):
                continue
            names.add(one.name)
            if pair[0]:
                pairs.add(pair)
            suite.append(one)
    return suite


def _suite_summary(suite: list[Scenario]) -> str:
    """The whole suite as a reviewer needs to see it: what each row claims to test."""
    return "\n".join(
        f"  {one.name} | use case: {one.use_case} | branch: {one.branch} | tests: {one.tests}"
        for one in suite
    )


async def gaps_in(
    contract: AgentContract,
    suite: list[Scenario],
    *,
    destination: Path,
    wanted: int,
    ask: Callable[..., Any] | None = None,
) -> list[Slice]:
    """What the finished suite is missing, as slices that would fill it.

    Nobody looks at a suite written in parallel. Each writer sees its own slice and the merge
    only removes collisions, so a use case that came back one short, or an obvious branch that
    every writer assumed somebody else had, survives to the end and nobody notices. This is the
    one pass that reads the suite as a whole.
    """
    if not suite:
        return []
    found: list[Slice] = []

    @tool(
        "submit_gaps",
        "The gaps worth filling in this suite, as the slices that would fill them. Return "
        "nothing when the suite covers what it should: a suite that is finished is a real "
        "answer, and inventing work to report is worse than saying so.",
        schema(
            {
                "gaps": {
                    "type": "array",
                    "description": "One entry per gap. Empty when the suite is covering what "
                    "it should.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "use_case": {"type": "string"},
                            "angle": {
                                "type": "string",
                                "description": "The scenario that is missing, in one line.",
                            },
                            "why": {"type": "string"},
                        },
                        "required": ["use_case", "angle"],
                    },
                }
            },
            ["gaps"],
        ),
    )
    async def submit_gaps(args: dict[str, Any]) -> dict[str, Any]:
        for one in args.get("gaps") or []:
            if not isinstance(one, dict):
                continue
            case = str(one.get("use_case") or "").strip()
            if case:
                found.append(
                    Slice(
                        use_case=case,
                        angle=str(one.get("angle") or "").strip(),
                        count=1,
                        why=str(one.get("why") or "").strip(),
                    )
                )
        return {
            "content": [
                {"type": "text", "text": f"{len(found)} gap(s) recorded. Nothing else to do."}
            ]
        }

    server = create_sdk_mcp_server(name=REVIEW_SERVER, version="0.1.0", tools=[submit_gaps])
    allowed = [qualified(REVIEW_SERVER, "submit_gaps")]
    options = ClaudeAgentOptions(
        system_prompt=(
            "You are reviewing a suite of tests somebody else wrote for an AI agent, in "
            "parallel, each writer blind to the others. Your only job is to say what is "
            "missing.\n\n"
            "Look for: a use case of this agent that nothing covers; a use case covered only "
            "on its ordinary path, where the branch that cannot be completed or the rule under "
            "pressure is the interesting one; two rows that are the same test under different "
            "names, leaving the branch one of them claimed uncovered.\n\n"
            "Judge coverage of the agent, not of the plan. Do not ask for more of what is "
            "already well covered, and do not report a gap you cannot name a scenario for. "
            "A suite of the right size that covers what matters is finished, and saying so is "
            f"the useful answer.\n\n## This agent\n\n{contract.brief()}"
        ),
        allowed_tools=allowed,
        mcp_servers={REVIEW_SERVER: server},
        permission_mode="default",
        cwd=str(destination.parent if destination.parent.exists() else Path.cwd()),
        setting_sources=[],
        max_turns=8,
        model=chosen_model(),
        env=provider_env(),
    )
    options.disallowed_tools = list(UNWANTED)
    options.hooks = gate_hooks(allowed)
    options.can_use_tool = permission_gate(ask, allowed)
    stage = Stage(options, name=f"{SKILL}:review")
    try:
        async with stage:
            await stage.say(
                f"This suite has {len(suite)} scenarios against a target of {wanted}:\n\n"
                f"{_suite_summary(suite)}\n\n"
                "Say what it is missing, then submit_gaps. Submit an empty list if it is "
                "covering what it should."
            )
    except Exception:  # noqa: BLE001 - a review that fails leaves the suite as written
        return []
    return found


async def write_in_parallel(
    contract: AgentContract,
    *,
    out: Path | None = None,
    wanted: int = 10,
    use_cases: list[str] | None = None,
    slices: list[dict] | None = None,
    at_once: int = AT_ONCE,
    rounds: int = TOP_UP_ROUNDS,
    on_event: Callable[..., Any] | None = None,
    ask: Callable[..., Any] | None = None,
) -> list[Scenario]:
    """Write a suite with one session per slice, review it, fill what it missed, and save once.

    Sequentially, a suite costs roughly three turns a scenario against one budget, which is why
    asking for forty stopped around twenty-five. Here the work is split into slices that run at
    the same time, so the wall clock is the slowest slice rather than the sum of all of them.

    Saving stays here, once, for a reason: ``save_scenarios`` regenerates the index and deletes
    any folder it does not know about, so letting the writers save would have each of them
    remove the others' work.
    """
    destination = out or artifact_dir(contract.agent)
    cases = [case for case in (use_cases or contract.real_use_cases) if case.strip()]
    if not cases and not slices:
        # Nothing to partition on. One writer, the ordinary path, rather than no scenarios.
        return await write(contract, out=destination, wanted=wanted, on_event=on_event, ask=ask)

    at_once = max(1, min(at_once or AT_ONCE, MOST_AT_ONCE))
    allocation = planned(wanted, cases, slices)
    progress.planned(
        destination,
        [(one.named(), one.count) for one in allocation],
        at_once=at_once,
        asked=wanted,
    )
    if on_event:
        on_event(
            {
                "type": "planned",
                "slices": [(one.named(), one.count) for one in allocation],
                "at_once": at_once,
            }
        )

    limit = asyncio.Semaphore(at_once)

    async def guarded(mine: Slice, siblings: list[Slice], index: int) -> list[Scenario]:
        async with limit:
            return await _write_slice(
                contract,
                mine,
                siblings,
                index=index,
                destination=destination,
                on_event=on_event,
                ask=ask,
            )

    written = await asyncio.gather(
        *(guarded(one, allocation, index) for index, one in enumerate(allocation)),
        return_exceptions=False,
    )
    suite = merged([load_scenarios(destination), *written])

    # Read the whole thing and fill what nobody covered. Bounded, because a reviewer asked
    # twice will always find something smaller to say.
    for _ in range(max(0, rounds)):
        if len(suite) >= wanted:
            break
        missing = await gaps_in(
            contract, suite, destination=destination, wanted=wanted, ask=ask
        )
        missing = missing[: max(0, wanted - len(suite))]
        if not missing:
            break
        if on_event:
            on_event({"type": "topping_up", "slices": [one.named() for one in missing]})
        progress.planned(
            destination,
            [(one.named(), one.count) for one in [*allocation, *missing]],
            at_once=at_once,
            asked=wanted,
        )
        for one in [*allocation, *missing]:
            if one in allocation:
                progress.finished(destination, one.named(), one.count)
        more = await asyncio.gather(
            *(
                guarded(one, missing, len(allocation) + index)
                for index, one in enumerate(missing)
            ),
            return_exceptions=False,
        )
        before = len(suite)
        suite = merged([suite, *more])
        allocation = [*allocation, *missing]
        if len(suite) == before:
            break

    write_scenarios(suite, destination, load_catalogue(destination))
    progress.settled(destination, kept_total=len(suite))
    if on_event:
        on_event({"type": "saved", "kept": len(suite), "asked": wanted})
    return load(destination)


async def write(
    contract: AgentContract,
    *,
    out: Path | None = None,
    wanted: int = 10,
    follow_ups: list[str] | None = None,
    on_event: Callable[..., Any] | None = None,
    ask: Callable[..., Any] | None = None,
    max_turns: int = 0,
) -> list[Scenario]:
    """Run the stage start to finish. Returns whatever scenarios were saved."""
    stage, destination = open_stage(
        contract, out=out, wanted=wanted, ask=ask, max_turns=max_turns
    )
    async with stage:
        await stage.say(opening(contract, wanted), on_event=on_event)
        for follow_up in follow_ups or []:
            await stage.say(follow_up, on_event=on_event)
    return load(destination)
