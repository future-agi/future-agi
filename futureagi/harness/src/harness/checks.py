"""Running a check the harness wrote, and deciding what its answer means.

A check is Python because an environment can be a database, a filesystem or a page, and any
little assertion language invented here would fit only the first. It is given the two things a
run leaves behind and returns a sentence when something is wrong:

    def check(world, calls):
        rows = world.state()["orders"]
        if len(rows) != 1:
            return f"{len(rows)} orders, expected 1"
        if not any(c.name == "order_combo_meal" for c in calls):
            return "the combo was never ordered"
        return None

``world`` is the environment afterwards. ``calls`` is every tool call that was made, each with
its arguments and whether it succeeded — so a check can insist not only that a call happened but
that it happened with the right arguments, which is the difference between booking 11 PM and
booking 10 PM.

A check that raises is a broken check, not a failed one, and is reported that way. Confusing the
two would let a typo read as a finding about the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .world.runtime import Call, GeneratedWorld


@dataclass
class Outcome:
    """What one check said."""

    name: str
    held: bool
    said: str = ""
    broken: bool = False

    def line(self) -> str:
        mark = "!" if self.broken else ("x" if self.held else " ")
        return f"  [{mark}] {self.name}" + (f" — {self.said}" if self.said else "")


def run_check(
    source: str, world: GeneratedWorld, calls: Sequence[Call], *, name: str = "check"
) -> Outcome:
    """Execute one check against what the run left behind."""
    namespace: dict[str, Any] = {}
    try:
        exec(compile(source, f"<check:{name}>", "exec"), namespace)
    except Exception as failed:
        return Outcome(name, False, f"the check would not compile: {failed}", broken=True)

    checker = namespace.get("check")
    if not callable(checker):
        return Outcome(name, False, "the check defines no check(world, calls)", broken=True)

    try:
        said = checker(world, list(calls))
    except Exception as failed:
        # The check is at fault, not the agent. A KeyError in an assertion is our bug, and
        # scoring it against the agent is how a harness invents findings.
        return Outcome(
            name, False, f"the check raised {type(failed).__name__}: {failed}", broken=True
        )

    if said is None or said is True:
        return Outcome(name, True)
    return Outcome(name, False, str(said) if said is not True else "")


def all_held(outcomes: Sequence[Outcome]) -> bool:
    return all(one.held for one in outcomes) and not any(one.broken for one in outcomes)


def broken(outcomes: Sequence[Outcome]) -> list[Outcome]:
    return [one for one in outcomes if one.broken]

def run_world_check(source: str, world: GeneratedWorld, *, name: str = "check") -> Outcome:
    """Execute one check about the world itself, rather than about a run.

    A world check asks whether the environment is usable at all, so it is written ``check(world)``
    and there are no calls to give it. Both arities are accepted, because the difference is not
    worth a rejection: a check written ``check(world, calls)`` out of habit is answering the same
    question, and gets an empty list.
    """
    import inspect

    namespace: dict[str, Any] = {}
    try:
        exec(compile(source, f"<world-check:{name}>", "exec"), namespace)
    except Exception as failed:
        return Outcome(name, False, f"the check would not compile: {failed}", broken=True)

    checker = namespace.get("check")
    if not callable(checker):
        return Outcome(name, False, "the check defines no check(world)", broken=True)

    try:
        wants = len(inspect.signature(checker).parameters)
    except (TypeError, ValueError):
        wants = 1
    try:
        said = checker(world) if wants < 2 else checker(world, [])
    except Exception as failed:
        return Outcome(
            name, False, f"the check raised {type(failed).__name__}: {failed}", broken=True
        )
    return Outcome(name, said is None, "" if said is None else str(said))
