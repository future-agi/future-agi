"""Whether a generated world is usable, decided by exercising it.

Published work on synthesised environments is consistent about two things. Most generated
environments contain bugs, so the gate has to aim at the ones that block rather than at
perfection. And the bugs cluster: edge-case handling first, then state consistency across
several calls. A gate that runs each handler once and calls it done misses both clusters.

So this exercises every tool three ways, and then exercises the world as a sequence:

- **happy**: a valid call, built from the values the contract says the argument accepts
- **edge**: an identifier that does not exist, and a required argument left out
- **sequence**: a declared series of calls whose final state is asserted

The distinction that matters throughout is **refusal versus crash**. A tool that rejects a
nonexistent id is working: that refusal is the entire point of a real world. A tool that raises
``KeyError`` on the same input is broken. They are both failures to a naive check and opposite
outcomes here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..contract import AgentContract, ToolSpec
from .expectations import check_state
from .kinds import WorldKind, for_contract
from .kinds import resolve as _resolve_kind
from .runtime import GeneratedWorld

HAPPY = "happy"
EDGE = "edge"
SEQUENCE = "sequence"
COVERAGE = "coverage"
DATA = "data"

# A value no generated world should ever have seeded, used to prove a lookup refuses.
ABSENT = "__does_not_exist__"


@dataclass
class ProbeResult:
    name: str
    kind: str
    passed: bool
    detail: str = ""


@dataclass
class ProbeReport:
    results: list[ProbeResult] = field(default_factory=list)

    @property
    def score(self) -> float:
        return (
            sum(1 for result in self.results if result.passed) / len(self.results)
            if self.results
            else 0.0
        )

    @property
    def failures(self) -> list[ProbeResult]:
        return [result for result in self.results if not result.passed]

    def summary(self) -> str:
        if not self.results:
            return "no probes ran"
        lines = [
            f"{len(self.results) - len(self.failures)}/{len(self.results)} probes passed"
        ]
        for failure in self.failures:
            lines.append(f"  {failure.kind}:{failure.name}: {failure.detail}")
        return "\n".join(lines)


def _valid_arguments(tool: ToolSpec) -> dict[str, Any]:
    """A plausible call, using the values the contract says each argument accepts."""
    arguments: dict[str, Any] = {}
    for arg in tool.args:
        options = tool.arg_values.get(arg)
        if isinstance(options, (list, tuple)):
            usable = [value for value in options if value not in (None, "null", "")]
            if usable:
                arguments[arg] = usable[0]
                continue
        declared = tool.arg_types.get(arg, "")
        if "list" in declared:
            arguments[arg] = []
        elif "int" in declared:
            arguments[arg] = 1
        elif "bool" in declared:
            arguments[arg] = True
        else:
            arguments[arg] = ABSENT
    return arguments


def _is_a_real_identifier(value: Any) -> bool:
    """Whether a permitted value names a record, rather than being an enum like 'M' or 'null'."""
    if not isinstance(value, str) or value in ("", "null", "none", "None"):
        return False
    return len(value) > 2 and not value.isdigit()


def _missing_catalogue(
    world: GeneratedWorld, contract: AgentContract, kind: WorldKind
) -> list[str]:
    """Identifiers the contract says a tool accepts that are nowhere in the seeded world.

    The gap this closes is a whole category left unseeded. Every call naming a sauce then fails,
    which looks from the outside exactly like a world being correctly strict, and a suite where
    nothing can be ordered scores perfectly. Whether the catalogue is complete cannot be settled
    by behaviour, so it is checked against the data.
    """
    present = kind.values_present(world)
    missing: list[str] = []
    for tool in contract.tools:
        for arg, values in (tool.arg_values or {}).items():
            if not isinstance(values, (list, tuple)):
                continue
            if not _looks_like_an_identifier(arg, tool.arg_types.get(arg, "")):
                continue
            absent = [
                value
                for value in values
                if _is_a_real_identifier(value) and value not in present
            ]
            if absent:
                shown = ", ".join(absent[:4]) + (
                    f" and {len(absent) - 4} more" if len(absent) > 4 else ""
                )
                missing.append(f"{tool.name}.{arg}: {shown}")
    return missing


def _missing_argument(error: str) -> bool:
    """Whether a failure is the language rejecting a call for want of a required argument."""
    said = (error or "").lower()
    return "typeerror" in said and "argument" in said and (
        "missing" in said or "required" in said or "unexpected keyword" in said
    )


def _reads_argument(source: str, name: str) -> bool:
    """Whether a handler actually takes this argument out of ``args``.

    Looking for the bare name is not enough. A handler that reads ``args['order_ids']`` and then
    loops ``for order_id in order_ids`` mentions ``order_id`` all over itself while never reading
    the argument the tool is given, so it silently ignores its input and reports success or
    refuses everything. Both look fine from the outside, which is why this is checked at the
    point of access rather than by behaviour.
    """
    pattern = (
        rf"args\s*(?:\[\s*|\.get\s*\(\s*|\.pop\s*\(\s*)"
        rf"['\"]{re.escape(name)}['\"]"
    )
    return re.search(pattern, source) is not None


def _looks_like_an_identifier(name: str, _declared: str = "") -> bool:
    """Whether an argument names a record that has to exist for the call to make sense.

    Decided by the name alone. Treating every ``str`` argument as a catalogue was a trap: a
    ``size`` accepting "Medium" and "Large" then demanded rows called Medium and Large in the
    world, which can never be seeded sensibly. The only ways out were to invent nonsense rows or
    to edit the contract, so a check meant to catch a missing menu instead pushed towards
    corrupting the record of what the agent is.

    A missed catalogue is a check that does not fire. A false one is a stage with no legal move,
    which is much worse, so this stays narrow.
    """
    return (
        name.endswith(("_id", "_ids", "_key", "_ref", "_code", "_sku")) or name == "id"
    )


def _identifier_arguments(tool: ToolSpec) -> dict[str, Any] | None:
    """The same call with every identifier replaced by one that cannot exist.

    Deliberately not gated on the contract listing that argument's values. A contract that
    failed to record them is exactly the case where nobody has checked what this tool does with
    a bad id, so skipping the probe there drops it precisely where it is most needed.
    """
    arguments = _valid_arguments(tool)
    swapped = False
    for arg in tool.args:
        declared = tool.arg_types.get(arg, "")
        if not tool.arg_values.get(arg) and not _looks_like_an_identifier(
            arg, declared
        ):
            continue
        arguments[arg] = [ABSENT] if "list" in declared.lower() else ABSENT
        swapped = True
    return arguments if swapped else None


def probe(
    world: GeneratedWorld,
    contract: AgentContract,
    *,
    sequences: Iterable[Mapping[str, Any]] = (),
    kind: WorldKind | None = None,
) -> ProbeReport:
    """Exercise the world and report what it can and cannot do.

    ``sequences`` are declared by whoever built the world, because knowing that adding an item
    should make it appear in a listing is judgement about this agent, not something derivable
    from a schema.
    """
    report = ProbeReport()
    kind = kind or for_contract(contract)

    # Every probe runs from the same starting world. Probes mutate, so without reverting
    # between them each one inherits the debris of the last and a check expecting three rows
    # finds seven. That is a fault in the harness, not in the world being checked.
    baseline = world.checkpoint()

    for tool in contract.tools:
        if tool.name not in world.handlers:
            report.results.append(
                ProbeResult(tool.name, COVERAGE, False, "contract tool has no handler")
            )
    for name in world.handlers:
        if name not in contract.tool_names():
            report.results.append(
                ProbeResult(
                    name, COVERAGE, False, "handler for a tool the agent does not have"
                )
            )

    for gap in _missing_catalogue(world, contract, kind):
        report.results.append(
            ProbeResult(
                gap.split(":")[0],
                DATA,
                False,
                f"the contract accepts values the world does not have: {gap}",
            )
        )
    if not _missing_catalogue(world, contract, kind):
        report.results.append(
            ProbeResult("catalogue", DATA, True, "every permitted identifier exists")
        )

    for tool in contract.tools:
        if tool.name not in world.handlers:
            continue
        source = world.handlers[tool.name]
        # Reading the source only says anything about a handler written here. A tool bound to the
        # agent's own code has a handler that forwards every argument on, so it never names any of
        # them, and checking for the names would fail every adopted tool while telling nobody
        # anything. The names are the agent's own problem there, and its own code is what runs.
        if not contract.adoptable(tool.name):
            unread = [arg for arg in tool.args if not _reads_argument(source, arg)]
            report.results.append(
                ProbeResult(
                    tool.name,
                    COVERAGE,
                    not unread,
                    # A handler reading order_ids when the tool takes order_id refuses
                    # everything, which looks exactly like a handler correctly refusing a bad id.
                    # Behaviour alone cannot tell those apart, so the names are checked directly.
                    ""
                    if not unread
                    else f"never reads {', '.join(unread)}, which the contract says it takes",
                )
            )

        world.revert(baseline)
        call = world.call(tool.name, _valid_arguments(tool))
        # A refusal here is acceptable: the contract's first listed value may genuinely be
        # invalid in the seeded world. A crash never is.
        report.results.append(
            ProbeResult(
                tool.name,
                HAPPY,
                call.ok or call.refused,
                "" if call.ok or call.refused else call.error,
            )
        )

        bogus = _identifier_arguments(tool)
        if bogus is not None:
            world.revert(baseline)
            call = world.call(tool.name, bogus)
            report.results.append(
                ProbeResult(
                    tool.name,
                    EDGE,
                    call.refused,
                    ""
                    if call.refused
                    else (
                        "succeeded on an id that does not exist"
                        if call.ok
                        else f"crashed instead of refusing: {call.error}"
                    ),
                )
            )

        if tool.args:
            world.revert(baseline)
            missing = _valid_arguments(tool)
            missing.pop(tool.args[0], None)
            call = world.call(tool.name, missing)
            # A tool bound to the agent's own code is a function with real parameters, so leaving a
            # required one out is rejected by the language before the body runs. That is the call
            # being refused, not the world falling over, and counting it as a crash would fail
            # every adopted tool for behaving exactly as the agent's own runtime makes it behave.
            declined = call.refused or (
                contract.adoptable(tool.name) and _missing_argument(call.error)
            )
            report.results.append(
                ProbeResult(
                    f"{tool.name}:without-{tool.args[0]}",
                    EDGE,
                    declined,
                    ""
                    if declined
                    else (
                        "accepted a call with a required argument missing"
                        if call.ok
                        else f"crashed instead of refusing: {call.error}"
                    ),
                )
            )

    world.revert(baseline)
    unknown = world.call(ABSENT, {})
    report.results.append(
        ProbeResult(
            "unknown-tool",
            EDGE,
            unknown.refused,
            "" if unknown.refused else "an unknown tool did not refuse",
        )
    )

    for index, sequence in enumerate(sequences):
        world.revert(baseline)
        report.results.append(_run_sequence(world, sequence, index))

    # Leave the world as the builder left it, not as the last probe left it.
    world.revert(baseline)
    return report


def dirty_state(
    world: GeneratedWorld,
    sequences: Iterable[Mapping[str, Any]],
    kind: WorldKind | None = None,
) -> list[str]:
    """Tables a scenario writes to that already hold rows before anything has happened.

    A world is the state every scenario starts from, so an order table with rows in it means
    the builder's own testing was frozen into the base state. Every scenario then begins with
    somebody else's order already in the cart, and a count check that should read one reads
    seven. Which tables are transactional is not guessable from a schema, so it is worked out
    by running the declared sequences and seeing what moves.
    """
    kind = kind or _resolve_kind("sqlite")
    baseline = world.checkpoint()
    before = kind.mutable_state(world)
    touched: set[str] = set()
    for index, sequence in enumerate(sequences):
        world.revert(baseline)
        _run_sequence(world, sequence, index)
        for name, size in kind.mutable_state(world).items():
            if size != before.get(name, 0):
                touched.add(name)
    world.revert(baseline)
    return sorted(name for name in touched if before.get(name, 0) > 0)


def _run_sequence(
    world: GeneratedWorld, sequence: Mapping[str, Any], index: int
) -> ProbeResult:
    """Run a declared series of calls and check the state it leaves behind.

    This is the state-consistency check: the failure mode where each call works on its own and
    the world still forgets what the previous one did.
    """
    name = str(sequence.get("name") or f"sequence-{index}")
    calls: Sequence[Mapping[str, Any]] = sequence.get("calls") or ()
    for step in calls:
        call = world.call(str(step.get("tool", "")), step.get("arguments") or {})
        if step.get("expect") == "refusal":
            if not call.refused:
                return ProbeResult(
                    name, SEQUENCE, False, f"{call.name} should have refused"
                )
            continue
        if not call.ok:
            return ProbeResult(name, SEQUENCE, False, f"{call.name}: {call.error}")

    failures = check_state(world.state(), sequence.get("expect_state") or {})
    if failures:
        return ProbeResult(name, SEQUENCE, False, failures[0])
    return ProbeResult(name, SEQUENCE, True)
