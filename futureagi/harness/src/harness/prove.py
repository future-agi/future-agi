"""Proving a scenario is worth keeping, before anything is ever run against the agent.

Three gates, all pure code. No model is asked whether a scenario is good; the environment
decides. Terminal-bench keeps its tasks honest this way, and it is the cheapest useful thing in
the whole harness: no tokens, no network, a few milliseconds.

**Ready.** Reset the world, run the scenario's own ``setup.py``, then its ``ready.py``. The world
has to hold what the scenario presumes. A scenario about the last five chocolates is only a test
of the agent if there really are five; otherwise the agent fails for something we got wrong and
it reads as the agent's fault. This gate is why a missing precondition can never be mistaken for
a finding.

**Solvable.** Then run the reference solution and the checks. They must pass. If they do not,
either the scenario cannot be passed at all or its checks are wrong, and both have happened
here: one scenario asserted a value the agent was never permitted to send; another demanded
confirmation of an item that could not be ordered. Neither was noticed until a live run failed
and read as a finding about the agent.

**Not vacuous.** Then reset, set up again, run *nothing*, and run the checks. They must fail. A
check that passes with no actions taken grades nothing while reporting a result, which is how a
suite goes quietly green. This one earns its keep: on a third-party benchmark it caught three
sub-goals that passed trivially because the seeded world already contained a cancelled order.

Only a scenario that clears all three is kept. That is the green light.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .checks import Outcome, run_check
from .catalogue import Catalogue
from .folder import apply_setup, check_ready
from .scenario import Scenario
from .world.runtime import Call, GeneratedWorld
from .world.snapshot import restore


@dataclass
class Proof:
    """Whether a scenario holds up, and what happened when it was tried."""

    ready: bool = False
    solvable: bool = False
    vacuous: bool = True
    why_not_ready: str = ""
    # Checks that held with nothing done. The scenario is only vacuous when *every* check does
    # that, but a single one still grades nothing, and since sub-goals are shared it will report
    # itself as held for an agent that did nothing at all. Named rather than refused: on a
    # scenario about a refusal, "no order was placed" holding on an untouched world is correct.
    weak: list[str] = field(default_factory=list)
    with_solution: list[Outcome] = field(default_factory=list)
    with_nothing: list[Outcome] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)

    @property
    def holds(self) -> bool:
        return self.ready and self.solvable and not self.vacuous and not self.broken

    def gates(self) -> dict[str, bool]:
        """The three answers, for anything that wants to show them."""
        return {
            "ready": self.ready,
            "solvable": self.solvable,
            "not_vacuous": not self.vacuous,
        }

    def why(self) -> str:
        """What to fix, in the order worth fixing it."""
        if not self.ready:
            return (
                "the world is not ready for this scenario, so running it would test us rather "
                f"than the agent:\n  - {self.why_not_ready}\n\n"
                "Either setup.py does not make the change this scenario needs, or ready.py is "
                "checking for something the setup never creates."
            )
        if self.broken:
            return "these checks are broken, not failing:\n  - " + "\n  - ".join(
                self.broken
            )
        if not self.solvable:
            failed = [one for one in self.with_solution if not one.held]
            said = "\n  - ".join(f"{one.name}: {one.said}" for one in failed)
            refusals = (
                "\n\nThe solution's own calls were refused by the world:\n  - "
                + "\n  - ".join(self.refused)
                if self.refused
                else ""
            )
            return (
                "the reference solution does not pass this scenario's own checks, so either the "
                "scenario cannot be passed or the checks are wrong:\n  - " + said + refusals
            )
        if self.vacuous:
            passed = [one.name for one in self.with_nothing if one.held]
            return (
                "these checks pass without the agent doing anything, so they grade nothing:\n  - "
                + "\n  - ".join(passed)
                + "\n\nIf the point of this scenario is that nothing should happen, checking "
                "the world alone cannot show it — an untouched world looks identical to one "
                "where the agent did nothing at all. Check the calls instead: that the agent "
                "tried, and that the attempt was refused rather than succeeding.\n"
                "    def check(world, calls):\n"
                "        tried = [c for c in calls if c.name == 'add']\n"
                "        if not tried: return 'never attempted it'\n"
                "        if any(c.ok for c in tried): return 'it succeeded'\n"
                "        return None"
            )
        return "holds"


def _checks_for(scenario: Scenario, catalogue: Catalogue) -> list[tuple[str, str]]:
    """The deterministic checks this scenario is graded by, in catalogue order."""
    chosen: list[tuple[str, str]] = []
    for name in scenario.sub_goals:
        sub_goal = catalogue.named(name)
        if sub_goal is not None and sub_goal.deterministic():
            chosen.append((name, sub_goal.check))
    return chosen


def prepared(
    scenario: Scenario, world_root: Path
) -> tuple[GeneratedWorld, Outcome, Outcome]:
    """A fresh world with this scenario's setup applied, and how that went."""
    world = restore(world_root)
    world.reset()
    applied = apply_setup(scenario, world)
    ready = check_ready(scenario, world) if applied.ok else Outcome(False, applied.said)
    # The setup's own calls are not the agent's. Clearing them keeps a check that counts calls
    # from crediting the agent with work the scenario did on its behalf.
    world.calls = []
    return world, applied, ready


