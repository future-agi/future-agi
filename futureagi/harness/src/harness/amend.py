"""Changing the contract after the fact, and being honest about having done it.

The contract is what the agent verifiably is, read from its own source. That makes it the thing
everything downstream is confined to, and it is why the harness cannot invent a tool or a value.

But it is not permanent. Two situations genuinely require changing it, and they are different:

- **It was read wrong.** Stage one missed a value the agent really accepts. Correcting that is
  restoring the truth, and the correction should come from the source.
- **The agent is being changed.** Somebody adds an item to the world because the real menu is
  gaining one. The world and the action space have to move together: an item the world holds but
  the agent cannot name is dead data, and a scenario about it can only fail.

Either way the amendment is recorded on the contract itself rather than blended into what was
read, so that a month later it is still possible to tell what came from the agent and what came
from us. That distinction is the whole value of the contract; quietly widening it would make it
the same kind of guess it exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

from .contract import MODALITIES, AgentContract, validate_contract

CONTRACT = "contract.json"


def widen(
    contract: AgentContract,
    destination: Path,
    *,
    tool_name: str,
    argument: str,
    values: list[str],
    why: str,
) -> tuple[bool, str]:
    """Let a tool's argument accept values it did not before.

    Amends the contract the stage is holding, then persists it. Loading a second copy from disk
    and writing that back would leave the stage still working from the old one, so the world it
    goes on to check would be checked against an action space that no longer matches.

    Returns whether it was amended, and what happened.
    """
    spec = next((tool for tool in contract.tools if tool.name == tool_name), None)
    if spec is None:
        return False, (
            f"{tool_name!r} is not a tool this agent has. It has: "
            f"{', '.join(sorted(contract.tool_names()))}"
        )
    if argument not in spec.args:
        return False, (
            f"{tool_name} takes no argument called {argument!r}. It takes: "
            f"{', '.join(spec.args) or 'nothing'}"
        )
    if not why.strip():
        return (
            False,
            "say why: an unexplained amendment is indistinguishable from a guess",
        )

    existing = spec.arg_values.get(argument)
    current = list(existing) if isinstance(existing, (list, tuple)) else []
    fresh = [value for value in values if value and value not in current]
    if not fresh:
        return False, f"{argument} already accepts {', '.join(values) or 'nothing new'}"

    spec.arg_values[argument] = [*current, *fresh]
    contract.amendments.append(
        f"{tool_name}.{argument} widened by {', '.join(fresh)}: {why.strip()}"
    )

    problems = validate_contract(contract)
    if problems:
        spec.arg_values[argument] = current
        contract.amendments.pop()
        return False, "the amended contract would not be valid: " + "; ".join(problems)

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / CONTRACT).write_text(
        json.dumps(contract.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return True, (
        f"{tool_name}.{argument} now accepts {', '.join(fresh)}. "
        f"{len(contract.amendments)} amendment(s) recorded on the contract."
    )


def add_rule(
    contract: AgentContract, destination: Path, *, rule: str, why: str
) -> tuple[bool, str]:
    """Give the agent a rule its source did not state.

    A hard constraint is not decoration: the agent under test is told it, and the judge grades
    against it. So this is a real change to what is being tested, and like a widened argument it
    is recorded rather than blended into what was read from the source.
    """
    rule = rule.strip()
    if not rule:
        return False, "no rule given"
    if not why.strip():
        return False, "say why: an unexplained rule is indistinguishable from a guess"
    if any(rule.lower() == existing.lower() for existing in contract.hard_constraints):
        return False, f"the agent already has that rule: {rule}"

    contract.hard_constraints.append(rule)
    contract.amendments.append(f"rule added — {rule}: {why.strip()}")
    problems = validate_contract(contract)
    if problems:
        contract.hard_constraints.pop()
        contract.amendments.pop()
        return False, "the amended contract would not be valid: " + "; ".join(problems)

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / CONTRACT).write_text(
        json.dumps(contract.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return True, (
        f"added. The agent now has {len(contract.hard_constraints)} rules, and this one is "
        "graded from here on."
    )


def set_modality(
    contract: AgentContract, destination: Path, *, modality: str, why: str
) -> tuple[bool, str]:
    """Correct how a person actually reaches this agent.

    Worth its own amendment because modality is the one field that reroutes everything: it picks
    the world, the simulator and the transport, so a wrong value does not degrade a run, it runs
    a different test. And it is the field the source is least able to settle. An agent's code
    reads the same whether it is answering a chat window or a phone call, so a reader with no
    other evidence concludes whatever the repository looks like, which for a text benchmark is
    text, even when the person asking said they had deployed it to a phone number.

    Where the agent is deployed is a fact about somebody's setup rather than about the source, so
    when the two disagree the person is right and the source is describing a different runtime of
    the same agent. Recorded like every other amendment, because it is still a change to what was
    read.
    """
    named = (modality or "").strip().lower()
    if named not in MODALITIES:
        return False, f"{named!r} is not a modality. It is one of: {', '.join(MODALITIES)}"
    if not why.strip():
        return False, "say why: modality decides how every scenario is run"
    if named == contract.modality:
        return False, f"the contract already says {named}"

    was = contract.modality
    contract.modality = named
    contract.amendments.append(f"modality {was} -> {named}: {why.strip()}")
    problems = validate_contract(contract)
    if problems:
        contract.modality = was
        contract.amendments.pop()
        return False, "the amended contract would not be valid: " + "; ".join(problems)

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / CONTRACT).write_text(
        json.dumps(contract.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    reached = {
        "voice": "a call placed to the agent where it is hosted, its own tools answered over a "
        "webhook",
        "chat": "a typed conversation, the agent reconstructed here from its contract",
        "browser": "a browser the agent drives",
    }[named]
    return True, f"modality is now {named}. Every scenario will be run as {reached}."


def drop_rule(
    contract: AgentContract, destination: Path, *, rule: str, why: str
) -> tuple[bool, str]:
    """Take away a rule the agent does not really have.

    Stage one can misread a comment as a constraint, and a rule nobody has is worse than a
    missing one: the agent under test is told to obey it and the judge fails it for not doing
    something it was never supposed to do.
    """
    if not why.strip():
        return False, "say why: removing a rule changes what is being graded"
    match = next(
        (
            existing
            for existing in contract.hard_constraints
            if existing.lower() == rule.strip().lower()
        ),
        None,
    ) or next(
        (
            existing
            for existing in contract.hard_constraints
            if rule.strip().lower() in existing.lower()
        ),
        None,
    )
    if match is None:
        return False, (
            "no rule like that. It has:\n  - "
            + "\n  - ".join(contract.hard_constraints)
        )
    contract.hard_constraints.remove(match)
    contract.amendments.append(f"rule removed — {match}: {why.strip()}")
    _persist(contract, destination)
    return True, f"removed. {len(contract.hard_constraints)} rules left"


def fix_tool(
    contract: AgentContract,
    destination: Path,
    *,
    tool_name: str,
    why: str,
    args: list[str] | None = None,
    arg_types: dict[str, str] | None = None,
    description: str = "",
    remove: bool = False,
) -> tuple[bool, str]:
    """Correct a tool that was read wrong, or take away one the agent does not have.

    The most damaging thing stage one can get wrong. Every argument name flows into the world's
    handlers, the probes and the scenarios, so a tool recorded with the wrong argument produces
    a world that refuses everything and a suite that blames the agent for it.
    """
    if not why.strip():
        return False, "say why: this changes what everything downstream is built from"
    spec = next((tool for tool in contract.tools if tool.name == tool_name), None)
    if spec is None:
        return False, (
            f"{tool_name!r} is not a tool this agent has. It has: "
            f"{', '.join(sorted(contract.tool_names()))}"
        )

    if remove:
        contract.tools.remove(spec)
        contract.amendments.append(f"tool removed — {tool_name}: {why.strip()}")
        problems = validate_contract(contract)
        if problems:
            contract.tools.append(spec)
            contract.amendments.pop()
            return False, "cannot remove it: " + "; ".join(problems)
        _persist(contract, destination)
        return True, f"{tool_name} removed. {len(contract.tools)} tools left"

    changed = []
    if args is not None:
        # Values recorded against an argument that no longer exists would silently be lost, so
        # they are carried across by name and anything orphaned is said out loud.
        orphaned = sorted(set(spec.arg_values) - set(args))
        spec.args = list(args)
        spec.arg_types = {k: v for k, v in spec.arg_types.items() if k in spec.args}
        spec.arg_values = {k: v for k, v in spec.arg_values.items() if k in spec.args}
        changed.append(f"arguments are now {', '.join(args)}")
        if orphaned:
            changed.append(f"dropped values recorded for {', '.join(orphaned)}")
    if arg_types:
        unknown = sorted(set(arg_types) - set(spec.args))
        if unknown:
            return False, f"{tool_name} takes no argument called {', '.join(unknown)}"
        spec.arg_types.update(arg_types)
        changed.append("types updated")
    if description:
        spec.description = description
        changed.append("description updated")
    if not changed:
        return False, "nothing to change: give args, arg_types, description, or remove"

    contract.amendments.append(f"tool corrected — {tool_name}: {why.strip()}")
    problems = validate_contract(contract)
    if problems:
        return False, "the amended contract would not be valid: " + "; ".join(problems)
    _persist(contract, destination)
    return True, f"{tool_name}: {', '.join(changed)}"


def _persist(contract: AgentContract, destination: Path) -> None:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / CONTRACT).write_text(
        json.dumps(contract.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def not_offered(contract: AgentContract, candidates: dict[str, set[str]]) -> list[str]:
    """For each argument, the candidate values the contract does not let the agent send.

    ``candidates`` maps an argument name to identifiers found in the world that plausibly belong
    to it. Kept as an argument rather than inferred here, because which column feeds which
    argument is knowledge about one agent, not something a schema states.
    """
    missing: list[str] = []
    for tool in contract.tools:
        for argument, values in (tool.arg_values or {}).items():
            if argument not in candidates or not isinstance(values, (list, tuple)):
                continue
            permitted = {str(value) for value in values}
            absent = sorted(candidates[argument] - permitted)
            if absent:
                missing.append(f"{tool.name}.{argument}: {', '.join(absent)}")
    return missing


def unreachable(
    contract: AgentContract,
    destination: Path,
    *,
    tool_name: str,
    why: str,
) -> tuple[bool, str]:
    """Record that a tool's own implementation cannot be run here, and let one be written instead.

    The harness refuses to write a replacement for a tool the agent already implements, because a
    stand-in that looks right is worse than a tool we admit we could not run. But some
    implementations genuinely cannot be reached: they are built by a framework that needs a live
    client, or they live in a package this environment does not have. Without a way to say so, the
    build has no legitimate exit at all, and the only ways forward are to give up or to lie.

    So this is the exit, and it costs something: the reason is recorded on the contract, next to
    the tool, permanently. Anyone reading it afterwards can tell which tools ran the agent's own
    code and which were stand-ins, which is the distinction the refusal exists to protect.
    """
    spec = next((tool for tool in contract.tools if tool.name == tool_name), None)
    if spec is None:
        return False, (
            f"{tool_name!r} is not a tool this agent has. It has: "
            f"{', '.join(sorted(contract.tool_names()))}"
        )
    if not why.strip():
        return False, (
            "say why it cannot be reached. An unexplained stand-in is indistinguishable from not "
            "having tried, and this is the one record that it was a stand-in at all."
        )
    entry = contract.entry_for(tool_name)
    if entry is None or entry.mode == "generate":
        return False, (
            f"{tool_name} has no implementation recorded, so nothing is blocking a handler for "
            "it. Write one with define_handler."
        )

    was = entry.mode
    entry.mode = "generate"
    entry.notes = (f"{entry.notes} " if entry.notes else "") + f"unreachable here: {why.strip()}"
    contract.amendments.append(
        f"{tool_name} was recorded as {was} but could not be reached, so the world implements it: "
        f"{why.strip()}"
    )

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / CONTRACT).write_text(
        json.dumps(contract.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return True, (
        f"Recorded: {tool_name} could not be run from the agent's own code here. define_handler "
        "will now accept one for it, and the contract carries the reason so nobody later reads "
        "this as the agent's own tool having been tested."
    )
