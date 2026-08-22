"""Stage three: write the scenarios the agent will be tested with.

Reads the contract and the world that was built from it, and produces scenarios grounded in both.
The stage can look at the world and run calls against throwaway copies of it, which is what keeps
a scenario about a real record rather than a plausible-sounding one.

Like the other stages it stays open. A suite is usually right on the second look, and "make three
of these harder" is the next thing said rather than a regeneration from nothing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import ClaudeAgentOptions

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
from .tools import qualified

SKILL = "write-scenarios"


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


# How many writers run at once. Each is a model session with its own subprocess, and each gate
# restores its own copy of the world, so this is bounded by the machine rather than by the API.
AT_ONCE = 4


def shares(wanted: int, use_cases: list[str]) -> list[tuple[str, int]]:
    """How many scenarios each use case is asked to produce.

    Evenly, with the remainder going to the ones named first, because a contract lists its
    primary use cases before its marginal ones. A use case that turns out to have less in it
    than its share says returns fewer; nothing forces it to pad.
    """
    if not use_cases:
        return []
    if wanted <= len(use_cases):
        return [(case, 1) for case in use_cases[:wanted]]
    each, extra = divmod(wanted, len(use_cases))
    return [(case, each + (1 if i < extra else 0)) for i, case in enumerate(use_cases)]


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


def branch_opening(
    contract: AgentContract, use_case: str, wanted: int, callers: str = ""
) -> str:
    return (
        f"Write {wanted} scenarios for {contract.agent!r}, all of them within this one use case:\n\n"
        f"    {use_case}\n\n"
        "Write nothing outside it. Somebody else is covering the other use cases at the same "
        "time, so a scenario that strays is either a duplicate of theirs or a gap in yours.\n\n"
        "Every scenario carries this use case verbatim in `use_case`, and its own one-line "
        "`branch` saying what makes it different from the others you write here. Branches are "
        "where the variety lives: the ordinary path, the branch that cannot be completed, the "
        "rule under pressure, state that has to carry across turns, the same request against a "
        "differently seeded world.\n\n"
        "Look at the world first with inspect_world so every scenario names real records, and "
        "read the sub-goals already defined. Work out each solution with try_calls before you "
        "submit it. Submit each one with submit_scenario and then stop: do not save, and do not "
        "ask what to do next. Whoever asked for this collects the suite and writes it.\n\n"
        "Vary the caller across the scenarios you write. Everyone else is writing their own use "
        "case and cannot see yours, so a suite where every caller is professional and formal is "
        "what happens when each writer picks the safest value. Give different scenarios "
        "different personalities, communication styles and accents from the values offered, and "
        "let the caller suit the situation: somebody whose card was declined is not in the same "
        "mood as somebody booking a routine morning ride." + callers
    )


async def _write_one_use_case(
    contract: AgentContract,
    use_case: str,
    count: int,
    *,
    index: int = 0,
    destination: Path,
    on_event: Callable[..., Any] | None,
    ask: Callable[..., Any] | None,
) -> list[Scenario]:
    """One use case's share, written by its own session. Returns what it proved, unsaved."""
    server, kept = scenario_tools(
        contract,
        destination,
        destination,
        wanted=count,
        can_save=False,
        start_from=[],
    )
    progress.started(destination, use_case)

    def watch(event: Any) -> None:
        # Report as they land rather than at the end. A slice that proves its first scenario
        # four minutes in is the difference between a run that looks alive and one that does not.
        progress.kept(destination, use_case, len(kept))
        if on_event:
            on_event(event)
    allowed = [
        qualified(SCENARIO_SERVER, name) for name in TOOL_NAMES if name != "save_scenarios"
    ]
    options = ClaudeAgentOptions(
        system_prompt=(
            f"{load_skill(SKILL)}\n\n## This agent\n\n{contract.brief(with_data=True)}"
            f"\n\n## Its world\n\n{world_summary(destination)}"
            f"\n\n## Your slice\n\nYou are writing only the scenarios for: {use_case}"
        ),
        allowed_tools=allowed,
        mcp_servers={SCENARIO_SERVER: server},
        permission_mode="default",
        cwd=str(destination.parent if destination.parent.exists() else Path.cwd()),
        setting_sources=[],
        max_turns=turns_for(count),
        model=chosen_model(),
        env=provider_env(),
    )
    options.disallowed_tools = list(UNWANTED)
    options.hooks = gate_hooks(allowed)
    options.can_use_tool = permission_gate(ask, allowed)
    stage = Stage(options, name=f"{SKILL}:{use_case[:40]}")
    try:
        async with stage:
            await stage.say(
                branch_opening(contract, use_case, count, callers_for(index, count)),
                on_event=watch,
            )
    except Exception as broke:  # noqa: BLE001 - one slice failing must not lose the others
        progress.failed(destination, use_case, str(broke))
        if on_event:
            on_event({"type": "slice_failed", "use_case": use_case, "why": str(broke)[:300]})
        return list(kept)
    progress.finished(destination, use_case, len(kept))
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


async def write_in_parallel(
    contract: AgentContract,
    *,
    out: Path | None = None,
    wanted: int = 10,
    use_cases: list[str] | None = None,
    at_once: int = AT_ONCE,
    on_event: Callable[..., Any] | None = None,
    ask: Callable[..., Any] | None = None,
) -> list[Scenario]:
    """Write a suite with one session per use case, then save it once.

    Sequentially, a suite costs roughly three turns a scenario against one budget, which is why
    asking for forty stopped around twenty-five. Here each use case is written by its own
    session, so the wall clock is the slowest use case rather than the sum of all of them, and
    the turn budget is per slice rather than shared.

    Saving stays here, once, for a reason: ``save_scenarios`` regenerates the index and deletes
    any folder it does not know about, so letting the writers save would have each of them
    remove the others' work.
    """
    destination = out or artifact_dir(contract.agent)
    cases = [case for case in (use_cases or contract.real_use_cases) if case.strip()]
    if not cases:
        # Nothing to partition on. One writer, the ordinary path, rather than no scenarios.
        return await write(contract, out=destination, wanted=wanted, on_event=on_event, ask=ask)

    allocation = shares(wanted, cases)
    progress.planned(destination, allocation, at_once=at_once, asked=wanted)
    if on_event:
        on_event({"type": "planned", "slices": allocation, "at_once": at_once})

    limit = asyncio.Semaphore(max(1, at_once))

    async def guarded(use_case: str, count: int, index: int) -> list[Scenario]:
        async with limit:
            return await _write_one_use_case(
                contract,
                use_case,
                count,
                index=index,
                destination=destination,
                on_event=on_event,
                ask=ask,
            )

    written = await asyncio.gather(
        *(
            guarded(case, count, index)
            for index, (case, count) in enumerate(allocation)
        ),
        return_exceptions=False,
    )

    suite = merged([load_scenarios(destination), *written])
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