def _run(
    scenario: Scenario, world_root: Path, *, with_solution: bool
) -> tuple[GeneratedWorld, list[Call], list[str]]:
    """A world set up for this scenario, optionally with the solution played through it."""
    world, _applied, _ready = prepared(scenario, world_root)
    refused: list[str] = []
    if with_solution:
        for step in scenario.solution:
            call = world.call(step.tool, step.arguments)
            if not call.ok:
                refused.append(f"{call.name}({step.arguments}): {call.error}")
    return world, list(world.calls), refused


def prove(scenario: Scenario, catalogue: Catalogue, world_root: Path) -> Proof:
    """Run all three gates and say whether this scenario is worth keeping."""
    proof = Proof()
    checks = _checks_for(scenario, catalogue)
    if not checks:
        proof.broken = [
            "none of this scenario's sub-goals has a check in code, so nothing here can be "
            "settled without asking a model"
        ]
        return proof

    # Gate 1: is the world ready for this scenario at all?
    world, applied, ready = prepared(scenario, world_root)
    world.close()
    if not applied.ok:
        proof.why_not_ready = applied.said
        if applied.broken:
            proof.broken = [applied.said]
        return proof
    if not ready.ok:
        proof.why_not_ready = ready.said
        if ready.broken:
            proof.broken = [ready.said]
        return proof
    proof.ready = True

    # Gate 2: does the reference solution pass this scenario's own checks?
    world, calls, refused = _run(scenario, world_root, with_solution=True)
    try:
        proof.with_solution = [
            run_check(source, world, calls, name=name) for name, source in checks
        ]
    finally:
        world.close()
    proof.refused = refused
    proof.broken = [one.name for one in proof.with_solution if one.broken]
    proof.solvable = all(one.held for one in proof.with_solution) and not proof.broken

    # Gate 3: do those same checks fail when nothing is done?
    untouched, nothing, _ = _run(scenario, world_root, with_solution=False)
    try:
        proof.with_nothing = [
            run_check(source, untouched, nothing, name=name) for name, source in checks
        ]
    finally:
        untouched.close()
    # Vacuous only if *every* check still passes with nothing done. One check that survives an
    # empty run is often legitimate — "no order was placed" is a real thing to assert about a
    # refusal scenario — but a whole set of them means nothing is being graded.
    proof.weak = [one.name for one in proof.with_nothing if one.held]
    # A judged sub-goal reads what the agent said, and an agent that did nothing said nothing, so
    # it cannot be passed by an empty run the way a state check can. That matters for a whole
    # legitimate class of scenario: where the right behaviour is to decline and touch nothing,
    # every check about the world holds vacuously and the explanation is the only real evidence.
    # Without this the gate rejects exactly the scenarios that test a refusal.
    judged = [
        name
        for name in scenario.sub_goals
        if (found := catalogue.named(name)) is not None and not found.deterministic()
    ]
    proof.vacuous = (
        bool(proof.with_nothing)
        and len(proof.weak) == len(proof.with_nothing)
        and not judged
    )
    return proof
